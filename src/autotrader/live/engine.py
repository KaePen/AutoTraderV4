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
)
from autotrader.decision.unified.position_sizer import PositionSizer
from autotrader.decision.unified.trade_bot import UnifiedTradeBot
from autotrader.live.config import LiveTradingConfig
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
        self._sizer = PositionSizer(config.bot_config)
        self._running = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._account_info: AccountInfo | None = None

        # コールバック（WebSocket連携用）
        self.on_signal: asyncio.Event | None = None
        self.on_position_update: asyncio.Event | None = None
        self.on_account_update: asyncio.Event | None = None

        # 最新データキャッシュ
        self._last_signal: Signal | None = None
        self._enable_auto_trade = config.enable_auto_trade

        # ティックエントリー最適化
        self._tick_optimizer = TickEntryOptimizer(
            config=config.tick_entry_config,
            data_provider=self._data_provider,
            symbol=config.symbol,
        )

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
        if signal and signal.direction != SignalType.HOLD:
            self._last_signal = signal
            logger.info(
                "シグナル生成: %s conf=%.2f",
                signal.direction.value,
                signal.confidence,
            )

            # 4. エントリー判定
            if self._enable_auto_trade:
                if self._should_use_tick_optimizer():
                    self._tick_optimizer.start_monitoring(
                        signal
                    )
                else:
                    await self._execute_entry(signal)

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

    async def _load_historical_data(self) -> None:
        """起動時に過去データをTradeBotに供給"""
        symbol = self._config.symbol
        lookback = self._config.candle_lookback
        timeframes = self._bot.timeframes

        logger.info(
            "過去データ読込: %s %d本 x %d時間足",
            symbol, lookback, len(timeframes),
        )

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

            self._bot.set_market_data({tf_str: df})
            logger.info(
                "データ読込完了: %s %s %d本",
                symbol, tf_str, len(df),
            )

    async def _update_market_data(self) -> None:
        """最新ローソク足データを取得してTradeBotに設定"""
        symbol = self._config.symbol
        # 各時間足の最新30本を取得（指標計算に十分な量）
        for tf_str in self._bot.timeframes:
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                continue

            df = await self._data_provider.get_candles_from_pos(
                symbol, tf, 30
            )
            if not df.empty:
                self._bot.set_market_data({tf_str: df})

    def _should_use_tick_optimizer(self) -> bool:
        """ティック最適化を使用すべきか判定

        Returns:
            bool: ティック最適化を使用すべきか
        """
        cfg = self._config.tick_entry_config
        if not cfg.enabled:
            return False

        # デモモードでは無効
        if getattr(self._bot, "demo_mode", False):
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
        positions = await self._executor.get_open_positions_async(
            self._config.symbol
        )
        if positions:
            logger.info(
                "既存ポジションあり(%d)、エントリースキップ",
                len(positions),
            )
            return

        # ロット計算
        if self._account_info is None:
            logger.warning("口座情報なし、エントリースキップ")
            return

        sl_pips = 0.0
        if signal.stop_loss is not None:
            tick = await self._data_provider.get_tick(
                self._config.symbol
            )
            price = float(
                tick.get("ask", 0)
                if signal.signal_type == SignalType.BUY
                else tick.get("bid", 0)
            )
            if price > 0 and signal.stop_loss > 0:
                raw_diff = abs(price - signal.stop_loss)
                if "JPY" in self._config.symbol.upper():
                    sl_pips = raw_diff / 0.01
                else:
                    sl_pips = raw_diff / 0.0001

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

        # MT5発注
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
                    result.ticket, signal_with_lot, lot
                )
            # TradeBotに通知
            self._bot.on_trade_executed(signal)
        else:
            logger.error("エントリー失敗: %s", result.message)

    async def _register_new_position(
        self, ticket: int, signal: Signal, volume: float
    ) -> None:
        """新ポジションをPositionManagerに登録

        Args:
            ticket: MT5チケットID
            signal: トレードシグナル
            volume: ロット数
        """
        from autotrader.decision.unified.mode_selector import (
            TradingPlan,
        )
        from autotrader.core.enums import TradingStrategyMode

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
            entry_price=signal.stop_loss or 0.0,
            entry_time=datetime.now(timezone.utc),
            sl=signal.stop_loss or 0.0,
            tp=signal.take_profit or 0.0,
            volume=volume,
            plan=plan,
        )

    async def _manage_positions(self) -> None:
        """既存ポジションの管理

        PositionManager.evaluateで各ポジションを評価し、
        SL変更・部分決済・全決済をMT5で実行。
        """
        positions = await self._executor.get_open_positions_async(
            self._config.symbol
        )
        if not positions:
            return

        # ATR取得（ポジション管理で使用）
        try:
            latest = await self._data_provider.get_latest_candle_async(
                self._config.symbol, Timeframe.M15
            )
            # ATRは簡易計算（最新の高値-安値）
            atr = float(latest.get("high", 0)) - float(
                latest.get("low", 0)
            )
        except Exception:
            atr = 0.3  # デフォルト（USDJPY: 約30pips）

        # 現在のシグナル方向（反転チェック用）
        current_signal_type = None
        if self._last_signal:
            current_signal_type = self._last_signal.direction

        for position in positions:
            pos_id = str(position.ticket)

            # PM未登録ならスキップ
            managed = self._pm.get_position(pos_id)
            if managed is None:
                continue

            # ティック取得
            tick = await self._data_provider.get_tick(
                position.symbol
            )
            if position.signal_type == SignalType.BUY:
                current_price = float(tick.get("bid", 0))
            else:
                current_price = float(tick.get("ask", 0))

            if current_price <= 0:
                continue

            # ポジション評価
            action = self._pm.evaluate(
                position_id=pos_id,
                current_price=current_price,
                current_time=datetime.now(timezone.utc),
                atr=atr,
                current_signal=current_signal_type,
            )

            # アクション実行
            await self._execute_action(position, action)

    async def _execute_action(self, position, action) -> None:
        """管理アクション実行

        Args:
            position: ポジションエンティティ
            action: ManagementAction
        """
        if action.action_type == ManagementActionType.HOLD:
            return

        if action.action_type == ManagementActionType.UPDATE_SL:
            if action.new_sl is not None:
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
                    # SL変更もあれば実行
                    if action.new_sl is not None:
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
