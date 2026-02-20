"""ライブトレーディングエンジン

asyncioメインループで定期的にMT5データを取得し、
既存の意思決定層（TradeBot, PositionManager, PositionSizer）で
シグナル生成・ポジション管理を実行する。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

import pandas as pd

from autotrader.adapters.mt5.connection import MT5ConnectionManager
from autotrader.adapters.mt5.data_provider import MT5DataProvider
from autotrader.adapters.mt5.trade_executor import MT5TradeExecutor
from autotrader.core.entities import AccountInfo, Signal
from autotrader.core.enums import MarketRegime, SignalType, Timeframe
from autotrader.core.interfaces.position_sizing import SizingContext
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.position_manager import (
    ManagementActionType,
    PositionManager,
    PositionManagerConfig,
)
from autotrader.decision.unified.position_sizer import (
    PositionSizer,
    PositionSizerConfig,
)
from autotrader.decision.unified.trade_bot import UnifiedTradeBot
from autotrader.live.config import LiveTradingConfig
from autotrader.decision.unified.signal_consolidator import (
    ConsolidatedSignal,
)
from autotrader.live.tick_entry_optimizer import TickEntryOptimizer

logger = logging.getLogger(__name__)


class LiveTradingEngine:
    """ライブトレーディングエンジン

    Attributes:
        _config: ライブトレーディング設定
        _conn: MT5接続マネージャ
        _data_provider: MT5データプロバイダ
        _executor: MT5トレード実行
        _bot: 統合トレードボット
        _pm: ポジションマネージャ
        _sizer: ポジションサイザー
        _running: エンジン実行中フラグ
        _task: メインループタスク
        _account_info: 最新の口座情報
    """

    def __init__(self, config: LiveTradingConfig) -> None:
        """初期化

        Args:
            config: ライブトレーディング設定
        """
        self._config = config
        self._conn = MT5ConnectionManager(config.mt5_config)
        self._data_provider = MT5DataProvider(self._conn)
        self._executor = MT5TradeExecutor(
            conn=self._conn,
            magic=config.mt5_config.magic_number,
            deviation=config.mt5_config.deviation,
            symbol=config.symbol,
        )
        self._bot = UnifiedTradeBot(config.bot_config)
        self._pm = PositionManager()
        self._sizer = PositionSizer(
            self._build_sizer_config(config.bot_config)
        )
        self._running = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._account_info: AccountInfo | None = None

        # コールバック（WebSocket連携用）
        self.on_signal: asyncio.Event | None = None
        self.on_position_update: asyncio.Event | None = None
        self.on_account_update: asyncio.Event | None = None

        # 最新データキャッシュ
        self._last_signal: Signal | None = None
        self._last_analysis: ConsolidatedSignal | None = None
        self._last_tick_time: datetime | None = None
        self._signal_history: list[Signal] = []
        self._enable_auto_trade = config.enable_auto_trade

        # ティックエントリー最適化
        self._tick_optimizer = TickEntryOptimizer(
            config=config.tick_entry_config,
            data_provider=self._data_provider,
            symbol=config.symbol,
        )

        # キャッシュ済みポジション（UI表示用）
        self._cached_positions: list[dict] = []
        # シンボル別デモモード状態（UI表示用）
        self._symbol_demo_mode: dict[str, bool] = {}

    @property
    def connected(self) -> bool:
        """MT5接続状態"""
        return self._conn.connected

    @property
    def running(self) -> bool:
        """エンジン実行中"""
        return self._running

    @property
    def account_info(self) -> AccountInfo | None:
        """最新の口座情報"""
        return self._account_info

    @property
    def enable_auto_trade(self) -> bool:
        """自動取引ON/OFF"""
        return self._enable_auto_trade

    @enable_auto_trade.setter
    def enable_auto_trade(self, value: bool) -> None:
        """自動取引ON/OFF設定"""
        self._enable_auto_trade = value
        logger.info("自動取引: %s", "ON" if value else "OFF")

    @property
    def last_analysis(self) -> ConsolidatedSignal | None:
        """直近のtick分析結果"""
        return self._last_analysis

    @property
    def last_tick_time(self) -> datetime | None:
        """直近のtick処理時刻"""
        return self._last_tick_time

    @property
    def demo_mode_enabled(self) -> bool:
        """デモモード状態"""
        return getattr(
            self._bot.config, "demo_mode", False
        )

    @property
    def symbol_auto_trade_states(self) -> dict[str, bool]:
        """シンボル別自動取引状態"""
        return {self._config.symbol: self._enable_auto_trade}

    @property
    def symbol_demo_mode_states(self) -> dict[str, bool]:
        """シンボル別デモモード状態"""
        if self._symbol_demo_mode:
            return dict(self._symbol_demo_mode)
        return {self._config.symbol: self.demo_mode_enabled}

    @property
    def trade_history(self) -> list[dict]:
        """クローズ済みトレード履歴（未実装：DBフォールバック用）"""
        return []

    @property
    def cached_positions(self) -> list[dict]:
        """キャッシュ済みオープンポジション（UI表示用）"""
        return self._cached_positions

    def set_symbol_auto_trade(
        self, symbol: str, enable: bool
    ) -> None:
        """シンボルごとの自動取引ON/OFF設定

        Args:
            symbol: 通貨ペアシンボル
            enable: 自動取引を有効にするか
        """
        self._enable_auto_trade = enable
        logger.info(
            "シンボル自動取引: %s %s",
            symbol,
            "ON" if enable else "OFF",
        )

    def set_symbol_demo_mode(
        self, symbol: str, enable: bool
    ) -> None:
        """シンボルごとのデモモードON/OFF設定

        Args:
            symbol: 通貨ペアシンボル
            enable: デモモードを有効にするか
        """
        self._symbol_demo_mode[symbol] = enable
        logger.info(
            "シンボルデモモード: %s %s",
            symbol,
            "ON" if enable else "OFF",
        )

    def reset_data_update_timer(self) -> None:
        """データ更新タイマーをリセット（次回tick即時実行）"""
        self._last_tick_time = None
        logger.debug("データ更新タイマーリセット")

    def update_bot_config(self, new_config: UnifiedBotConfig) -> None:
        """Botの設定を動的に更新する

        デモ/ライブモード切り替え時やWebUI設定変更時に呼ばれる。
        TradeBot.config と PositionSizer を新設定で再構築する。

        Args:
            new_config: 新しいUnifiedBotConfig
        """
        self._bot.config = new_config
        self._sizer = PositionSizer(
            self._build_sizer_config(new_config)
        )
        logger.info("BotConfig更新完了 demo_mode=%s", new_config.demo_mode)

    def update_pm_config(self, new_config: PositionManagerConfig) -> None:
        """PositionManagerの設定を動的に更新する

        WebUI設定変更時にポジション管理パラメータを即時反映する。

        Args:
            new_config: 新しいPositionManagerConfig
        """
        self._pm.config = new_config
        logger.info("PositionManagerConfig更新完了")

    @staticmethod
    def _build_sizer_config(
        bot_config: UnifiedBotConfig,
    ) -> PositionSizerConfig:
        """UnifiedBotConfigからPositionSizerConfigを生成

        UnifiedBotConfigはPositionSizerConfigと別型のため
        必要なフィールドを抽出して変換する。

        Args:
            bot_config: Bot設定

        Returns:
            PositionSizerConfig: サイザー設定
        """
        return PositionSizerConfig(
            base_risk_pct=bot_config.base_risk_pct,
            max_risk_pct_absolute=bot_config.max_risk_pct_absolute,
            max_lot_per_trade=bot_config.max_lot_per_trade,
            max_total_exposure_lot=bot_config.max_total_exposure_lot,
            equity_floor_pct=bot_config.equity_floor_pct,
            equity_caution_pct=bot_config.equity_caution_pct,
            slippage_buffer_pips=bot_config.slippage_buffer_pips,
        )

    @property
    def signal_history(self) -> list[Signal]:
        """シグナル履歴"""
        return self._signal_history

    async def start(self) -> None:
        """エンジン開始"""
        if self._running:
            logger.warning("エンジンは既に実行中です")
            return

        logger.info("ライブトレーディングエンジン開始")

        # MT5接続
        await self._conn.connect()

        # 過去データ読込
        await self._load_historical_data()

        # 口座情報取得
        self._account_info = (
            await self._data_provider.get_account_info()
        )

        # 既存ポジション同期
        await self._sync_positions()

        # メインループ開始
        self._running = True
        self._task = asyncio.create_task(self._main_loop())
        logger.info(
            "エンジン起動完了: symbol=%s interval=%.0fs auto=%s",
            self._config.symbol,
            self._config.check_interval_sec,
            self._enable_auto_trade,
        )

    async def stop(self) -> None:
        """エンジン停止"""
        self._running = False

        # ティック監視中ならキャンセル
        if self._tick_optimizer.is_active:
            self._tick_optimizer.cancel_monitoring(
                "エンジン停止"
            )

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        await self._conn.disconnect()
        logger.info("ライブトレーディングエンジン停止")

    async def _main_loop(self) -> None:
        """メインループ

        check_interval_sec間隔で_tickを実行。
        ティック監視中は高速ポーリング（100ms）。
        """
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(
                    "ティック処理エラー: %s", e, exc_info=True
                )

            if self._tick_optimizer.is_active:
                await asyncio.sleep(
                    self._config.tick_entry_config
                    .poll_interval_sec
                )
            else:
                await asyncio.sleep(
                    self._config.check_interval_sec
                )

    async def _tick(self) -> None:
        """1ティック分の処理

        口座情報→ローソク足→シグナル生成→エントリー判定→ポジション管理
        """
        # 1. 口座情報更新
        self._account_info = (
            await self._data_provider.get_account_info()
        )

        # 2. 最新ローソク足データ取得・設定
        await self._update_market_data()

        # 3. シグナル生成
        current_time = pd.Timestamp.now(tz="UTC")
        signal = self._bot.generate_signal(current_time)

        # 全tickの分析結果を保存（HOLD含む）
        self._last_analysis = signal
        self._last_tick_time = datetime.now(timezone.utc)

        if signal and signal.direction != SignalType.HOLD:
            self._last_signal = signal
            converted = self._consolidated_to_signal(signal)
            self._signal_history.append(converted)
            # 履歴上限
            if len(self._signal_history) > 200:
                self._signal_history = (
                    self._signal_history[-200:]
                )
            logger.info(
                "シグナル生成: %s conf=%.2f",
                signal.direction.value,
                signal.confidence,
            )

            # WebSocketシグナルブロードキャスト
            try:
                from autotrader.web.websocket.handlers import (
                    broadcast_signal_update,
                )
                asyncio.create_task(
                    broadcast_signal_update({
                        "signal_id": converted.signal_id,
                        "symbol": converted.symbol,
                        "timeframe": converted.timeframe,
                        "signal_type": converted.signal_type.value,
                        "confidence": converted.confidence,
                        "confidence_level": (
                            converted.confidence_level.value
                        ),
                        "stop_loss": converted.stop_loss,
                        "take_profit": converted.take_profit,
                        "reasoning": converted.reasoning,
                        "created_at": (
                            converted.created_at.isoformat()
                        ),
                    })
                )
            except Exception:
                pass  # ブロードキャスト失敗は無視

            # 4. エントリー判定
            if self._enable_auto_trade:
                entry_signal = self._consolidated_to_signal(signal)
                if self._should_use_tick_optimizer():
                    self._tick_optimizer.start_monitoring(
                        entry_signal
                    )
                else:
                    await self._execute_entry(entry_signal)

        # 4.5 ティック監視ポーリング
        if self._tick_optimizer.is_active:
            result = await self._tick_optimizer.poll_tick()
            if result is not None:
                if result.should_execute:
                    pending = (
                        self._tick_optimizer.pending_signal
                    )
                    if pending is not None:
                        await self._execute_entry(pending)
                self._tick_optimizer.reset()

        # 5. 既存ポジション管理
        await self._manage_positions()

    def _consolidated_to_signal(
        self, cs: ConsolidatedSignal,
    ) -> Signal:
        """ConsolidatedSignalをSignalエンティティに変換

        Args:
            cs: 統合シグナル

        Returns:
            Signal: シグナルエンティティ
        """
        return Signal(
            signal_id=str(uuid.uuid4()),
            symbol=self._config.symbol,
            timeframe=cs.primary_tf,
            signal_type=cs.direction,
            confidence=cs.confidence,
            stop_loss=cs.sl_pips,
            take_profit=cs.tp_pips,
            reasoning=cs.rationale,
            created_at=datetime.now(timezone.utc),
            indicators_snapshot={},
            regime=cs.regime,
            mode=cs.mode,
            consensus_score=cs.consensus_score,
        )

    async def _load_historical_data(self) -> None:
        """起動時に過去データをTradeBotに供給

        全TFのデータを一括収集してから設定。
        （個別set_market_dataは辞書を上書きするため）
        """
        symbol = self._config.symbol
        lookback = self._config.candle_lookback
        timeframes = self._bot.timeframes

        logger.info(
            "過去データ読込: %s %d本 x %d時間足",
            symbol, lookback, len(timeframes),
        )

        all_data: dict[str, pd.DataFrame] = {}
        for tf_str in timeframes:
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                logger.warning("未知の時間足: %s", tf_str)
                continue

            df = await self._data_provider.get_candles_from_pos(
                symbol, tf, lookback
            )
            if df.empty:
                logger.warning(
                    "データなし: %s %s", symbol, tf_str
                )
                continue

            all_data[tf_str] = df
            logger.info(
                "データ読込完了: %s %s %d本",
                symbol, tf_str, len(df),
            )

        if all_data:
            self._bot.set_market_data(all_data)
            logger.info(
                "全TFデータ設定完了: %d時間足", len(all_data)
            )

    async def _update_market_data(self) -> None:
        """最新ローソク足データを取得してTradeBotに設定"""
        symbol = self._config.symbol
        # 全TFのデータを一括収集してから設定
        # （個別set_market_dataは辞書を上書きするため）
        all_data: dict[str, pd.DataFrame] = {}
        for tf_str in self._bot.timeframes:
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                continue

            df = await self._data_provider.get_candles_from_pos(
                symbol, tf, 30
            )
            if not df.empty:
                all_data[tf_str] = df

        if all_data:
            self._bot.set_market_data(all_data)

    def _should_use_tick_optimizer(self) -> bool:
        """ティック最適化を使用すべきか判定

        Returns:
            bool: ティック最適化を使用すべきか
        """
        cfg = self._config.tick_entry_config
        if not cfg.enabled:
            return False

        # デモモードでは無効
        if getattr(self._bot.config, "demo_mode", False):
            return False

        # 現在のモード判定
        current_mode = getattr(
            self._bot, "current_mode", ""
        )
        if isinstance(current_mode, str):
            mode_name = current_mode.upper()
        else:
            mode_name = str(current_mode).upper()

        if cfg.enabled_modes and mode_name:
            return mode_name in (
                m.upper() for m in cfg.enabled_modes
            )

        # モード不明の場合はenabledに従う
        return True

    async def _execute_entry(self, signal: Signal) -> None:
        """エントリー実行

        Args:
            signal: トレードシグナル
        """
        # 既存ポジションチェック
        # デモモードではdemo_max_positions分まで許可
        positions = await self._executor.get_open_positions_async(
            self._config.symbol
        )
        max_pos = (
            self._bot.config.demo_max_positions
            if self._bot.config.demo_mode
            else 1
        )
        if len(positions) >= max_pos:
            logger.info(
                "既存ポジション上限(%d/%d)、エントリースキップ",
                len(positions), max_pos,
            )
            return

        # ロット計算
        if self._account_info is None:
            logger.warning("口座情報なし、エントリースキップ")
            return

        # signal.stop_lossはpips値（_consolidated_to_signalでsl_pipsを設定）
        sl_pips = (
            signal.stop_loss
            if signal.stop_loss is not None and signal.stop_loss > 0
            else 30.0
        )

        # SizingContextを作成
        regime = MarketRegime.RANGE
        if signal.regime:
            try:
                regime = MarketRegime(signal.regime)
            except ValueError:
                pass

        sizing_ctx = SizingContext(
            equity=self._account_info.equity,
            sl_pips=sl_pips if sl_pips > 0 else 30.0,
            confidence=signal.confidence,
            regime=regime,
            consecutive_losses=0,
            current_dd_pct=0.0,
            initial_equity=self._account_info.balance,
        )
        sizing_result = self._sizer.calculate(sizing_ctx)

        if sizing_result.blocked:
            logger.warning(
                "サイジング拒否: %s", sizing_result.reasoning
            )
            return

        lot = sizing_result.lot

        if lot <= 0:
            logger.warning("ロット計算結果=0、エントリースキップ")
            return

        # Signal にlotを付与
        signal_with_lot = signal.model_copy(update={"lot": lot})

        # MT5発注（発注直前のtick価格を取得してentry_priceに使用）
        entry_tick = await self._data_provider.get_tick(
            self._config.symbol
        )
        result = await self._executor.open_position_async(
            signal_with_lot, lot
        )

        if result.success:
            logger.info(
                "エントリー成功: ticket=%d %.2f lots",
                result.ticket or 0, lot,
            )
            # PositionManagerに登録
            if result.ticket:
                await self._register_new_position(
                    result.ticket, signal_with_lot, lot, entry_tick
                )
            # TradeBotに通知（取引時刻を渡す）
            self._bot.on_trade_executed(signal.created_at)
            # WebSocketポジション更新ブロードキャスト
            try:
                from autotrader.web.websocket.handlers import (
                    broadcast_position_update,
                )
                asyncio.create_task(
                    broadcast_position_update(
                        {"symbol": self._config.symbol}
                    )
                )
            except Exception:
                pass
        else:
            logger.error("エントリー失敗: %s", result.message)

    async def _register_new_position(
        self,
        ticket: int,
        signal: Signal,
        volume: float,
        entry_tick: dict | None = None,
    ) -> None:
        """新ポジションをPositionManagerに登録

        Args:
            ticket: MT5チケットID
            signal: トレードシグナル
            volume: ロット数
            entry_tick: エントリー時のtick情報（ask/bid）
        """
        from autotrader.decision.unified.mode_selector import (
            TradingPlan,
        )
        from autotrader.core.enums import TradingStrategyMode

        # エントリー価格（実際のask/bid価格）
        is_buy = signal.signal_type == SignalType.BUY
        if entry_tick:
            entry_price = float(
                entry_tick.get("ask", 0) if is_buy
                else entry_tick.get("bid", 0)
            )
        else:
            entry_price = 0.0

        # signal.stop_loss/take_profitはpips値 → 価格レベルに変換
        # USDJPY: pip_size=0.01, その他: pip_size=0.0001
        pip_size = (
            0.01 if "JPY" in signal.symbol.upper() else 0.0001
        )
        sl_price = 0.0
        tp_price = 0.0
        if entry_price > 0:
            if signal.stop_loss and signal.stop_loss > 0:
                sl_dist = signal.stop_loss * pip_size
                sl_price = (
                    entry_price - sl_dist if is_buy
                    else entry_price + sl_dist
                )
            if signal.take_profit and signal.take_profit > 0:
                tp_dist = signal.take_profit * pip_size
                tp_price = (
                    entry_price + tp_dist if is_buy
                    else entry_price - tp_dist
                )

        logger.info(
            "PM登録: ticket=%d entry=%.3f sl=%.3f tp=%.3f",
            ticket, entry_price, sl_price, tp_price,
        )

        # デフォルトのトレーディングプラン
        plan = TradingPlan(
            mode=TradingStrategyMode.DAY_TRADE,
            primary_tf="M15",
            entry_tf="M15",
            confirm_tfs=["H1", "H4"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.1, 1.4),
            selection_reason="live_default",
            regime=signal.regime,
        )

        self._pm.register_position(
            position_id=str(ticket),
            direction=signal.signal_type,
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc),
            sl=sl_price,
            tp=tp_price,
            volume=volume,
            plan=plan,
        )

    async def _manage_positions(self) -> None:
        """既存ポジションの管理

        PositionManager.evaluateで各ポジションを評価し、
        SL変更・部分決済・全決済をMT5で実行。
        _cached_positionsをMT5の現在状態で更新する。
        """
        positions = await self._executor.get_open_positions_async(
            self._config.symbol
        )
        if not positions:
            self._cached_positions = []
            return

        # ATR取得（ポジション管理で使用）
        # USDJPY換算で約20pips相当を最小値とする
        _min_atr = 0.20 if "JPY" in self._config.symbol.upper() else 0.0020
        try:
            latest = await self._data_provider.get_latest_candle_async(
                self._config.symbol, Timeframe.M15
            )
            # ATRは簡易計算（最新の高値-安値）
            _h = float(latest.get("high", 0))
            _l = float(latest.get("low", 0))
            atr = _h - _l if (_h > 0 and _l > 0) else _min_atr
            atr = max(atr, _min_atr)
        except Exception:
            atr = 0.3  # デフォルト（USDJPY: 約30pips）

        # 現在のシグナル方向（反転チェック用）
        current_signal_type = None
        if self._last_signal:
            current_signal_type = self._last_signal.direction

        # pip計算係数（通貨ペア別）
        is_jpy = "JPY" in self._config.symbol.upper()
        pip_factor = 0.01 if is_jpy else 0.0001
        pip_value = 1000.0 if is_jpy else 10.0

        cache_list: list[dict] = []
        for position in positions:
            pos_id = str(position.ticket)

            # ティック取得（キャッシュ＋管理評価で共用）
            current_price = position.entry_price
            try:
                tick = await self._data_provider.get_tick(
                    position.symbol
                )
                price_key = (
                    "bid"
                    if position.signal_type == SignalType.BUY
                    else "ask"
                )
                fetched = float(tick.get(price_key, 0))
                if fetched > 0:
                    current_price = fetched
            except Exception:
                pass

            # キャッシュエントリ構築（MT5全ポジションを対象）
            pip_diff = (
                (current_price - position.entry_price) / pip_factor
            )
            if position.signal_type == SignalType.SELL:
                pip_diff = -pip_diff
            cache_list.append({
                "position_id": str(position.ticket),
                "ticket": position.ticket,
                "symbol": position.symbol,
                "signal_type": position.signal_type.value,
                "volume": position.volume,
                "entry_price": position.entry_price,
                "current_price": current_price,
                "stop_loss": position.stop_loss,
                "take_profit": position.take_profit,
                "opened_at": (
                    position.opened_at.isoformat()
                    if hasattr(position.opened_at, "isoformat")
                    else str(position.opened_at)
                ),
                "unrealized_pnl": (
                    pip_diff * position.volume * pip_value
                ),
                "unrealized_pnl_pips": pip_diff,
            })

            # PM未登録ならアクション評価をスキップ
            managed = self._pm.get_position(pos_id)
            if managed is None:
                continue

            try:
                # ポジション評価
                action = self._pm.evaluate(
                    position_id=pos_id,
                    current_price=current_price,
                    current_time=datetime.now(timezone.utc),
                    atr=atr,
                    current_signal=current_signal_type,
                )

                # アクション実行
                await self._execute_action(
                    position, action, current_price
                )
            except Exception as e:
                logger.error(
                    "ポジション管理エラー(ticket=%d): %s",
                    position.ticket, e,
                )

        self._cached_positions = cache_list

    async def _execute_action(
        self, position, action, current_price: float = 0.0
    ) -> None:
        """管理アクション実行

        Args:
            position: ポジションエンティティ
            action: ManagementAction
            current_price: 現在価格（SL検証用）
        """
        if action.action_type == ManagementActionType.HOLD:
            return

        if action.action_type == ManagementActionType.UPDATE_SL:
            if action.new_sl is not None:
                # SL値のバリデーション: 現在価格と同方向か確認
                # BUYのSLは現在価格より下、SELLのSLは現在価格より上
                sl_valid = True
                if current_price > 0:
                    if position.signal_type == SignalType.BUY:
                        sl_valid = action.new_sl < current_price
                    else:
                        sl_valid = action.new_sl > current_price
                if not sl_valid:
                    logger.warning(
                        "SL値が無効（価格と逆側）: ticket=%d"
                        " SL=%.3f price=%.3f スキップ",
                        position.ticket,
                        action.new_sl,
                        current_price,
                    )
                else:
                    result = await self._executor.modify_position_async(
                        position, stop_loss=action.new_sl
                    )
                    if result.success:
                        logger.info(
                            "SL更新: ticket=%d → %.3f (%s)",
                            position.ticket,
                            action.new_sl,
                            action.reason,
                        )

        elif action.action_type == ManagementActionType.PARTIAL_CLOSE:
            close_vol = round(
                position.volume * action.close_ratio, 2
            )
            if close_vol > 0:
                result = await self._executor.close_partial_async(
                    position, close_vol, action.reason
                )
                if result.success:
                    logger.info(
                        "部分決済: ticket=%d %.2f lots (%s)",
                        position.ticket, close_vol, action.reason,
                    )
                    # SL変更もあれば実行（バリデーション付き）
                    if action.new_sl is not None:
                        _sl_ok = True
                        if current_price > 0:
                            if position.signal_type == SignalType.BUY:
                                _sl_ok = action.new_sl < current_price
                            else:
                                _sl_ok = action.new_sl > current_price
                        if _sl_ok:
                            await self._executor.modify_position_async(
                                position, stop_loss=action.new_sl
                            )

        elif action.action_type == ManagementActionType.FULL_CLOSE:
            result = await self._executor.close_position_async(
                position, action.reason
            )
            if result.success:
                logger.info(
                    "全決済: ticket=%d (%s)",
                    position.ticket, action.reason,
                )
                self._pm.unregister_position(
                    str(position.ticket)
                )

    async def _sync_positions(self) -> None:
        """MT5の既存ポジションとPositionManagerを同期

        エンジン起動時に呼び出される。
        """
        positions = await self._executor.get_open_positions_async(
            self._config.symbol
        )
        if not positions:
            logger.info("同期対象ポジションなし")
            return

        logger.info(
            "%d件のポジションを同期", len(positions)
        )
        for pos in positions:
            # PMに未登録なら簡易登録
            pos_id = str(pos.ticket)
            if self._pm.get_position(pos_id) is None:
                from autotrader.decision.unified.mode_selector import (
                    TradingPlan,
                )
                from autotrader.core.enums import TradingStrategyMode

                plan = TradingPlan(
                    mode=TradingStrategyMode.DAY_TRADE,
                    primary_tf="M15",
                    entry_tf="M15",
                    confirm_tfs=["H1", "H4"],
                    manage_tf="M15",
                    max_holding_bars=32,
                    tp_sl_ratio_range=(1.1, 1.4),
                    selection_reason="synced_at_startup",
                )

                self._pm.register_position(
                    position_id=pos_id,
                    direction=pos.signal_type,
                    entry_price=pos.entry_price,
                    entry_time=pos.opened_at,
                    sl=pos.stop_loss or pos.entry_price,
                    tp=pos.take_profit or pos.entry_price,
                    volume=pos.volume,
                    plan=plan,
                )
                logger.info(
                    "ポジション同期: ticket=%d %s %.2f lots",
                    pos.ticket,
                    pos.signal_type.value,
                    pos.volume,
                )
