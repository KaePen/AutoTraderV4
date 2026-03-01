"""トレード実行サービス

エントリー判定・MT5発注・PositionManager登録・DB記録を担当する。
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import uuid
from datetime import UTC, datetime

from autotrader.core.entities import Signal
from autotrader.core.enums import (
    MarketRegime,
    SignalType,
)
from autotrader.core.event_bus import event_bus
from autotrader.core.interfaces.position_sizing import (
    SizingContext,
)
from autotrader.decision.unified.config import (
    UnifiedBotConfig,
)
from autotrader.decision.unified.position_sizer import (
    PositionSizer,
    PositionSizerConfig,
)
from autotrader.decision.unified.signal_consolidator import (
    ConsolidatedSignal,
)

logger = logging.getLogger(__name__)


def build_sizer_config(
    bot_config: UnifiedBotConfig,
    symbol: str = "",
) -> PositionSizerConfig:
    """UnifiedBotConfigからPositionSizerConfigを生成

    UnifiedBotConfigはPositionSizerConfigと別型のため
    必要なフィールドを抽出して変換する。

    Args:
        bot_config: Bot設定
        symbol: 通貨ペアシンボル（pip_value自動計算用）

    Returns:
        PositionSizerConfig: サイザー設定
    """
    return PositionSizerConfig(
        symbol=symbol,
        base_risk_pct=bot_config.base_risk_pct,
        max_risk_pct_absolute=(bot_config.max_risk_pct_absolute),
        max_lot_per_trade=bot_config.max_lot_per_trade,
        max_total_exposure_lot=(bot_config.max_total_exposure_lot),
        equity_floor_pct=bot_config.equity_floor_pct,
        equity_caution_pct=bot_config.equity_caution_pct,
        slippage_buffer_pips=(bot_config.slippage_buffer_pips),
    )


def get_pip_size(symbol: str) -> float:
    """通貨ペアのpipサイズを返す（JPY系=0.01、その他=0.0001）

    Args:
        symbol: 通貨ペアシンボル

    Returns:
        float: pipサイズ
    """
    return 0.01 if "JPY" in symbol.upper() else 0.0001


def get_pip_value(symbol: str) -> float:
    """通貨ペアの1lot/1pipあたりの価値を返す
    （JPY系=1000、その他=10）

    Args:
        symbol: 通貨ペアシンボル

    Returns:
        float: pip価値（円）
    """
    return 1000.0 if "JPY" in symbol.upper() else 10.0


class TradeExecutorService:
    """トレード実行サービス

    Attributes:
        _executor: MT5トレード実行
        _sizer: ポジションサイザー
        _pm: ポジションマネージャ
        _bot: 統合トレードボット
        _tick_optimizer: ティックエントリー最適化
        _data_provider: MT5データプロバイダ
    """

    def __init__(
        self,
        executor: object,
        sizer: PositionSizer,
        pm: object,
        bot: object,
        tick_optimizer: object,
        data_provider: object,
    ) -> None:
        """初期化

        Args:
            executor: MT5TradeExecutor
            sizer: PositionSizer
            pm: PositionManager
            bot: UnifiedTradeBot
            tick_optimizer: TickEntryOptimizer
            data_provider: MT5DataProvider
        """
        self._executor = executor
        self._sizer = sizer
        self._pm = pm
        self._bot = bot
        self._tick_optimizer = tick_optimizer
        self._data_provider = data_provider

    @property
    def sizer(self) -> PositionSizer:
        """ポジションサイザー"""
        return self._sizer

    @sizer.setter
    def sizer(self, value: PositionSizer) -> None:
        """ポジションサイザー設定"""
        self._sizer = value

    @property
    def tick_optimizer(self) -> object:
        """ティックエントリー最適化"""
        return self._tick_optimizer

    @tick_optimizer.setter
    def tick_optimizer(self, value: object) -> None:
        """ティックエントリー最適化設定"""
        self._tick_optimizer = value

    def should_use_tick_optimizer(self) -> bool:
        """ティック最適化を使用すべきか判定

        Returns:
            bool: ティック最適化を使用すべきか
        """
        cfg = self._tick_optimizer._config
        if not cfg.enabled:
            return False

        # デモモードでは無効
        return not getattr(self._bot.config, "demo_mode", False)

    def consolidated_to_signal(
        self,
        cs: ConsolidatedSignal,
        active_symbol: str,
    ) -> Signal:
        """ConsolidatedSignalをSignalエンティティに変換

        Args:
            cs: 統合シグナル
            active_symbol: アクティブシンボル

        Returns:
            Signal: シグナルエンティティ
        """
        return Signal(
            signal_id=str(uuid.uuid4()),
            symbol=active_symbol,
            timeframe=cs.primary_tf,
            signal_type=cs.direction,
            confidence=cs.confidence,
            stop_loss=cs.sl_pips,
            take_profit=cs.tp_pips,
            reasoning=cs.rationale,
            created_at=datetime.now(UTC),
            indicators_snapshot={},
            regime=cs.regime,
            mode=cs.mode,
            consensus_score=cs.consensus_score,
        )

    async def execute_entry(
        self,
        signal: Signal,
        active_symbol: str,
        account_info,
        open_trades: dict[int, str],
        cached_positions: list[dict],
        save_position_state,
    ) -> None:
        """エントリー実行

        Args:
            signal: トレードシグナル
            active_symbol: アクティブシンボル
            account_info: 口座情報
            open_trades: オープントレード辞書
            cached_positions: キャッシュ済みポジション
            save_position_state: 状態保存コールバック
        """
        # 既存ポジションチェック（設定値に基づく上限）
        positions = await self._executor.get_open_positions_async(
            active_symbol
        )
        # MT5接続エラー時はエントリーを安全にスキップ
        if positions is None:
            logger.warning("MT5ポジション取得失敗 — エントリースキップ")
            return
        cfg = self._bot.config
        base_max = (
            cfg.demo_max_positions if cfg.demo_mode else cfg.max_positions
        )
        bonus = getattr(cfg, "bonus_max_positions", 0)
        threshold = getattr(cfg, "bonus_score_threshold", 7.0)
        if (
            bonus > 0
            and signal.consensus_score is not None
            and signal.consensus_score >= threshold
        ):
            max_pos = base_max + bonus
        else:
            max_pos = base_max
        if len(positions) >= max_pos:
            logger.info(
                "既存ポジション上限(%d)、エントリースキップ",
                max_pos,
            )
            return

        # ロット計算
        if account_info is None:
            logger.warning("口座情報なし、エントリースキップ")
            return

        # signal.stop_lossはpips値
        sl_pips = (
            signal.stop_loss
            if signal.stop_loss is not None and signal.stop_loss > 0
            else 30.0
        )

        # SizingContextを作成
        regime = MarketRegime.RANGE
        if signal.regime:
            with contextlib.suppress(ValueError):
                regime = MarketRegime(signal.regime)

        sizing_ctx = SizingContext(
            equity=account_info.equity,
            sl_pips=sl_pips if sl_pips > 0 else 30.0,
            confidence=signal.confidence,
            regime=regime,
            consecutive_losses=0,
            current_dd_pct=0.0,
            initial_equity=account_info.balance,
        )
        sizing_result = self._sizer.calculate(sizing_ctx)

        if sizing_result.blocked:
            logger.warning(
                "サイジング拒否: %s",
                sizing_result.reasoning,
            )
            return

        lot = sizing_result.lot

        if lot <= 0:
            logger.warning("ロット計算結果=0、エントリースキップ")
            return

        # Signal にlotを付与
        signal_with_lot = signal.model_copy(update={"lot": lot})

        # MT5発注（発注直前のtick価格を取得してentry_priceに使用）
        entry_tick = await self._data_provider.get_tick(active_symbol)
        result = await self._executor.open_position_async(signal_with_lot, lot)

        if result.success:
            logger.info(
                "エントリー成功: ticket=%d %.2f lots",
                result.ticket or 0,
                lot,
            )
            # PositionManagerに登録
            trade_id = ""
            if result.ticket:
                await self._register_new_position(
                    result.ticket,
                    signal_with_lot,
                    lot,
                    entry_tick,
                    save_position_state,
                )
                # DB書き込み（エントリー記録）
                trade_id = (
                    self._write_entry_to_db(
                        result.ticket,
                        signal_with_lot,
                        lot,
                        entry_tick,
                    )
                    or ""
                )
                if trade_id:
                    open_trades[result.ticket] = trade_id

            # _cached_positionsに即時追加（次tick待ち不要）
            entry_price = signal_with_lot.stop_loss or 0.0
            if entry_tick:
                price_key = (
                    "ask"
                    if signal_with_lot.signal_type == SignalType.BUY
                    else "bid"
                )
                entry_price = (
                    float(entry_tick.get(price_key, 0)) or entry_price
                )
            cached_positions.append(
                {
                    "position_id": str(result.ticket or 0),
                    "trade_id": trade_id,
                    "ticket": result.ticket or 0,
                    "symbol": active_symbol,
                    "signal_type": (signal_with_lot.signal_type.value),
                    "volume": lot,
                    "entry_price": entry_price,
                    "current_price": entry_price,
                    "stop_loss": signal_with_lot.stop_loss,
                    "take_profit": (signal_with_lot.take_profit),
                    "opened_at": datetime.now(
                        UTC,
                    ).isoformat(),
                    "unrealized_pnl": 0.0,
                    "unrealized_pnl_pips": 0.0,
                    "remaining_minutes": None,
                    "max_hold_minutes": None,
                    "elapsed_minutes": 0,
                }
            )

            # TradeBotに通知（取引時刻を渡す）
            self._bot.on_trade_executed(signal.created_at)
            # EventBus経由でポジション更新ブロードキャスト
            event_bus.publish_nowait(
                "position.opened",
                {"symbol": active_symbol},
            )
        else:
            logger.error("エントリー失敗: %s", result.message)

    async def _register_new_position(
        self,
        ticket: int,
        signal: Signal,
        volume: float,
        entry_tick: dict | None,
        save_position_state,
    ) -> None:
        """新ポジションをPositionManagerに登録

        Args:
            ticket: MT5チケットID
            signal: トレードシグナル
            volume: ロット数
            entry_tick: エントリー時のtick情報（ask/bid）
            save_position_state: 状態保存コールバック
        """
        from autotrader.core.enums import (
            TradingStrategyMode,
        )
        from autotrader.decision.unified.mode_selector import (
            TradingModeSelector,
        )

        # エントリー価格（実際のask/bid価格）
        is_buy = signal.signal_type == SignalType.BUY
        if entry_tick:
            entry_price = float(
                entry_tick.get("ask", 0)
                if is_buy
                else entry_tick.get("bid", 0)
            )
        else:
            entry_price = 0.0

        # signal.stop_loss/take_profitはpips値→価格レベルに変換
        pip_size = get_pip_size(signal.symbol)
        sl_price = 0.0
        tp_price = 0.0
        if entry_price > 0:
            if signal.stop_loss and signal.stop_loss > 0:
                sl_dist = signal.stop_loss * pip_size
                sl_price = (
                    entry_price - sl_dist if is_buy else entry_price + sl_dist
                )
            if signal.take_profit and signal.take_profit > 0:
                tp_dist = signal.take_profit * pip_size
                tp_price = (
                    entry_price + tp_dist if is_buy else entry_price - tp_dist
                )

        logger.info(
            "PM登録: ticket=%d entry=%.3f sl=%.3f tp=%.3f",
            ticket,
            entry_price,
            sl_price,
            tp_price,
        )

        # signal.modeから実際のモードを解決
        # （デフォルト: UNIVERSAL）
        mode = TradingStrategyMode.UNIVERSAL
        if signal.mode:
            try:
                mode = TradingStrategyMode(signal.mode.upper())
            except ValueError:
                logger.warning(
                    "不明なモード: %s、UNIVERSALを使用",
                    signal.mode,
                )

        # ModeSelector経由でモード別プランパラメータを取得
        _base_plan = TradingModeSelector().get_plan_for_mode(mode)
        plan = dataclasses.replace(
            _base_plan,
            selection_reason="live",
            regime=signal.regime,
        )
        logger.info(
            "PM登録プラン: mode=%s primary_tf=%s",
            mode.value,
            plan.primary_tf,
        )

        self._pm.register_position(
            position_id=str(ticket),
            direction=signal.signal_type,
            entry_price=entry_price,
            entry_time=datetime.now(UTC),
            sl=sl_price,
            tp=tp_price,
            volume=volume,
            plan=plan,
        )
        # 新規登録時に管理状態をローカルDBに保存
        save_position_state(str(ticket))

    def _write_entry_to_db(
        self,
        ticket: int,
        signal: Signal,
        lot: float,
        entry_tick: dict | None,
    ) -> str | None:
        """エントリーをDBに記録

        Args:
            ticket: MT5チケットID
            signal: トレードシグナル
            lot: ロット数
            entry_tick: エントリー時のtick情報

        Returns:
            str | None: 作成されたtrade_id（失敗時None）
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.repositories import (
            TradeRepository,
        )
        from autotrader.config.settings import get_settings

        try:
            is_buy = signal.signal_type == SignalType.BUY
            if entry_tick:
                entry_price = float(
                    entry_tick.get("ask", 0)
                    if is_buy
                    else entry_tick.get("bid", 0)
                )
            else:
                entry_price = 0.0
            pip_size = get_pip_size(signal.symbol)
            sl_price = None
            tp_price = None
            if entry_price > 0:
                if signal.stop_loss and signal.stop_loss > 0:
                    sl_dist = signal.stop_loss * pip_size
                    sl_price = (
                        entry_price - sl_dist
                        if is_buy
                        else entry_price + sl_dist
                    )
                if signal.take_profit and signal.take_profit > 0:
                    tp_dist = signal.take_profit * pip_size
                    tp_price = (
                        entry_price + tp_dist
                        if is_buy
                        else entry_price - tp_dist
                    )
            db_url = get_settings().database_url
            with get_session(db_url) as db:
                repo = TradeRepository(db)
                trade = repo.create(
                    symbol=signal.symbol,
                    signal_type=signal.signal_type.value,
                    volume=lot,
                    entry_price=entry_price,
                    opened_at=datetime.now(UTC),
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    ticket=ticket,
                )
                trade_id = trade.trade_id
            logger.info(
                "DB記録（エントリー）: trade_id=%s ticket=%d",
                trade_id,
                ticket,
            )
            return trade_id
        except Exception as e:
            logger.error("DB書き込みエラー（エントリー）: %s", e)
            return None
