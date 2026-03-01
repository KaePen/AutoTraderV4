"""ライブトレーディングエンジン

asyncioメインループで定期的にMT5データを取得し、
既存の意思決定層（TradeBot, PositionManager, PositionSizer）で
シグナル生成・ポジション管理を実行する。

Facadeパターンにより5つのサービスに処理を委譲する:
- FundamentalService: ファンダメンタルデータ収集・分析
- MarketDataService: ローソク足・指標計算
- BroadcastService: WebSocket配信
- TradeExecutorService: エントリー実行
- PositionSyncService: ポジション同期・管理
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time as _time
from datetime import UTC, datetime

import pandas as pd

from autotrader.adapters.mt5.connection import (
    MT5ConnectionManager,
)
from autotrader.adapters.mt5.data_provider import (
    MT5DataProvider,
)
from autotrader.adapters.mt5.trade_executor import (
    MT5TradeExecutor,
)
from autotrader.core.entities import AccountInfo, Signal
from autotrader.core.enums import SignalType
from autotrader.core.event_bus import event_bus
from autotrader.core.exceptions import ValidationError
from autotrader.decision.unified.config import (
    UnifiedBotConfig,
)
from autotrader.decision.unified.position_manager import (
    PositionManager,
    PositionManagerConfig,
)
from autotrader.decision.unified.position_sizer import (
    PositionSizer,
)
from autotrader.decision.unified.signal_consolidator import (
    ConsolidatedSignal,
)
from autotrader.decision.unified.trade_bot import (
    UnifiedTradeBot,
)
from autotrader.live.broadcast_service import (
    BroadcastService,
)
from autotrader.live.config import LiveTradingConfig
from autotrader.live.fundamental_service import (
    FundamentalService,
)
from autotrader.live.market_data_service import (
    MarketDataService,
)
from autotrader.live.position_sync_service import (
    PositionSyncService,
    _mt5_reason_to_exit_reason,  # noqa: F401
)
from autotrader.live.tick_entry_optimizer import (
    TickEntryOptimizer,
)
from autotrader.live.trade_executor_service import (
    TradeExecutorService,
    build_sizer_config,
    get_pip_size,
    get_pip_value,
)

logger = logging.getLogger(__name__)


class LiveTradingEngine:
    """ライブトレーディングエンジン（Facade）

    5つのサービスに処理を委譲し、メインループで
    オーケストレーションを行う。

    Attributes:
        _config: ライブトレーディング設定
        _conn: MT5接続マネージャ
        _data_provider: MT5データプロバイダ
        _executor: MT5トレード実行
        _bot: 統合トレードボット
        _pm: ポジションマネージャ
        _running: エンジン実行中フラグ
    """

    def __init__(
        self,
        config: LiveTradingConfig,
        shared_conn: MT5ConnectionManager | None = None,
        shared_data_provider: MT5DataProvider | None = None,
        shared_fundamental_collector=None,
        shared_rss_collector=None,
    ) -> None:
        """初期化

        Args:
            config: ライブトレーディング設定
            shared_conn: 共有MT5接続（マルチエンジン時）
            shared_data_provider: 共有データプロバイダ
            shared_fundamental_collector: 共有ファンダメンタル
                コレクター（EngineManager経由）
            shared_rss_collector: 共有RSSコレクター
                （EngineManager経由）
        """
        self._config = config
        self._conn = shared_conn or MT5ConnectionManager(config.mt5_config)
        self._data_provider = shared_data_provider or MT5DataProvider(
            self._conn
        )
        # 共有接続の場合、接続/切断はEngineManagerが管理
        self._owns_connection = shared_conn is None
        self._executor = MT5TradeExecutor(
            conn=self._conn,
            magic=config.mt5_config.magic_number,
            deviation=config.mt5_config.deviation,
            symbol=config.symbol,
        )
        self._bot = UnifiedTradeBot(config.bot_config)
        self._pm = PositionManager()
        self._sizer = PositionSizer(
            build_sizer_config(
                config.bot_config,
                config.symbol,
            )
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
        # ランタイムで切替可能なアクティブシンボル
        self._active_symbol = config.symbol

        # ティックエントリー最適化
        self._tick_optimizer = TickEntryOptimizer(
            config=config.tick_entry_config,
            data_provider=self._data_provider,
            symbol=config.symbol,
        )

        # シンボル別デモモード状態（UI表示用）
        self._symbol_demo_mode: dict[str, bool] = {}
        # フル処理（ローソク足+指標+シグナル）最終実行時刻
        self._last_full_tick_time: float = 0.0

        # ===== サービス初期化 =====

        # ファンダメンタルサービス
        self._fundamental_svc = FundamentalService(
            config=config.fundamental_config,
            symbol=config.symbol,
            data_provider=self._data_provider,
            shared_fundamental_collector=(shared_fundamental_collector),
            shared_rss_collector=shared_rss_collector,
        )

        # マーケットデータサービス
        self._market_data_svc = MarketDataService(
            data_provider=self._data_provider,
            bot=self._bot,
        )

        # ブロードキャストサービス
        self._broadcast_svc = BroadcastService()

        # トレード実行サービス
        self._trade_executor_svc = TradeExecutorService(
            executor=self._executor,
            sizer=self._sizer,
            pm=self._pm,
            bot=self._bot,
            tick_optimizer=self._tick_optimizer,
            data_provider=self._data_provider,
        )

        # ポジション同期サービス
        self._position_sync_svc = PositionSyncService(
            data_provider=self._data_provider,
            executor=self._executor,
            pm=self._pm,
            bot=self._bot,
        )

    # ===== プロパティ =====

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
    def active_symbol(self) -> str:
        """現在のアクティブシンボル"""
        return self._active_symbol

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
        return getattr(self._bot.config, "demo_mode", False)

    @property
    def symbol_auto_trade_states(self) -> dict[str, bool]:
        """シンボル別自動取引状態"""
        return {self._active_symbol: self._enable_auto_trade}

    @property
    def symbol_demo_mode_states(self) -> dict[str, bool]:
        """シンボル別デモモード状態"""
        if self._symbol_demo_mode:
            return dict(self._symbol_demo_mode)
        return {self._active_symbol: self.demo_mode_enabled}

    @property
    def trade_history(self) -> list[dict]:
        """クローズ済みトレード履歴"""
        return self._position_sync_svc.closed_trades

    @property
    def cached_positions(self) -> list[dict]:
        """キャッシュ済みオープンポジション（UI表示用）"""
        return self._position_sync_svc.cached_positions

    @property
    def signal_history(self) -> list[Signal]:
        """シグナル履歴"""
        return self._signal_history

    @property
    def config_symbol(self) -> str:
        """設定シンボル（外部参照用）"""
        return self._config.symbol

    @property
    def fundamental_collector(self) -> object | None:
        """ファンダメンタルデータコレクター（外部参照用）"""
        return self._fundamental_svc.fundamental_collector

    @property
    def rss_collector(self) -> object | None:
        """RSSコレクター（外部参照用）"""
        return self._fundamental_svc.rss_collector

    # ===== 公開メソッド =====

    async def change_symbol(self, symbol: str) -> None:
        """アクティブシンボルを変更しコンポーネントを再初期化

        Args:
            symbol: 新しい通貨ペアシンボル

        Raises:
            ValidationError: シンボルが空または不正な場合
        """
        if not symbol or not symbol.strip():
            raise ValidationError("symbolは空にできません")
        symbol = symbol.strip().upper()
        if symbol == self._active_symbol:
            return

        old = self._active_symbol
        logger.info("シンボル変更: %s → %s", old, symbol)

        # 1. ティック監視キャンセル
        if self._tick_optimizer.is_active:
            self._tick_optimizer.cancel_monitoring("シンボル変更")

        # 2. アクティブシンボル更新
        self._active_symbol = symbol
        self._fundamental_svc.symbol = symbol

        # 3. PositionSizer再構築
        self._sizer = PositionSizer(
            build_sizer_config(self._bot.config, symbol)
        )
        self._trade_executor_svc.sizer = self._sizer

        # 4. TickEntryOptimizer再構築
        self._tick_optimizer = TickEntryOptimizer(
            config=self._config.tick_entry_config,
            data_provider=self._data_provider,
            symbol=symbol,
        )
        self._trade_executor_svc.tick_optimizer = self._tick_optimizer

        # 5. MT5TradeExecutorのデフォルトシンボル更新
        self._executor._symbol = symbol

        # 6. キャッシュリセット
        self._last_signal = None
        self._last_analysis = None
        self._market_data_svc.last_tick_data = None
        self._market_data_svc.last_mt5_tick_ms = 0
        self._last_full_tick_time = 0.0
        self._position_sync_svc.cached_positions = []
        self._position_sync_svc.open_trades = {}

        # 7. エンジン実行中なら過去データ再読込+ポジション同期
        if self._running:
            await self._market_data_svc.load_historical_data(
                symbol,
                self._config.candle_lookback,
            )
            await self._position_sync_svc.sync_positions(
                symbol,
            )

        logger.info("シンボル変更完了: %s", symbol)

    async def set_symbol_auto_trade(
        self,
        symbol: str,
        enable: bool,
    ) -> None:
        """シンボルごとの自動取引ON/OFF設定

        Args:
            symbol: 通貨ペアシンボル
            enable: 自動取引を有効にするか
        """
        # シンボルが異なる場合は切替
        if symbol != self._active_symbol:
            await self.change_symbol(symbol)

        self._enable_auto_trade = enable
        logger.info(
            "シンボル自動取引: %s %s",
            symbol,
            "ON" if enable else "OFF",
        )

    def set_symbol_demo_mode(
        self,
        symbol: str,
        enable: bool,
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

    def get_current_entry_threshold(
        self,
        mode_str: str | None = None,
    ) -> float | None:
        """現在のbot設定からエントリー閾値を取得

        Args:
            mode_str: 互換性のため残存（未使用）

        Returns:
            float | None: 現在の閾値。取得不可の場合はNone
        """
        if not self._bot:
            return None
        try:
            cfg = self._bot.config
            if cfg.demo_mode:
                return cfg.demo_consensus_threshold
            return cfg.consensus_threshold
        except AttributeError:
            return None

    def update_bot_config(
        self,
        new_config: UnifiedBotConfig,
    ) -> None:
        """Botの設定を動的に更新する

        Args:
            new_config: 新しいUnifiedBotConfig
        """
        self._bot.config = new_config
        self._bot._init_new_components()
        self._sizer = PositionSizer(
            build_sizer_config(
                new_config,
                self._active_symbol,
            )
        )
        self._trade_executor_svc.sizer = self._sizer
        logger.info(
            "BotConfig更新完了 demo_mode=%s",
            new_config.demo_mode,
        )

    def update_pm_config(
        self,
        new_config: PositionManagerConfig,
    ) -> None:
        """PositionManagerの設定を動的に更新する

        Args:
            new_config: 新しいPositionManagerConfig
        """
        self._pm.config = new_config
        logger.info("PositionManagerConfig更新完了")

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
    ):
        """ローソク足データ取得（public API）"""
        return await self._market_data_svc.get_candles(
            symbol,
            timeframe,
            limit,
        )

    async def get_candles_before(
        self,
        symbol: str,
        timeframe: str,
        end_time: datetime,
        limit: int,
    ):
        """指定時刻より前のローソク足データ取得"""
        return await self._market_data_svc.get_candles_before(
            symbol,
            timeframe,
            end_time,
            limit,
        )

    def get_indicators(
        self,
        timeframe: str,
    ) -> dict | None:
        """計算済み指標取得（public API）"""
        return self._market_data_svc.get_indicators(timeframe)

    def get_news_for_symbol(
        self,
        symbol: str,
        limit: int = 50,
    ) -> list:
        """指定シンボルに関連するニュースをフィルタリング"""
        return self._fundamental_svc.get_news_for_symbol(symbol, limit)

    async def sync_positions_on_toggle(self) -> None:
        """自動取引ON切替時のポジション同期"""
        await self._position_sync_svc.sync_positions_on_toggle(
            self._active_symbol,
            self._running,
        )

    # ===== 後方互換用の静的メソッド =====

    @staticmethod
    def _build_sizer_config(
        bot_config: UnifiedBotConfig,
        symbol: str = "",
    ):
        """UnifiedBotConfigからPositionSizerConfigを生成"""
        return build_sizer_config(bot_config, symbol)

    @staticmethod
    def _get_pip_size(symbol: str) -> float:
        """通貨ペアのpipサイズを返す"""
        return get_pip_size(symbol)

    @staticmethod
    def _get_pip_value(symbol: str) -> float:
        """通貨ペアの1lot/1pipあたりの価値を返す"""
        return get_pip_value(symbol)

    @staticmethod
    def _blend_news_sentiment(
        ctx,
        sentiment: float,
        weight: float = 0.15,
    ):
        """ニュースセンチメントをブレンド"""
        return FundamentalService.blend_news_sentiment(
            ctx,
            sentiment,
            weight,
        )

    # ===== エンジンライフサイクル =====

    async def start(self) -> None:
        """エンジン開始"""
        if self._running:
            logger.warning("エンジンは既に実行中です")
            return

        logger.info("ライブトレーディングエンジン開始")

        # MT5接続（共有接続時はスキップ）
        if self._owns_connection:
            await self._conn.connect()

        # 過去データ読込
        await self._market_data_svc.load_historical_data(
            self._active_symbol,
            self._config.candle_lookback,
        )

        # 口座情報取得
        self._account_info = await self._data_provider.get_account_info()

        # 既存ポジション同期
        await self._position_sync_svc.sync_positions(
            self._active_symbol,
        )

        # ファンダメンタル収集タスク起動
        await self._fundamental_svc.start_tasks()

        # メインループ開始
        self._running = True
        self._task = asyncio.create_task(self._main_loop())
        logger.info(
            "エンジン起動完了: symbol=%s interval=%.0fs auto=%s",
            self._active_symbol,
            self._config.check_interval_sec,
            self._enable_auto_trade,
        )

    async def stop(self) -> None:
        """エンジン停止"""
        await self._fundamental_svc.stop_tasks()
        self._running = False

        # ティック監視中ならキャンセル
        if self._tick_optimizer.is_active:
            self._tick_optimizer.cancel_monitoring("エンジン停止")

        if self._task:
            self._task.cancel()
            with contextlib.suppress(
                asyncio.CancelledError,
            ):
                await self._task
            self._task = None

        # 共有接続時はdisconnectをスキップ
        if self._owns_connection:
            await self._conn.disconnect()
        logger.info("ライブトレーディングエンジン停止")

    # ===== メインループ =====

    async def _main_loop(self) -> None:
        """メインループ

        - 0.1秒毎: MT5 tick変化検出 → 価格をbroadcast
        - check_interval_sec毎: ローソク足+指標+シグナル+
          ティックエントリ評価 → 全UI更新
        """
        while self._running:
            try:
                now = _time.monotonic()
                if (
                    now - self._last_full_tick_time
                    >= self._config.check_interval_sec
                ):
                    await self._tick()
                    self._last_full_tick_time = now
                else:
                    await self._market_data_svc.tick_price_update(
                        self._active_symbol
                    )
            except Exception as e:
                logger.error(
                    "ティック処理エラー: %s",
                    e,
                    exc_info=True,
                )
            await asyncio.sleep(0.1)

    async def _tick(self) -> None:
        """1ティック分の処理

        口座情報→ローソク足→ポジション管理→
        シグナル生成→エントリー判定
        """
        # 1. 口座情報更新
        self._account_info = await self._data_provider.get_account_info()

        # 2. 最新ローソク足データ取得・設定
        await self._market_data_svc.update_market_data(self._active_symbol)

        # 3. ポジション管理（シグナル生成前に実行）
        await self._position_sync_svc.manage_positions(
            active_symbol=self._active_symbol,
            enable_auto_trade=self._enable_auto_trade,
            last_signal=self._last_signal,
        )

        # 4. ファンダメンタルコンテキスト取得
        fundamental_ctx = self._fundamental_svc.get_fundamental_context(
            self._active_symbol
        )
        if fundamental_ctx == "SKIP":
            return

        # 5. ニュースセンチメントをブレンド
        fundamental_ctx = await self._fundamental_svc.process_news_sentiment(
            self._active_symbol,
            fundamental_ctx,
        )

        # 6. シグナル生成
        current_time = pd.Timestamp.now(tz="UTC")
        signal = self._bot.generate_signal(
            current_time,
            fundamental_ctx=fundamental_ctx,
        )

        # 分析結果を保存
        if signal and signal.scores:
            self._last_analysis = signal
        self._last_tick_time = datetime.now(UTC)

        if signal and signal.direction != SignalType.HOLD:
            self._last_signal = signal
            converted = self._trade_executor_svc.consolidated_to_signal(
                signal,
                self._active_symbol,
            )
            self._signal_history.append(converted)
            # 履歴上限
            if len(self._signal_history) > 200:
                self._signal_history = self._signal_history[-200:]
            logger.info(
                "シグナル生成: %s conf=%.2f",
                signal.direction.value,
                signal.confidence,
            )

            # EventBus経由でシグナルブロードキャスト
            event_bus.publish_nowait(
                "signal.generated",
                {
                    "signal_id": converted.signal_id,
                    "symbol": converted.symbol,
                    "timeframe": converted.timeframe,
                    "signal_type": (converted.signal_type.value),
                    "confidence": converted.confidence,
                    "confidence_level": (converted.confidence_level.value),
                    "stop_loss": converted.stop_loss,
                    "take_profit": converted.take_profit,
                    "reasoning": converted.reasoning,
                    "created_at": (converted.created_at.isoformat()),
                },
            )

            # エントリー判定
            if self._enable_auto_trade:
                entry_signal = self._trade_executor_svc.consolidated_to_signal(
                    signal,
                    self._active_symbol,
                )
                if self._trade_executor_svc.should_use_tick_optimizer():
                    self._tick_optimizer.start_monitoring(entry_signal)
                else:
                    await self._trade_executor_svc.execute_entry(
                        signal=entry_signal,
                        active_symbol=(self._active_symbol),
                        account_info=(self._account_info),
                        open_trades=(self._position_sync_svc.open_trades),
                        cached_positions=(
                            self._position_sync_svc.cached_positions
                        ),
                        save_position_state=(
                            self._position_sync_svc._save_position_state
                        ),
                    )

        # ティック監視ポーリング
        if self._tick_optimizer.is_active:
            result = await self._tick_optimizer.poll_tick()
            if result is not None:
                if result.should_execute:
                    pending = self._tick_optimizer.pending_signal
                    if pending is not None:
                        await self._trade_executor_svc.execute_entry(
                            signal=pending,
                            active_symbol=(self._active_symbol),
                            account_info=(self._account_info),
                            open_trades=(self._position_sync_svc.open_trades),
                            cached_positions=(
                                self._position_sync_svc.cached_positions
                            ),
                            save_position_state=(
                                self._position_sync_svc._save_position_state
                            ),
                        )
                self._tick_optimizer.reset()

        # 7. tick完了: 全UIデータをWebSocketで一括配信
        payload = BroadcastService.build_tick_payload(
            config_symbol=self._config.symbol,
            last_analysis=self._last_analysis,
            last_tick_time=self._last_tick_time,
            running=self._running,
            connected=self.connected,
            enable_auto_trade=self._enable_auto_trade,
            demo_mode_enabled=self.demo_mode_enabled,
            account_info=self._account_info,
            cached_positions=(self._position_sync_svc.cached_positions),
            signal_history=self._signal_history,
            bot=self._bot,
            get_current_entry_threshold=(self.get_current_entry_threshold),
            extract_indicators=(self._market_data_svc.extract_indicators),
        )
        asyncio.create_task(
            self._broadcast_svc.broadcast_tick_update(
                payload,
            )
        )

    # ===== 後方互換用の委譲メソッド =====
    # テストやサブクラスで直接呼ばれるメソッド群

    async def _load_historical_data(self) -> None:
        """過去データ読込（後方互換）"""
        await self._market_data_svc.load_historical_data(
            self._active_symbol,
            self._config.candle_lookback,
        )

    async def _update_market_data(self) -> None:
        """ローソク足データ更新（後方互換）"""
        await self._market_data_svc.update_market_data(
            self._active_symbol,
        )

    def _calc_indicators(
        self,
        data: dict[str, pd.DataFrame],
    ) -> dict[str, pd.DataFrame]:
        """指標計算（後方互換）"""
        return self._market_data_svc.calc_indicators(data)

    async def _tick_price_update(self) -> None:
        """軽量tick処理（後方互換）"""
        await self._market_data_svc.tick_price_update(
            self._active_symbol,
        )

    def _extract_indicators(
        self,
        timeframe: str,
    ) -> dict:
        """指標抽出（後方互換）"""
        return self._market_data_svc.extract_indicators(
            timeframe,
        )

    def _should_use_tick_optimizer(self) -> bool:
        """ティック最適化判定（後方互換）"""
        return self._trade_executor_svc.should_use_tick_optimizer()

    def _consolidated_to_signal(
        self,
        cs: ConsolidatedSignal,
    ) -> Signal:
        """シグナル変換（後方互換）"""
        return self._trade_executor_svc.consolidated_to_signal(
            cs,
            self._active_symbol,
        )

    async def _execute_entry(
        self,
        signal: Signal,
    ) -> None:
        """エントリー実行（後方互換）"""
        await self._trade_executor_svc.execute_entry(
            signal=signal,
            active_symbol=self._active_symbol,
            account_info=self._account_info,
            open_trades=(self._position_sync_svc.open_trades),
            cached_positions=(self._position_sync_svc.cached_positions),
            save_position_state=(self._position_sync_svc._save_position_state),
        )

    async def _manage_positions(self) -> None:
        """ポジション管理（後方互換）"""
        await self._position_sync_svc.manage_positions(
            active_symbol=self._active_symbol,
            enable_auto_trade=self._enable_auto_trade,
            last_signal=self._last_signal,
        )

    async def _sync_positions(self) -> None:
        """ポジション同期（後方互換）"""
        await self._position_sync_svc.sync_positions(
            self._active_symbol,
        )

    async def _close_ghost_db_records(
        self,
        active_tickets: set[int],
    ) -> None:
        """ゴーストレコード掃除（後方互換）"""
        await self._position_sync_svc.close_ghost_db_records(
            active_tickets,
            self._active_symbol,
        )

    def _fetch_ghost_records(
        self,
        active_tickets: set[int],
    ) -> list[tuple[int, str]]:
        """ゴーストレコード取得（後方互換）"""
        return self._position_sync_svc._fetch_ghost_records(
            active_tickets,
            self._active_symbol,
        )

    def _apply_ghost_updates(
        self,
        updates: list[dict],
    ) -> None:
        """ゴースト更新適用（後方互換）"""
        self._position_sync_svc._apply_ghost_updates(
            updates,
        )

    def _restore_open_trades_from_db(
        self,
        tickets: list[int],
    ) -> None:
        """トレードID復元（後方互換）"""
        self._position_sync_svc._restore_open_trades_from_db(
            tickets,
        )

    async def _handle_external_close(
        self,
        ticket: int,
    ) -> None:
        """外部決済処理（後方互換）"""
        await self._position_sync_svc._handle_external_close(
            ticket,
            self._active_symbol,
        )

    def _write_close_to_db(
        self,
        ticket: int,
        current_price: float,
        action_reason: str,
        profit_loss: float = 0.0,
    ) -> None:
        """決済DB記録（後方互換）"""
        self._position_sync_svc._write_close_to_db(
            ticket,
            current_price,
            action_reason,
            profit_loss,
            self._active_symbol,
        )

    def _write_entry_to_db(
        self,
        ticket: int,
        signal: Signal,
        lot: float,
        entry_tick: dict | None,
    ) -> str | None:
        """エントリーDB記録（後方互換）"""
        return self._trade_executor_svc._write_entry_to_db(
            ticket,
            signal,
            lot,
            entry_tick,
        )

    async def _register_new_position(
        self,
        ticket: int,
        signal: Signal,
        volume: float,
        entry_tick: dict | None = None,
    ) -> None:
        """ポジション登録（後方互換）"""
        await self._trade_executor_svc._register_new_position(
            ticket,
            signal,
            volume,
            entry_tick,
            self._position_sync_svc._save_position_state,
        )

    def _load_position_states(self) -> dict[str, dict]:
        """管理状態ロード（後方互換）"""
        return self._position_sync_svc._load_position_states()

    def _save_position_state(
        self,
        position_id: str,
    ) -> None:
        """管理状態保存（後方互換）"""
        self._position_sync_svc._save_position_state(
            position_id,
        )

    def _delete_position_state(
        self,
        position_id: str,
    ) -> None:
        """管理状態削除（後方互換）"""
        self._position_sync_svc._delete_position_state(
            position_id,
        )

    def _cleanup_stale_states(
        self,
        active_ids: set[str],
    ) -> None:
        """陳腐化状態クリーンアップ（後方互換）"""
        self._position_sync_svc._cleanup_stale_states(
            active_ids,
        )

    def _init_fundamental(self, cfg) -> None:
        """ファンダメンタル初期化（後方互換）"""
        self._fundamental_svc.init_fundamental(cfg)

    def _init_calendar_only(self) -> None:
        """カレンダー軽量初期化（後方互換）"""
        self._fundamental_svc.init_calendar_only()

    async def _start_fundamental_tasks(self) -> None:
        """ファンダメンタルタスク起動（後方互換）"""
        await self._fundamental_svc.start_tasks()

    async def _stop_fundamental_tasks(self) -> None:
        """ファンダメンタルタスク停止（後方互換）"""
        await self._fundamental_svc.stop_tasks()

    async def _on_rss_news(self, news_item) -> None:
        """RSSニュース受信（後方互換）"""
        await self._fundamental_svc.on_rss_news(news_item)

    async def _broadcast_tick_update(self) -> None:
        """tick配信（後方互換）"""
        payload = BroadcastService.build_tick_payload(
            config_symbol=self._config.symbol,
            last_analysis=self._last_analysis,
            last_tick_time=self._last_tick_time,
            running=self._running,
            connected=self.connected,
            enable_auto_trade=self._enable_auto_trade,
            demo_mode_enabled=self.demo_mode_enabled,
            account_info=self._account_info,
            cached_positions=(self._position_sync_svc.cached_positions),
            signal_history=self._signal_history,
            bot=self._bot,
            get_current_entry_threshold=(self.get_current_entry_threshold),
            extract_indicators=(self._market_data_svc.extract_indicators),
        )
        await self._broadcast_svc.broadcast_tick_update(
            payload,
        )

    def _build_tick_payload(self) -> dict:
        """ペイロード構築（後方互換）"""
        return BroadcastService.build_tick_payload(
            config_symbol=self._config.symbol,
            last_analysis=self._last_analysis,
            last_tick_time=self._last_tick_time,
            running=self._running,
            connected=self.connected,
            enable_auto_trade=self._enable_auto_trade,
            demo_mode_enabled=self.demo_mode_enabled,
            account_info=self._account_info,
            cached_positions=(self._position_sync_svc.cached_positions),
            signal_history=self._signal_history,
            bot=self._bot,
            get_current_entry_threshold=(self.get_current_entry_threshold),
            extract_indicators=(self._market_data_svc.extract_indicators),
        )

    async def _run_morning_update(self) -> None:
        """朝の市場観更新（後方互換）"""
        await self._fundamental_svc.run_morning_update(
            self._active_symbol,
        )

    async def _handle_post_event_analysis(
        self,
        event_name: str,
        currency: str,
        actual: float | None,
        forecast: float | None,
        previous: float | None,
        current_price: float,
        price_change: float = 0.0,
    ) -> None:
        """指標後分析（後方互換）"""
        await self._fundamental_svc.handle_post_event_analysis(
            symbol=self._active_symbol,
            event_name=event_name,
            currency=currency,
            actual=actual,
            forecast=forecast,
            previous=previous,
            current_price=current_price,
            price_change=price_change,
        )

    # ===== 後方互換用プロパティ（内部属性アクセス） =====
    # テストやengine_managerが直接_attributeにアクセスするケース

    @property
    def _cached_positions(self) -> list[dict]:
        """キャッシュポジション（後方互換）"""
        return self._position_sync_svc.cached_positions

    @_cached_positions.setter
    def _cached_positions(
        self,
        value: list[dict],
    ) -> None:
        self._position_sync_svc.cached_positions = value

    @property
    def _open_trades(self) -> dict[int, str]:
        """オープントレード辞書（後方互換）"""
        return self._position_sync_svc.open_trades

    @_open_trades.setter
    def _open_trades(
        self,
        value: dict[int, str],
    ) -> None:
        self._position_sync_svc.open_trades = value

    @property
    def _closed_trades(self) -> list[dict]:
        """クローズ済みトレード（後方互換）"""
        return self._position_sync_svc.closed_trades

    @_closed_trades.setter
    def _closed_trades(
        self,
        value: list[dict],
    ) -> None:
        self._position_sync_svc.closed_trades = value

    @property
    def _fundamental_memory(self):
        """ファンダメンタルメモリ（後方互換）"""
        return self._fundamental_svc.fundamental_memory

    @_fundamental_memory.setter
    def _fundamental_memory(self, value) -> None:
        self._fundamental_svc._fundamental_memory = value

    @property
    def _fundamental_collector(self):
        """ファンダメンタルコレクター（後方互換）"""
        return self._fundamental_svc.fundamental_collector

    @_fundamental_collector.setter
    def _fundamental_collector(self, value) -> None:
        self._fundamental_svc._fundamental_collector = value

    @property
    def _rss_collector(self):
        """RSSコレクター（後方互換）"""
        return self._fundamental_svc.rss_collector

    @_rss_collector.setter
    def _rss_collector(self, value) -> None:
        self._fundamental_svc._rss_collector = value

    @property
    def _morning_update_done_date(self):
        """朝の更新完了日（後方互換）"""
        return self._fundamental_svc._morning_update_done_date

    @_morning_update_done_date.setter
    def _morning_update_done_date(self, value) -> None:
        self._fundamental_svc._morning_update_done_date = value

    @property
    def _news_buffer(self) -> list:
        """ニュースバッファ（後方互換）"""
        return self._fundamental_svc.news_buffer

    @_news_buffer.setter
    def _news_buffer(self, value: list) -> None:
        self._fundamental_svc.news_buffer = value

    @property
    def _news_analyzer(self):
        """ニュースアナライザー（後方互換）"""
        return self._fundamental_svc.news_analyzer

    @_news_analyzer.setter
    def _news_analyzer(self, value) -> None:
        self._fundamental_svc._news_analyzer = value

    @property
    def _keyword_scorer(self):
        """キーワードスコアラー（後方互換）"""
        return self._fundamental_svc.keyword_scorer

    @_keyword_scorer.setter
    def _keyword_scorer(self, value) -> None:
        self._fundamental_svc._keyword_scorer = value

    @property
    def _sentiment_store(self):
        """センチメントストア（後方互換）"""
        return self._fundamental_svc.sentiment_store

    @_sentiment_store.setter
    def _sentiment_store(self, value) -> None:
        self._fundamental_svc._sentiment_store = value

    @property
    def _shared_fundamental_collector(self):
        """共有ファンダメンタルコレクター（後方互換）"""
        return self._fundamental_svc._shared_fundamental_collector

    @_shared_fundamental_collector.setter
    def _shared_fundamental_collector(
        self,
        value,
    ) -> None:
        self._fundamental_svc._shared_fundamental_collector = value

    @property
    def _shared_rss_collector(self):
        """共有RSSコレクター（後方互換）"""
        return self._fundamental_svc._shared_rss_collector

    @_shared_rss_collector.setter
    def _shared_rss_collector(self, value) -> None:
        self._fundamental_svc._shared_rss_collector = value

    @property
    def _owns_collectors(self) -> bool:
        """コレクター所有フラグ（後方互換）"""
        return self._fundamental_svc.owns_collectors

    @_owns_collectors.setter
    def _owns_collectors(self, value: bool) -> None:
        self._fundamental_svc._owns_collectors = value

    @property
    def _last_tick_data(self) -> dict | None:
        """直近tickデータ（後方互換）"""
        return self._market_data_svc.last_tick_data

    @_last_tick_data.setter
    def _last_tick_data(
        self,
        value: dict | None,
    ) -> None:
        self._market_data_svc.last_tick_data = value

    @property
    def _last_mt5_tick_ms(self) -> int:
        """最終tick ms（後方互換）"""
        return self._market_data_svc.last_mt5_tick_ms

    @_last_mt5_tick_ms.setter
    def _last_mt5_tick_ms(self, value: int) -> None:
        self._market_data_svc.last_mt5_tick_ms = value
