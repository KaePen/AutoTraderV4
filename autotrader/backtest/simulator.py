"""トレードシミュレーター

バックテスト中のポジション管理・約定処理を行う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from autotrader.config import DEFAULT_TRADING_PARAMS
from autotrader.config.trading_params import get_preset
from autotrader.core.entities import Signal, Trade, Position, Candle
from autotrader.core.enums import SignalType, ExitReason, TradingStrategyMode
from autotrader.decision.unified.position_manager import (
    ManagementActionType,
    PositionManager,
    PositionManagerConfig,
)
from autotrader.backtest.position_event_logger import (
    PositionEventLogger,
    PositionEventType,
)


@dataclass
class SimulatorConfig:
    """シミュレーター設定

    Attributes:
        initial_balance: 初期残高
        spread_pips: スプレッド（pips）
        pip_value: 1pipの価値
        max_positions: 最大ポジション数（通常時）
        default_volume: デフォルトロット数
        slippage_pips: スリッページ（pips）
        commission_per_lot: ロット当たり手数料
        strategy_max_positions: 戦略別最大ポジション数
        pip_unit: 1pipの価格単位（JPY系=0.01、非JPY系=0.0001）
        bonus_max_positions: 高品質シグナル時に追加するポジション数（0=無効）
        bonus_score_threshold: bonus発動のconsensus_score閾値
    """

    initial_balance: float = 1_000_000.0
    spread_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.spread_pips
    )
    pip_value: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.pip_value
    )
    max_positions: int = 1
    default_volume: float = 0.1
    slippage_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.slippage_pips
    )
    # 1pipの価格単位（JPY系=0.01、非JPY系=0.0001）
    pip_unit: float = 0.01
    commission_per_lot: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.commission_per_lot
    )
    strategy_max_positions: dict[str, int] = field(default_factory=dict)
    # 品質ベース動的ポジション枠
    # consensus_score が bonus_score_threshold 以上の時のみ追加枠を解放
    bonus_max_positions: int = 0
    bonus_score_threshold: float = 7.0
    # PositionManager統合（デフォルトOFF=ベースライン保持）
    use_position_manager: bool = False
    # PositionManagerConfig（外部から注入）
    pm_config: PositionManagerConfig | None = None
    # 動的ロットサイズ（signal.lotを使用、OFFならdefault_volume固定）
    use_dynamic_lot: bool = False
    # セッション別スプレッド（デフォルトOFF=固定スプレッド）
    use_session_spread: bool = False
    session_spreads: dict[str, float] = field(
        default_factory=lambda: {
            "tokyo": 1.2,
            "london": 1.0,
            "london_ny_overlap": 0.8,
            "new_york": 1.2,
            "off_hours": 2.5,
        }
    )
    # UnifiedBotConfig（TradingPlan生成用）
    bot_config: Any = None

    @classmethod
    def from_preset(
        cls,
        symbol: str,
        *,
        initial_balance: float = 1_000_000.0,
        default_volume: float = 0.1,
    ) -> SimulatorConfig:
        """SymbolPreset からシミュレーター設定を生成.

        Args:
            symbol: 通貨ペア名（例: "USDJPY"）
            initial_balance: 初期残高
            default_volume: デフォルトロット数

        Returns:
            SimulatorConfig: プリセットベースの設定
        """
        p = get_preset(symbol)
        pip_unit = 0.01 if "JPY" in symbol else 0.0001
        return cls(
            initial_balance=initial_balance,
            spread_pips=p.spread_pips,
            pip_value=p.pip_value,
            max_positions=p.max_positions,
            default_volume=default_volume,
            slippage_pips=p.slippage_pips,
            pip_unit=pip_unit,
            commission_per_lot=p.commission_per_lot,
            bonus_max_positions=p.bonus_max_positions,
            bonus_score_threshold=p.bonus_score_threshold,
            use_position_manager=p.use_position_manager,
            pm_config=p.to_pm_config() if p.use_position_manager else None,
        )


@dataclass
class SimulatorState:
    """シミュレーター状態

    Attributes:
        balance: 現在残高
        equity: 評価額
        open_positions: オープンポジションリスト
        closed_trades: クローズ済みトレードリスト
        peak_equity: 最高評価額（DD計算用）
        current_drawdown: 現在のドローダウン
        max_drawdown: 最大ドローダウン
        daily_pnl: 日次損益
        positions_by_strategy: 戦略別オープンポジション
    """

    balance: float = 0.0
    equity: float = 0.0
    open_positions: list[Position] = field(default_factory=list)
    closed_trades: list[Trade] = field(default_factory=list)
    peak_equity: float = 0.0
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    daily_pnl: dict[str, float] = field(default_factory=dict)
    positions_by_strategy: dict[str, list[Position]] = field(
        default_factory=dict
    )


class TradeSimulator:
    """トレードシミュレーター

    バックテスト中のポジション管理・約定処理を行う。
    SL/TP判定、トレーリングストップ、強制決済等を処理。

    Attributes:
        config: シミュレーター設定
        state: シミュレーター状態
    """

    def __init__(self, config: SimulatorConfig | None = None) -> None:
        """初期化

        Args:
            config: シミュレーター設定
        """
        self.config = config or SimulatorConfig()
        self.state = SimulatorState(
            balance=self.config.initial_balance,
            equity=self.config.initial_balance,
            peak_equity=self.config.initial_balance,
        )
        # ホットパス用事前計算キャッシュ
        self._pip_unit = self.config.pip_unit
        self._spread_price = (
            self.config.spread_pips * self._pip_unit
        )
        self._half_spread = self._spread_price / 2
        self._slippage_price = (
            self.config.slippage_pips * self._pip_unit
        )
        self._pip_value = self.config.pip_value
        # bonus_max_positions が有効な場合は単一ポジション高速パスを無効化
        self._single_position = (
            self.config.max_positions == 1
            and self.config.bonus_max_positions == 0
        )
        # 日次PnL: 前回記録日キャッシュ（strftime回避）
        self._last_pnl_date: int = -1
        # PositionManager統合
        self._use_pm = self.config.use_position_manager
        self._pm: PositionManager | None = None
        if self._use_pm:
            pm_cfg = self.config.pm_config or PositionManagerConfig(
                spread_pips=self.config.spread_pips,
                slippage_pips=self.config.slippage_pips,
            )
            self._pm = PositionManager(pm_cfg)
        # UnifiedBotConfig（TradingPlan生成用）
        self._bot_config = self.config.bot_config
        # シグナル参照保持（PositionManager用）
        self._signal_cache: dict[str, Signal] = {}
        # MFE/MAE追跡（pips単位）
        self._mfe_mae: dict[str, dict[str, float]] = {}
        # エントリー時メトリクス保存
        self._entry_metrics: dict[str, dict[str, float]] = {}
        # Exit時メトリクス保存
        self._exit_metrics: dict[str, dict[str, float]] = {}
        # 連敗カウンター
        self._consecutive_losses: int = 0
        # 処理中candle用スプレッド
        self._current_candle_spread: float = 0.0
        # ポジションイベントロガー
        self._pos_event_logger: PositionEventLogger | None = None
        # セッション別スプレッド
        self._use_session_spread = self.config.use_session_spread
        if self._use_session_spread:
            # 時間帯→スプレッド(pips)の24要素テーブル
            spreads = self.config.session_spreads
            self._hourly_spread: list[float] = [
                spreads.get("off_hours", 2.5)
            ] * 24
            # 東京: 0-6 UTC
            for h in range(0, 7):
                self._hourly_spread[h] = spreads.get("tokyo", 1.2)
            # ロンドン: 7-12 UTC
            for h in range(7, 13):
                self._hourly_spread[h] = spreads.get("london", 1.0)
            # ロンドン-NY重複: 13-17 UTC
            for h in range(13, 18):
                self._hourly_spread[h] = spreads.get(
                    "london_ny_overlap", 0.8,
                )
            # NY: 18-22 UTC
            for h in range(18, 23):
                self._hourly_spread[h] = spreads.get(
                    "new_york", 1.2,
                )
            # 23 UTC: off_hours（デフォルト）

    def set_position_event_logger(
        self, logger: PositionEventLogger,
    ) -> None:
        """ポジションイベントロガーを設定

        Args:
            logger: PositionEventLogger
        """
        self._pos_event_logger = logger

    @property
    def position_event_logger(
        self,
    ) -> PositionEventLogger | None:
        """ポジションイベントロガーを取得"""
        return self._pos_event_logger

    def reset(self) -> None:
        """状態をリセット"""
        self.state = SimulatorState(
            balance=self.config.initial_balance,
            equity=self.config.initial_balance,
            peak_equity=self.config.initial_balance,
        )
        self._last_pnl_date = -1
        if self._pm:
            self._pm.reset()
        self._signal_cache.clear()
        self._mfe_mae.clear()
        self._entry_metrics.clear()
        self._exit_metrics.clear()
        self._consecutive_losses = 0
        if self._pos_event_logger:
            self._pos_event_logger.reset()

    def process_candle(
        self,
        candle: Candle,
        signal: Signal | None = None,
    ) -> list[Trade]:
        """足データを処理

        1. オープンポジションの評価額更新
        2. SL/TPチェック（PositionManager有効時は高度な決済ロジック）
        3. シグナルに基づくエントリー/決済

        Args:
            candle: 現在の足データ
            signal: トレードシグナル（任意）

        Returns:
            list[Trade]: この足で発生したトレード（決済含む）
        """
        state = self.state
        trades: list[Trade] = []
        sig_type = (
            signal.signal_type if signal else None
        )

        # 現在足スプレッド保存（exit_metrics用）
        self._current_candle_spread = (
            self._get_spread_for_candle(candle)
        )

        # MFE/MAE更新（決済判定前に実行し、最終足を含める）
        self._update_mfe_mae(candle)

        # 単一ポジション高速パス
        if self._single_position:
            open_pos = state.open_positions
            if open_pos:
                pos = open_pos[0]
                if self._use_pm:
                    close_result = self._check_exit_conditions_pm(
                        pos, candle, sig_type,
                    )
                else:
                    close_result = self._check_exit_conditions(
                        pos, candle,
                    )
                if close_result:
                    _fill, _reason, _trigger = close_result
                    trade = self._close_position(
                        pos, _fill, candle.time,
                        _reason, _trigger,
                    )
                    trades.append(trade)

            # 評価額更新
            self._update_equity(candle)

            if signal and signal.signal_type != SignalType.HOLD:
                # 反対シグナルなら決済（PM無効時のみ）
                if state.open_positions and not self._use_pm:
                    pos = state.open_positions[0]
                    if self._is_opposite_signal(pos, signal):
                        exit_price = self._get_exit_price(
                            pos.signal_type, candle,
                        )
                        trade = self._close_position(
                            pos, exit_price, candle.time,
                            ExitReason.SIGNAL_REVERSAL,
                            candle.close,
                        )
                        trades.append(trade)

                # 新規エントリー
                if not state.open_positions:
                    position = self._open_position(signal, candle)
                    if position:
                        state.open_positions.append(position)
        else:
            # 複数ポジション用汎用パス
            positions_to_close = []
            for position in state.open_positions:
                if self._use_pm:
                    close_result = self._check_exit_conditions_pm(
                        position, candle, sig_type,
                    )
                else:
                    close_result = self._check_exit_conditions(
                        position, candle,
                    )
                if close_result:
                    positions_to_close.append(
                        (
                            position,
                            close_result[0],
                            close_result[1],
                            close_result[2],
                        )
                    )

            for (
                position, exit_price,
                exit_reason, trigger_price,
            ) in positions_to_close:
                trade = self._close_position(
                    position, exit_price, candle.time,
                    exit_reason, trigger_price,
                )
                trades.append(trade)

            self._update_equity(candle)

            if signal and signal.signal_type != SignalType.HOLD:
                # 反対シグナルなら決済（PM無効時のみ）
                if not self._use_pm:
                    for position in list(state.open_positions):
                        if self._is_opposite_signal(position, signal):
                            exit_price = self._get_exit_price(
                                position.signal_type, candle,
                            )
                            trade = self._close_position(
                                position, exit_price, candle.time,
                                ExitReason.SIGNAL_REVERSAL,
                                candle.close,
                            )
                            trades.append(trade)

                # 品質ベース動的ポジション枠の計算
                _eff_max = self.config.max_positions
                if (
                    self.config.bonus_max_positions > 0
                    and signal.consensus_score is not None
                    and signal.consensus_score
                    >= self.config.bonus_score_threshold
                ):
                    _eff_max += self.config.bonus_max_positions
                if len(state.open_positions) < _eff_max:
                    position = self._open_position(signal, candle)
                    if position:
                        state.open_positions.append(position)

        # DD更新
        self._update_drawdown()

        # 日次損益記録（日付変更時のみ）
        self._record_daily_pnl(candle.time)

        return trades

    def _check_exit_conditions(
        self,
        position: Position,
        candle: Candle,
    ) -> tuple[float, ExitReason, float] | None:
        """決済条件をチェック（ギャップ約定対応）

        Args:
            position: ポジション
            candle: 現在の足データ

        Returns:
            tuple | None: (fill_price, reason, trigger_price)
        """
        sl = position.stop_loss
        tp = position.take_profit
        slip = self._slippage_price

        if position.signal_type == SignalType.BUY:
            if sl and candle.low <= sl:
                if candle.open < sl:
                    return (
                        candle.open - slip,
                        ExitReason.STOP_LOSS,
                        sl,
                    )
                return sl - slip, ExitReason.STOP_LOSS, sl
            if tp and candle.high >= tp:
                if candle.open > tp:
                    return (
                        candle.open - slip,
                        ExitReason.TAKE_PROFIT,
                        tp,
                    )
                return (
                    tp - slip, ExitReason.TAKE_PROFIT, tp,
                )
        else:
            if sl and candle.high >= sl:
                if candle.open > sl:
                    return (
                        candle.open + slip,
                        ExitReason.STOP_LOSS,
                        sl,
                    )
                return sl + slip, ExitReason.STOP_LOSS, sl
            if tp and candle.low <= tp:
                if candle.open < tp:
                    return (
                        candle.open + slip,
                        ExitReason.TAKE_PROFIT,
                        tp,
                    )
                return (
                    tp + slip, ExitReason.TAKE_PROFIT, tp,
                )

        return None

    def _check_exit_conditions_pm(
        self,
        position: Position,
        candle: Candle,
        current_signal: SignalType | None = None,
    ) -> tuple[float, ExitReason, float] | None:
        """PositionManager経由の決済条件チェック

        candle.closeベースでPMのevaluateを呼び出し、
        SL/TP/時間決済/反転/トレーリングを統合処理する。

        Args:
            position: ポジション
            candle: 現在の足データ
            current_signal: 現在のシグナル

        Returns:
            tuple | None: (fill_price, reason, trigger_price)
        """
        if self._pm is None:
            return None

        managed = self._pm.get_position(position.position_id)
        if managed is None:
            return None

        # ATR取得
        atr = 0.002  # デフォルト20pips
        cached = self._signal_cache.get(
            position.position_id,
        )
        if cached and cached.indicators_snapshot:
            atr = cached.indicators_snapshot.get(
                "atr_14", atr,
            )

        action = self._pm.evaluate(
            position_id=position.position_id,
            current_price=candle.close,
            current_time=candle.time,
            atr=atr,
            current_signal=current_signal,
        )

        if action.action_type == ManagementActionType.FULL_CLOSE:
            exit_price = self._calc_pm_fill_price(
                position.signal_type, candle,
                action.trigger_price, action.exit_reason,
            )
            return (
                exit_price,
                action.exit_reason,
                action.trigger_price,
            )

        if action.action_type == ManagementActionType.PARTIAL_CLOSE:
            _reason = (
                action.exit_reason
                or ExitReason.TAKE_PROFIT
            )
            fill = self._calc_pm_fill_price(
                position.signal_type, candle,
                action.trigger_price, _reason,
            )
            self._partial_close_position(
                position, candle, action.close_ratio,
                action.new_sl,
                exit_reason=_reason,
                exit_price_override=fill,
                trigger_price=action.trigger_price,
            )
            return None

        if action.action_type == ManagementActionType.UPDATE_SL:
            sl_before = position.stop_loss or 0.0
            # SimulatorのPosition.stop_lossをPMと同期
            # Position は frozen Pydantic → 置換
            if action.new_sl is not None:
                new_pos = position.model_copy(
                    update={"stop_loss": action.new_sl},
                )
                self.state.open_positions = [
                    new_pos
                    if p.position_id == position.position_id
                    else p
                    for p in self.state.open_positions
                ]
            if self._pos_event_logger and action.new_sl:
                self._pos_event_logger.log(
                    timestamp=candle.time,
                    position_id=position.position_id,
                    event_type=(
                        PositionEventType.TRAILING_UPDATE
                    ),
                    price=candle.close,
                    volume_before=position.volume,
                    volume_after=position.volume,
                    sl_before=sl_before,
                    sl_after=action.new_sl,
                    reason=action.reason,
                )

        return None

    def _partial_close_position(
        self,
        position: Position,
        candle: Candle,
        close_ratio: float,
        new_sl: float | None = None,
        exit_reason: ExitReason = ExitReason.TAKE_PROFIT,
        exit_price_override: float | None = None,
        trigger_price: float = 0.0,
    ) -> Trade | None:
        """部分決済処理

        Args:
            position: ポジション
            candle: 現在の足データ
            close_ratio: 決済比率（0.0-1.0）
            new_sl: 新しいSL価格
            exit_reason: 決済理由
            exit_price_override: 外部指定の決済価格
            trigger_price: トリガー価格（ログ用）

        Returns:
            Trade | None: 部分決済のトレード
        """
        close_volume = position.volume * close_ratio
        if close_volume <= 0:
            return None

        if exit_price_override is not None:
            exit_price = exit_price_override
        else:
            exit_price = self._get_exit_price(
                position.signal_type, candle,
            )

        # 損益計算（按分）
        if position.signal_type == SignalType.BUY:
            profit_pips = (exit_price - position.entry_price) * 100
        else:
            profit_pips = (position.entry_price - exit_price) * 100

        profit_loss = (
            profit_pips * self._pip_value * close_volume
        )
        commission = self.config.commission_per_lot * close_volume
        profit_loss -= commission

        # 残高更新
        self.state.balance += profit_loss

        # 部分決済トレード作成
        trade = Trade(
            trade_id=str(uuid4()),
            signal_id=position.signal_id,
            symbol=position.symbol,
            signal_type=position.signal_type,
            volume=close_volume,
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            profit_loss=profit_loss,
            profit_loss_pips=profit_pips,
            exit_reason=exit_reason,
            opened_at=position.opened_at,
            closed_at=candle.time,
            regime=position.regime,
            mode=position.mode,
            consensus_score=position.consensus_score,
            parent_trade_id=position.position_id,
            position_id=position.position_id,
        )
        self.state.closed_trades.append(trade)

        # Exit時メトリクス保存（trade_idベース）
        self._exit_metrics[trade.trade_id] = {
            "exit_spread": self._current_candle_spread,
            "equity_after": self.state.balance,
            "commission": commission,
            "slippage_pips": self.config.slippage_pips,
            "trigger_price": trigger_price,
            "fill_price": exit_price,
        }

        # ポジションイベントログ: PARTIAL_CLOSE
        if self._pos_event_logger:
            _evt_type = PositionEventType.PARTIAL_CLOSE_1R
            if exit_reason == ExitReason.TAKE_PROFIT_2R:
                _evt_type = (
                    PositionEventType.PARTIAL_CLOSE_2R
                )
            self._pos_event_logger.log(
                timestamp=candle.time,
                position_id=position.position_id,
                event_type=_evt_type,
                price=exit_price,
                volume_before=position.volume,
                volume_after=(
                    position.volume - close_volume
                ),
                sl_before=(
                    position.stop_loss or 0.0
                ),
                sl_after=new_sl or (
                    position.stop_loss or 0.0
                ),
                reason=exit_reason.value,
            )

        # 残りボリュームのポジション更新（frozen→置換）
        remaining_vol = position.volume - close_volume
        if remaining_vol > 0.001:
            new_pos = Position(
                position_id=position.position_id,
                symbol=position.symbol,
                signal_type=position.signal_type,
                volume=remaining_vol,
                entry_price=position.entry_price,
                stop_loss=new_sl or position.stop_loss,
                take_profit=position.take_profit,
                opened_at=position.opened_at,
                signal_id=position.signal_id,
                regime=position.regime,
                mode=position.mode,
                consensus_score=position.consensus_score,
            )
            # リスト内置換
            self.state.open_positions = [
                new_pos if p.position_id == position.position_id
                else p for p in self.state.open_positions
            ]
            # ManagedPositionのvolume更新
            if self._pm:
                managed = self._pm.get_position(position.position_id)
                if managed:
                    managed.remaining_volume = remaining_vol
        else:
            # 全量決済と同等
            self.state.open_positions = [
                p for p in self.state.open_positions
                if p.position_id != position.position_id
            ]
            if self._pm:
                self._pm.unregister_position(position.position_id)

        return trade

    def _open_position(
        self,
        signal: Signal,
        candle: Candle,
        strategy_id: str | None = None,
    ) -> Position | None:
        """ポジションをオープン

        Args:
            signal: トレードシグナル
            candle: 現在の足データ
            strategy_id: 戦略ID（戦略別追跡用）

        Returns:
            Position | None: 作成されたポジション
        """
        entry_price = self._get_entry_price(signal.signal_type, candle)
        # 動的サイジングONかつシグナルにlot指定あり→使用
        volume = (
            signal.lot
            if (
                self.config.use_dynamic_lot
                and signal.lot is not None
                and signal.lot > 0
            )
            else self.config.default_volume
        )

        # 証拠金チェック（簡易版）
        required_margin = entry_price * volume * 10000 / 25  # レバ25倍想定
        if required_margin > self.state.balance * 0.8:
            return None

        position = Position(
            position_id=str(uuid4()),
            symbol=signal.symbol,
            signal_type=signal.signal_type,
            volume=volume,
            entry_price=entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            opened_at=candle.time,
            signal_id=signal.signal_id,
            regime=signal.regime,
            mode=signal.mode,
            consensus_score=signal.consensus_score,
        )

        # 手数料差し引き
        commission = self.config.commission_per_lot * volume
        self.state.balance -= commission

        # 戦略別追跡
        if strategy_id:
            if strategy_id not in self.state.positions_by_strategy:
                self.state.positions_by_strategy[strategy_id] = []
            self.state.positions_by_strategy[strategy_id].append(position)

        # エントリー時メトリクス保存
        entry_spread = self._get_spread_for_candle(candle)
        # リスク率計算
        _risk_pct = 0.0
        if signal.stop_loss and entry_price > 0:
            _sl_pips = abs(
                entry_price - signal.stop_loss
            ) * 100
            _risk = (
                _sl_pips * self._pip_value * volume
            )
            if self.state.balance > 0:
                _risk_pct = _risk / self.state.balance * 100
        self._entry_metrics[position.position_id] = {
            "spread": entry_spread,
            "equity_before": self.state.balance,
            "dd_pct_at_entry": (
                self.state.current_drawdown * 100
            ),
            "consecutive_losses": float(
                self._consecutive_losses
            ),
            "risk_per_trade_pct": _risk_pct,
        }

        # PositionManager登録
        if self._pm and position.stop_loss and position.take_profit:
            # mode→TradingPlanを構築
            from autotrader.decision.unified.mode_selector import (
                TradingPlan,
            )
            import dataclasses as _dc
            plan = TradingPlan.create_universal(
                self._bot_config,
            )
            plan = _dc.replace(
                plan,
                regime=signal.regime,
            )
            self._pm.register_position(
                position_id=position.position_id,
                direction=position.signal_type,
                entry_price=entry_price,
                entry_time=candle.time,
                sl=position.stop_loss,
                tp=position.take_profit,
                volume=volume,
                plan=plan,
            )
            # シグナル参照をキャッシュ
            self._signal_cache[position.position_id] = signal

        # ポジションイベントログ: OPEN
        if self._pos_event_logger:
            self._pos_event_logger.log(
                timestamp=candle.time,
                position_id=position.position_id,
                event_type=PositionEventType.OPEN,
                price=entry_price,
                volume_before=0.0,
                volume_after=volume,
                sl_before=0.0,
                sl_after=(
                    position.stop_loss or 0.0
                ),
                reason="新規エントリー",
            )

        return position

    def _close_position(
        self,
        position: Position,
        exit_price: float,
        exit_time: datetime,
        exit_reason: ExitReason,
        trigger_price: float = 0.0,
    ) -> Trade:
        """ポジションを決済

        Args:
            position: ポジション
            exit_price: 決済価格
            exit_time: 決済時刻
            exit_reason: 決済理由

        Returns:
            Trade: クローズされたトレード
        """
        # 損益計算
        if position.signal_type == SignalType.BUY:
            profit_pips = (exit_price - position.entry_price) * 100
        else:
            profit_pips = (position.entry_price - exit_price) * 100

        profit_loss = profit_pips * self._pip_value * position.volume

        # BREAKEVEN は損益0扱い
        if exit_reason == ExitReason.BREAKEVEN:
            profit_loss = 0.0
            profit_pips = 0.0

        # 手数料差し引き
        commission = self.config.commission_per_lot * position.volume
        profit_loss -= commission

        # 残高更新
        self.state.balance += profit_loss

        # トレード作成
        trade = Trade(
            trade_id=str(uuid4()),
            signal_id=position.signal_id,
            symbol=position.symbol,
            signal_type=position.signal_type,
            volume=position.volume,
            entry_price=position.entry_price,
            exit_price=exit_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            profit_loss=profit_loss,
            profit_loss_pips=profit_pips,
            exit_reason=exit_reason,
            opened_at=position.opened_at,
            closed_at=exit_time,
            regime=position.regime,
            mode=position.mode,
            consensus_score=position.consensus_score,
            position_id=position.position_id,
        )

        # ポジションリストから削除
        self.state.open_positions = [
            p for p in self.state.open_positions
            if p.position_id != position.position_id
        ]

        # 戦略別追跡からも削除
        for strat_id, positions in self.state.positions_by_strategy.items():
            self.state.positions_by_strategy[strat_id] = [
                p for p in positions
                if p.position_id != position.position_id
            ]

        # クローズ済みリストに追加
        self.state.closed_trades.append(trade)

        # PositionManager登録解除
        if self._pm:
            self._pm.unregister_position(position.position_id)
        self._signal_cache.pop(position.position_id, None)
        # MFE/MAE・メトリクスはreset時に一括クリア

        # Exit時メトリクス保存（trade_idベース）
        self._exit_metrics[trade.trade_id] = {
            "exit_spread": self._current_candle_spread,
            "equity_after": self.state.balance,
            "commission": commission,
            "slippage_pips": self.config.slippage_pips,
            "trigger_price": trigger_price,
            "fill_price": exit_price,
        }

        # 連敗カウンター更新
        if profit_loss > 0:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1

        # ポジションイベントログ: FULL_CLOSE
        if self._pos_event_logger:
            self._pos_event_logger.log(
                timestamp=exit_time,
                position_id=position.position_id,
                event_type=PositionEventType.FULL_CLOSE,
                price=exit_price,
                volume_before=position.volume,
                volume_after=0.0,
                sl_before=(
                    position.stop_loss or 0.0
                ),
                sl_after=0.0,
                reason=exit_reason.value,
            )

        return trade

    def _get_entry_price(
        self,
        signal_type: SignalType,
        candle: Candle,
    ) -> float:
        """エントリー価格を取得

        Args:
            signal_type: シグナル種別
            candle: 足データ

        Returns:
            float: エントリー価格（スプレッド・スリッページ込み）
        """
        spread = self._get_spread_for_candle(candle)
        half_spread = spread / 2
        if signal_type == SignalType.BUY:
            # 買い：Ask価格（Close + スプレッド半分 + スリッページ）
            return candle.close + half_spread + self._slippage_price
        else:
            # 売り：Bid価格（Close - スプレッド半分 - スリッページ）
            return candle.close - half_spread - self._slippage_price

    def _get_exit_price(
        self,
        signal_type: SignalType,
        candle: Candle,
    ) -> float:
        """決済価格を取得

        Args:
            signal_type: ポジションのシグナル種別
            candle: 足データ

        Returns:
            float: 決済価格
        """
        spread = self._get_spread_for_candle(candle)
        half_spread = spread / 2
        if signal_type == SignalType.BUY:
            # 買いポジション決済：Bid価格
            return candle.close - half_spread
        else:
            # 売りポジション決済：Ask価格
            return candle.close + half_spread

    # 成行決済の理由（candle.close ± half_spread）
    _MARKET_EXIT_REASONS = {
        ExitReason.TIME_EXIT,
        ExitReason.SIGNAL_REVERSAL,
    }

    def _calc_pm_fill_price(
        self,
        signal_type: SignalType,
        candle: Candle,
        trigger_price: float,
        exit_reason: ExitReason,
    ) -> float:
        """PM経路の決済価格を計算

        指値/逆指値（SL/TP/BE/TRAIL/1R/2R）は
        trigger_price ± slippage。
        成行（TIME_EXIT/SIGNAL_REVERSAL）は
        candle.close ± half_spread。

        Args:
            signal_type: ポジションのシグナル種別
            candle: 足データ
            trigger_price: トリガー価格
            exit_reason: 決済理由

        Returns:
            float: 決済価格
        """
        # 成行決済
        if exit_reason in self._MARKET_EXIT_REASONS:
            return self._get_exit_price(signal_type, candle)
        # 指値/逆指値決済
        slip = self._slippage_price
        if signal_type == SignalType.BUY:
            fill = trigger_price - slip
        else:
            fill = trigger_price + slip

        # ガード: 異常値検出（非JPY系通貨ペアも含む正値チェック）
        if fill <= 0.0:
            raise ValueError(
                f"異常な決済価格: fill={fill:.5f}, "
                f"trigger={trigger_price:.5f}, "
                f"reason={exit_reason.value}"
            )
        pips = abs(fill - trigger_price) / self._pip_unit
        if pips > 500:
            raise ValueError(
                f"異常なスリッページ: {pips:.1f}pips, "
                f"fill={fill:.5f}, "
                f"trigger={trigger_price:.5f}"
            )
        return fill

    def _get_spread_for_candle(self, candle: Candle) -> float:
        """足のセッション別スプレッド（価格単位）を取得

        Args:
            candle: 足データ

        Returns:
            float: スプレッド（価格単位）
        """
        if not self._use_session_spread:
            return self._spread_price

        hour = candle.time.hour
        spread_pips = self._hourly_spread[hour]
        return spread_pips * self._pip_unit

    def _is_opposite_signal(
        self,
        position: Position,
        signal: Signal,
    ) -> bool:
        """反対シグナルかどうか判定

        Args:
            position: ポジション
            signal: シグナル

        Returns:
            bool: 反対シグナルの場合True
        """
        if position.signal_type == SignalType.BUY:
            return signal.signal_type == SignalType.SELL
        else:
            return signal.signal_type == SignalType.BUY

    def _update_equity(self, candle: Candle) -> None:
        """評価額を更新

        Args:
            candle: 現在の足データ
        """
        open_pos = self.state.open_positions
        if not open_pos:
            self.state.equity = self.state.balance
            return

        close_price = candle.close
        pip_val = self._pip_value
        unrealized_pnl = 0.0

        for position in open_pos:
            if position.signal_type == SignalType.BUY:
                pips = (close_price - position.entry_price) * 100
            else:
                pips = (position.entry_price - close_price) * 100
            unrealized_pnl += pips * pip_val * position.volume

        self.state.equity = self.state.balance + unrealized_pnl

    def _update_mfe_mae(self, candle: Candle) -> None:
        """MFE/MAE（最大含み益/最大含み損）を更新

        各オープンポジションのhigh/lowからpips単位で追跡。
        R換算とMFE到達時刻も記録。

        Args:
            candle: 現在の足データ
        """
        for position in self.state.open_positions:
            pid = position.position_id
            if pid not in self._mfe_mae:
                # SL距離(pips)を保存
                sl_pips = 0.0
                if position.stop_loss:
                    sl_pips = abs(
                        position.entry_price - position.stop_loss
                    ) * 100
                self._mfe_mae[pid] = {
                    "mfe": 0.0, "mae": 0.0,
                    "sl_pips": sl_pips,
                    "mfe_r": 0.0, "mae_r": 0.0,
                    "mfe_time": None,
                }

            tracker = self._mfe_mae[pid]
            if position.signal_type == SignalType.BUY:
                fav = (candle.high - position.entry_price) * 100
                adv = (candle.low - position.entry_price) * 100
            else:
                fav = (position.entry_price - candle.low) * 100
                adv = (position.entry_price - candle.high) * 100

            if fav > tracker["mfe"]:
                tracker["mfe"] = fav
                tracker["mfe_time"] = candle.time
            if adv < tracker["mae"]:
                tracker["mae"] = adv

            # R換算
            sl_pips = tracker["sl_pips"]
            if sl_pips > 0:
                tracker["mfe_r"] = tracker["mfe"] / sl_pips
                tracker["mae_r"] = tracker["mae"] / sl_pips

    def get_position_mfe_mae(
        self, position_id: str,
    ) -> dict:
        """ポジションのMFE/MAEを取得

        Args:
            position_id: ポジションID

        Returns:
            dict: MFE/MAE(pips), R換算, MFE到達時刻
        """
        return self._mfe_mae.get(
            position_id,
            {
                "mfe": 0.0, "mae": 0.0,
                "sl_pips": 0.0,
                "mfe_r": 0.0, "mae_r": 0.0,
                "mfe_time": None,
            },
        )

    def get_entry_metrics(
        self, position_id: str,
    ) -> dict[str, float]:
        """ポジションのエントリー時メトリクスを取得

        Args:
            position_id: ポジションID

        Returns:
            dict: {"spread": float, ...}
        """
        return self._entry_metrics.get(
            position_id, {},
        )

    def get_exit_metrics(
        self, trade_id: str,
    ) -> dict[str, float]:
        """Exit時メトリクスを取得

        Args:
            trade_id: トレードID

        Returns:
            dict[str, float]: exit_spread等のメトリクス
        """
        return self._exit_metrics.get(trade_id, {})

    def _update_drawdown(self) -> None:
        """ドローダウンを更新"""
        if self.state.equity > self.state.peak_equity:
            self.state.peak_equity = self.state.equity

        if self.state.peak_equity > 0:
            self.state.current_drawdown = (
                (self.state.peak_equity - self.state.equity)
                / self.state.peak_equity
            )

            if self.state.current_drawdown > self.state.max_drawdown:
                self.state.max_drawdown = self.state.current_drawdown

    def _record_daily_pnl(self, current_time: datetime) -> None:
        """日次損益を記録（日付変更時のみ）

        Args:
            current_time: 現在時刻
        """
        day_ordinal = current_time.toordinal()
        if day_ordinal != self._last_pnl_date:
            self._last_pnl_date = day_ordinal
            date_key = current_time.strftime("%Y-%m-%d")
            self.state.daily_pnl[date_key] = self.state.equity

    def get_open_positions(self) -> list[Position]:
        """オープンポジションを取得

        Returns:
            list[Position]: オープンポジションリスト
        """
        return list(self.state.open_positions)

    def get_closed_trades(self) -> list[Trade]:
        """クローズ済みトレードを取得

        Returns:
            list[Trade]: クローズ済みトレードリスト
        """
        return list(self.state.closed_trades)

    def get_state_snapshot(self) -> dict[str, Any]:
        """状態のスナップショットを取得

        Returns:
            dict: 状態スナップショット
        """
        return {
            "balance": self.state.balance,
            "equity": self.state.equity,
            "open_positions_count": len(self.state.open_positions),
            "closed_trades_count": len(self.state.closed_trades),
            "peak_equity": self.state.peak_equity,
            "current_drawdown": self.state.current_drawdown,
            "max_drawdown": self.state.max_drawdown,
        }

    def can_open_position(
        self,
        strategy_id: str | None = None,
        signal_score: float | None = None,
    ) -> bool:
        """ポジションを開設可能かチェック

        Args:
            strategy_id: 戦略ID（指定時は戦略別制限を適用）
            signal_score: シグナルのconsensus_score（品質ボーナス枠判定用）

        Returns:
            ポジション開設可能ならTrue
        """
        # 品質ベース動的ポジション枠の計算
        effective_max = self.config.max_positions
        if (
            self.config.bonus_max_positions > 0
            and signal_score is not None
            and signal_score >= self.config.bonus_score_threshold
        ):
            effective_max += self.config.bonus_max_positions

        # 全体のポジション数制限
        if len(self.state.open_positions) >= effective_max:
            return False

        # 戦略別制限がある場合
        if strategy_id and strategy_id in self.config.strategy_max_positions:
            max_pos = self.config.strategy_max_positions[strategy_id]
            current = len(
                self.state.positions_by_strategy.get(strategy_id, [])
            )
            if current >= max_pos:
                return False

        return True

    def get_strategy_position_count(self, strategy_id: str) -> int:
        """戦略別ポジション数を取得

        Args:
            strategy_id: 戦略ID

        Returns:
            オープンポジション数
        """
        return len(self.state.positions_by_strategy.get(strategy_id, []))

    def force_close_all(
        self,
        candle: Candle,
        reason: ExitReason = ExitReason.FORCE_CLOSE,
    ) -> list[Trade]:
        """全ポジションを強制決済

        Args:
            candle: 現在の足データ
            reason: 決済理由

        Returns:
            list[Trade]: 決済されたトレードリスト
        """
        trades = []

        for position in list(self.state.open_positions):
            exit_price = self._get_exit_price(position.signal_type, candle)
            trade = self._close_position(
                position, exit_price, candle.time, reason,
                trigger_price=0.0,
            )
            trades.append(trade)

        return trades
