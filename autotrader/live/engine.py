"""ライブトレーディングエンジン

asyncioメインループで定期的にMT5データを取得し、
既存の意思決定層（TradeBot, PositionManager, PositionSizer）で
シグナル生成・ポジション管理を実行する。
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time as _time
import uuid
from datetime import UTC, datetime, timedelta

import pandas as pd

from autotrader.adapters.mt5.connection import MT5ConnectionManager
from autotrader.adapters.mt5.data_provider import MT5DataProvider
from autotrader.adapters.mt5.exceptions import MT5DataError, MT5Error
from autotrader.adapters.mt5.trade_executor import MT5TradeExecutor
from autotrader.calculator.technical.batch import TechnicalIndicatorBatch
from autotrader.config.trading_params import get_pip_unit, get_pip_value
from autotrader.core.entities import AccountInfo, Signal
from autotrader.core.enums import (
    ExitReason,
    MarketRegime,
    SignalType,
    Timeframe,
)
from autotrader.core.event_bus import get_event_bus
from autotrader.core.exceptions import TradingError, ValidationError
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
from autotrader.decision.unified.signal_consolidator import (
    ConsolidatedSignal,
)
from autotrader.decision.unified.mode_selector import (
    UNIVERSAL_MODE,
    TradingPlan,
)
from autotrader.decision.unified.trade_bot import UnifiedTradeBot
from autotrader.live.config import FundamentalConfig, LiveTradingConfig
from autotrader.live.mt5_utils import mt5_reason_to_exit_reason
from autotrader.live.reload import TradeLogicReloader
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

    # MT5一時切断時の外部決済誤検知防止リトライ上限
    # tick回数ベース（tick間隔 ~1秒 × 30回 ≒ 30秒相当）
    _EXT_CLOSE_MAX_RETRIES: int = 30

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
        # pm_config にライブ用 pip_unit を反映
        _live_pip_unit = get_pip_unit(config.symbol)
        self._pm = PositionManager(
            dataclasses.replace(config.pm_config, pip_unit=_live_pip_unit),
        )
        self._sizer = PositionSizer(
            self._build_sizer_config(config.bot_config, config.symbol)
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

        # キャッシュ済みポジション（UI表示用）
        self._cached_positions: list[dict] = []
        # シンボル別デモモード状態（UI表示用）
        self._symbol_demo_mode: dict[str, bool] = {}
        # チケット→トレードID マッピング（DB記録用）
        self._open_trades: dict[int, str] = {}
        # 外部決済検出リトライカウンター（誤検知防止）
        self._ext_close_retries: dict[int, int] = {}
        # クローズ済みトレード履歴（インメモリ）
        self._closed_trades: list[dict] = []
        # MT5 tick高速ポーリング用（最終tickのms単位時刻）
        self._last_mt5_tick_ms: int = 0
        # 直近tick価格キャッシュ（_tick_price_update→_update_market_dataで共用）
        self._last_tick_data: dict | None = None
        # フル処理（ローソク足+指標+シグナル）最終実行時刻
        self._last_full_tick_time: float = 0.0
        # fire-and-forget タスクの強参照保持（GC防止）
        self._background_tasks: set[asyncio.Task[None]] = set()

        # ファンダメンタル関連（FundamentalConfig.enabled=Trueのみ初期化）
        self._fundamental_memory = None
        self._fundamental_collector = None
        self._morning_update_done_date: datetime | None = None
        # RSSニュース関連
        self._rss_collector = None
        self._news_analyzer = None
        self._news_buffer: list = []
        # 共有コレクター（EngineManager経由）
        self._shared_fundamental_collector = shared_fundamental_collector
        self._shared_rss_collector = shared_rss_collector
        # コレクター所有フラグ（共有時は起動/停止しない）
        self._owns_collectors = shared_fundamental_collector is None
        # キーワードセンチメント分析・永続化（常時有効）
        from autotrader.adapters.fundamental.keyword_sentiment import (
            KeywordSentimentScorer,
        )
        from autotrader.adapters.fundamental.sentiment_store import (
            SentimentStore,
        )

        self._keyword_scorer = KeywordSentimentScorer()
        self._sentiment_store = SentimentStore()
        if config.fundamental_config.enabled:
            self._init_fundamental(config.fundamental_config)
        else:
            # カレンダー＋RSS軽量初期化（DB不要・LLM不要）
            self._init_calendar_only()

        # グローバルポジション/エクスポージャー制限
        # EngineManager.set_global_limit_callbacks() で注入
        self._get_global_position_count = None
        self._get_global_exposure_lot = None
        self._global_max_positions: int = 0
        self._global_max_exposure_lot: float = 0.0
        self._get_jpy_direction_count = None
        self._max_same_direction_jpy: int = 0
        # EngineManager参照（ポートフォリオDD監視用）
        self._engine_manager = None

        # エントリースキップ理由（UI通知用）
        self._last_entry_skip_reason: str | None = None

        # ホットリロード関連
        self._entry_blocked: bool = False
        self._reload_lock: asyncio.Lock = asyncio.Lock()
        self._reload_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._last_reload_at: datetime | None = None
        # プロジェクトルートを engine.py から推定
        _engine_path = __import__("pathlib").Path(__file__).resolve()
        _project_root = _engine_path.parent.parent.parent
        self._reloader = TradeLogicReloader(_project_root)

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
        return self._closed_trades

    @property
    def cached_positions(self) -> list[dict]:
        """キャッシュ済みオープンポジション（UI表示用）"""
        return self._cached_positions

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

        # 3. PositionSizer再構築
        self._sizer = PositionSizer(
            self._build_sizer_config(self._bot.config, symbol)
        )

        # 4. TickEntryOptimizer再構築
        self._tick_optimizer = TickEntryOptimizer(
            config=self._config.tick_entry_config,
            data_provider=self._data_provider,
            symbol=symbol,
        )

        # 5. MT5TradeExecutorのデフォルトシンボル更新
        self._executor._symbol = symbol

        # 6. キャッシュリセット
        self._last_signal = None
        self._last_analysis = None
        self._last_tick_data = None
        self._last_mt5_tick_ms = 0
        self._last_full_tick_time = 0.0
        self._cached_positions = []
        self._open_trades = {}
        self._ext_close_retries = {}

        # 7. エンジン実行中なら過去データ再読込+ポジション同期
        if self._running:
            await self._load_historical_data()
            await self._sync_positions()

        logger.info("シンボル変更完了: %s", symbol)

    async def set_symbol_auto_trade(self, symbol: str, enable: bool) -> None:
        """シンボルごとの自動取引ON/OFF設定

        シンボルが現在と異なる場合はコンポーネントを再初期化する。

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

    def set_symbol_demo_mode(self, symbol: str, enable: bool) -> None:
        """シンボルごとのデモモードON/OFF設定

        Args:
            symbol: 通貨ペアシンボル
            enable: デモモードを有効にするか
        """
        self._symbol_demo_mode[symbol] = enable
        # 自エンジン担当シンボルならBotConfigのdemo_modeも更新
        if symbol == self._active_symbol and self._bot:
            current = self._bot.config
            if getattr(current, "demo_mode", False) != enable:
                new_config = dataclasses.replace(
                    current, demo_mode=enable,
                )
                self.update_bot_config(new_config)
        logger.info(
            "[%s] シンボルデモモード: %s",
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
        """現在のbot設定からエントリー閾値を取得（デモ/ライブ切替即時反映）

        consensus オブジェクトを経由せず bot.config から直接計算するため、
        update_bot_config 直後でも正しい閾値を返す。

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

    def update_bot_config(self, new_config: UnifiedBotConfig) -> None:
        """Botの設定を動的に更新する

        デモ/ライブモード切り替え時やWebUI設定変更時に呼ばれる。
        TradeBot.config と内部コンポーネント（consensus含む）を再構築する。

        Args:
            new_config: 新しいUnifiedBotConfig
        """
        self._bot.config = new_config
        # consensus等のコンポーネントをdemo_modeに合わせて再初期化
        # （デモ時は閾値を大幅に下げてシグナルを活発化）
        self._bot._init_new_components()
        self._sizer = PositionSizer(
            self._build_sizer_config(new_config, self._active_symbol)
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

    def set_global_limit_callbacks(
        self,
        get_global_position_count,
        get_global_exposure_lot,
        global_max_positions: int = 0,
        global_max_exposure_lot: float = 0.0,
        get_jpy_direction_count=None,
        max_same_direction_jpy: int = 0,
    ) -> None:
        """グローバルポジション/エクスポージャー制限を設定

        EngineManagerがエンジン追加時にコールバックを注入する。

        Args:
            get_global_position_count: 全ペア合計ポジション数
                取得コールバック
            get_global_exposure_lot: 全ペア合計ロット数
                取得コールバック
            global_max_positions: 最大ポジション数（0=無制限）
            global_max_exposure_lot: 最大ロット数（0.0=無制限）
            get_jpy_direction_count: JPYペア方向別カウント
                取得コールバック
            max_same_direction_jpy: JPY同方向の最大数
                （0=無制限）
        """
        self._get_global_position_count = (
            get_global_position_count
        )
        self._get_global_exposure_lot = (
            get_global_exposure_lot
        )
        self._global_max_positions = global_max_positions
        self._global_max_exposure_lot = global_max_exposure_lot
        self._get_jpy_direction_count = (
            get_jpy_direction_count
        )
        self._max_same_direction_jpy = max_same_direction_jpy
        logger.info(
            "[%s] グローバル制限設定: "
            "max_pos=%d, max_lot=%.1f, max_dir_jpy=%d",
            self._active_symbol,
            global_max_positions,
            global_max_exposure_lot,
            max_same_direction_jpy,
        )

    @staticmethod
    def _build_sizer_config(
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
            max_risk_pct_absolute=bot_config.max_risk_pct_absolute,
            max_lot_per_trade=bot_config.max_lot_per_trade,
            max_total_exposure_lot=bot_config.max_total_exposure_lot,
            equity_floor_pct=bot_config.equity_floor_pct,
            equity_caution_pct=bot_config.equity_caution_pct,
            slippage_buffer_pips=bot_config.slippage_buffer_pips,
        )

    @staticmethod
    def _get_pip_size(symbol: str) -> float:
        """通貨ペアのpipサイズを返す（JPY系=0.01、その他=0.0001）

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            float: pipサイズ
        """
        return get_pip_unit(symbol)

    @staticmethod
    def _get_pip_value(symbol: str) -> float:
        """通貨ペアの1lot/1pipあたりの価値を返す

        公式: 100,000 × pip_unit × quote_ccy_rate
        プリセット登録済みなら正確値、未登録なら通貨名から推定。

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            float: pip価値（円）
        """
        return get_pip_value(symbol)

    @property
    def signal_history(self) -> list[Signal]:
        """シグナル履歴"""
        return self._signal_history

    async def start(self) -> None:
        """エンジン開始"""
        if self._running:
            logger.warning("エンジンは既に実行中です")
            return

        logger.info("[%s] ライブトレーディングエンジン開始", self._active_symbol)

        # MT5接続（共有接続時はスキップ）
        if self._owns_connection:
            await self._conn.connect()

        # 過去データ読込
        await self._load_historical_data()

        # 口座情報取得
        self._account_info = await self._data_provider.get_account_info()

        # 既存ポジション同期
        await self._sync_positions()

        # ファンダメンタル収集タスク起動
        await self._start_fundamental_tasks()

        # メインループ開始
        self._running = True
        self._task = asyncio.create_task(self._main_loop())

        # ホットリロード変更検知ループ起動
        if self._config.reload_config.enabled:
            self._reload_task = asyncio.create_task(
                self._auto_reload_loop()
            )

        logger.info(
            "エンジン起動完了: symbol=%s interval=%.0fs auto=%s",
            self._active_symbol,
            self._config.check_interval_sec,
            self._enable_auto_trade,
        )

    async def stop(self) -> None:
        """エンジン停止"""
        await self._stop_fundamental_tasks()
        self._running = False

        # ティック監視中ならキャンセル
        if self._tick_optimizer.is_active:
            self._tick_optimizer.cancel_monitoring("エンジン停止")

        # ホットリロードループ停止
        if self._reload_task:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
            self._reload_task = None

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # 共有接続時はdisconnectをスキップ
        if self._owns_connection:
            await self._conn.disconnect()
        logger.info("[%s] ライブトレーディングエンジン停止", self._active_symbol)

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
                    # _tick()の早期リターン（重要指標スキップ等）
                    # に関係なく、最新価格をチャートに配信する
                    await self._publish_cached_price()
                else:
                    await self._tick_price_update()
            except Exception as e:
                logger.error("ティック処理エラー: %s", e, exc_info=True)
            await asyncio.sleep(0.1)

    async def _tick_price_update(self) -> None:
        """軽量tick処理: MT5のbid/askを取得して価格をbroadcast

        ローソク足取得・指標計算を行わない高速版。
        前回と同じtickであればbroadcastをスキップする。
        """
        try:
            tick = await self._data_provider.get_tick_fast(self._active_symbol)
        except MT5DataError:
            return

        if not tick:
            return

        tick_ms = int(tick.get("time_msc", 0))
        if tick_ms <= self._last_mt5_tick_ms:
            return  # 新しいtickなし

        self._last_mt5_tick_ms = tick_ms
        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", 0.0))

        # 1秒サイクルのフル処理で使うためキャッシュ
        self._last_tick_data = tick

        get_event_bus().publish_nowait(
            "price.updated",
            {
                "symbol": self._active_symbol,
                "bid": bid,
                "ask": ask,
                "time_ms": tick_ms,
            },
        )

    async def _tick(self) -> None:
        """1ティック分の処理

        口座情報→ローソク足→ポジション管理→シグナル生成→エントリー判定
        ポジション管理を先行させることで、SL/TP発動等による
        ポジション減少を即座にエントリー判断へ反映する。
        """
        # 1. 口座情報更新
        self._account_info = await self._data_provider.get_account_info()

        # 1.5. ポートフォリオDD監視
        if (
            self._engine_manager is not None
            and self._account_info is not None
        ):
            mgr = self._engine_manager
            mgr.update_portfolio_dd(
                self._account_info.equity,
            )
            # 緊急停止: 全ポジション決済
            if (
                mgr.dd_emergency_active
                and not mgr._emergency_close_done
            ):
                await mgr.emergency_close_all()

        # 2. 最新ローソク足データ取得・設定
        await self._update_market_data()

        # 3. ポジション管理（シグナル生成前に実行）
        # SL/TP発動・手動決済による減少を_cached_positionsへ即時反映し、
        # 同一tick内のエントリー判断で最新ポジション数を使えるようにする。
        await self._manage_positions()

        # [FUNDAMENTAL] ファンダメンタルコンテキスト取得・指標前スキップ
        now_utc = datetime.now(UTC)
        if self._fundamental_memory:
            fundamental_ctx = self._fundamental_memory.get_context_for_llm(
                self._active_symbol, now_utc
            )
            if fundamental_ctx.has_high_impact_within_30min:
                logger.info("[%s] 重要指標直前のためスキップ", self._active_symbol)
                return
        else:
            fundamental_ctx = None
            # SentimentStore からのフォールバック
            persisted = self._sentiment_store.load_latest(
                self._active_symbol,
            )
            if persisted and persisted.score != 0.0:
                from autotrader.adapters.fundamental.schemas import (
                    FundamentalContext,
                )

                fundamental_ctx = FundamentalContext(
                    sentiment_score=persisted.score,
                    direction_bias=(persisted.score * 0.15),
                )

        # [NEWS] ニュースセンチメントをブレンド
        if fundamental_ctx is not None and self._news_analyzer is not None:
            news_items = self.get_news_for_symbol(self._active_symbol)
            if news_items:
                sentiment = await self._news_analyzer.analyze(
                    news_items, self._active_symbol
                )
                fundamental_ctx = self._blend_news_sentiment(
                    fundamental_ctx, sentiment
                )
                # ファイル永続化
                from autotrader.adapters.fundamental.sentiment_store import (
                    SentimentRecord,
                )

                self._sentiment_store.save(
                    self._active_symbol,
                    SentimentRecord(
                        timestamp=datetime.now(
                            UTC,
                        ).isoformat(),
                        score=sentiment,
                        method="llm",
                        confidence=0.7,
                        news_count=len(news_items),
                        top_headlines=[n.title for n in news_items[:3]],
                    ),
                )
                # active_symbolの関連ニュースのみ除去
                base = self._active_symbol[:3].upper()
                quote = self._active_symbol[3:6].upper()
                self._news_buffer = [
                    n
                    for n in self._news_buffer
                    if base not in n.currencies and quote not in n.currencies
                ]
            else:
                # バッファ空でもキャッシュから取得
                sentiment = self._news_analyzer.get_current_sentiment(
                    self._active_symbol
                )
                if sentiment != 0.0:
                    fundamental_ctx = self._blend_news_sentiment(
                        fundamental_ctx, sentiment
                    )

        # 3.5. リアルタイムスプレッドをbotに注入（SoftGuard用）
        try:
            _spread_pips = await self._data_provider.get_spread_async(
                self._active_symbol,
            )
            self._bot.set_current_spread_pips(_spread_pips)
        except Exception:
            pass  # 取得失敗時はプリセット値にフォールバック

        # 4. シグナル生成
        current_time = pd.Timestamp.now(tz="UTC")
        signal = self._bot.generate_signal(
            current_time,
            fundamental_ctx=fundamental_ctx,
        )

        # 分析結果を保存
        # クールダウン等で分析スキップ時（scores空）は前回の
        # 表示データを保持し、UIの空表示を防止
        if signal and signal.scores:
            self._last_analysis = signal
        self._last_tick_time = datetime.now(UTC)

        if signal and signal.direction != SignalType.HOLD:
            self._last_signal = signal
            converted = self._consolidated_to_signal(signal)
            self._signal_history.append(converted)
            # 履歴上限
            if len(self._signal_history) > 200:
                self._signal_history = self._signal_history[-200:]
            logger.debug(
                "[%s] シグナル生成: %s conf=%.2f",
                self._active_symbol,
                signal.direction.value,
                signal.confidence,
            )

            # EventBus経由でシグナルブロードキャスト
            get_event_bus().publish_nowait(
                "signal.generated",
                {
                    "signal_id": converted.signal_id,
                    "symbol": converted.symbol,
                    "timeframe": converted.timeframe,
                    "signal_type": converted.signal_type.value,
                    "confidence": converted.confidence,
                    "confidence_level": (converted.confidence_level.value),
                    "stop_loss": converted.stop_loss,
                    "take_profit": converted.take_profit,
                    "reasoning": converted.reasoning,
                    "created_at": (converted.created_at.isoformat()),
                },
            )

            # 4. エントリー判定
            if self._enable_auto_trade:
                entry_signal = self._consolidated_to_signal(signal)
                if self._should_use_tick_optimizer():
                    self._tick_optimizer.start_monitoring(entry_signal)
                else:
                    await self._execute_entry(entry_signal)

        # 4.5 ティック監視ポーリング
        if self._tick_optimizer.is_active:
            result = await self._tick_optimizer.poll_tick()
            if result is not None:
                if result.should_execute:
                    pending = self._tick_optimizer.pending_signal
                    if pending is not None:
                        await self._execute_entry(pending)
                self._tick_optimizer.reset()

        # 5. tick完了: 全UIデータをWebSocketで一括配信
        task = asyncio.create_task(
            self._broadcast_tick_update(),
        )
        self._background_tasks.add(task)
        task.add_done_callback(
            self._background_tasks.discard,
        )

    async def _publish_cached_price(self) -> None:
        """MT5から最新tick価格を取得してチャートに配信

        _tick()の結果に関係なく（早期リターンでも）、
        最新の価格をフロントエンドに送信する。
        time_ms=0で送信し、既存バーのclose更新のみ行う
        （新しい足の生成は_tick_price_update()に任せる）。
        """
        try:
            tick = await self._data_provider.get_tick_fast(
                self._active_symbol,
            )
        except Exception:
            return
        if not tick:
            return
        bid = float(tick.get("bid", 0.0))
        if bid > 0:
            # time_ms=0: フロントエンドはcloseのみ更新し
            # 新足生成をスキップする
            await get_event_bus().publish(
                "price.updated",
                {
                    "symbol": self._active_symbol,
                    "bid": bid,
                    "ask": float(tick.get("ask", 0.0)),
                    "time_ms": 0,
                },
            )

    async def _broadcast_tick_update(self) -> None:
        """tick完了後に全UIデータをダッシュボードへ一括配信

        analysis / account / positions / radar を1ペイロードで送信。
        フロントエンドはこのイベントを受信してUIを全更新する。
        """
        payload = self._build_tick_payload()
        await get_event_bus().publish("tick.completed", payload)

    def _build_tick_payload(self) -> dict:
        """tick_updateペイロードを構築

        Returns:
            dict: analysis / account / positions / radar / mode
        """
        # --- analysis ---
        cs = self._last_analysis
        tick_time = self._last_tick_time
        if cs is not None:
            analysis = {
                "symbol": self._config.symbol,
                "direction": cs.direction.value,
                "confidence": cs.confidence,
                "consensus_score": cs.consensus_score,
                "entry_threshold": (
                    self.get_current_entry_threshold(cs.mode)
                    or cs.entry_threshold
                ),
                "regime": cs.regime,
                "mode": cs.mode,
                "rationale": cs.rationale,
                "htf_alignment": cs.htf_alignment,
                "penalty_total": cs.penalty_total,
                "penalty_breakdown": dict(cs.penalty_breakdown),
                "trend_strength": cs.trend_strength,
                "aligned_tfs": list(cs.aligned_tfs),
                "tf_scores": dict(cs.scores),
                "tf_breakdowns": {
                    k: dict(v) for k, v in cs.tf_score_breakdowns.items()
                },
                "tf_directions": dict(cs.tf_directions),
                "last_tick_time": (
                    tick_time.isoformat() if tick_time else None
                ),
                "demo_mode": self.demo_mode_enabled,
                "engine_running": self._running,
                "auto_trade_enabled": self._enable_auto_trade,
                "mt5_connected": self.connected,
                "buy_score": cs.buy_score,
                "sell_score": cs.sell_score,
            }
        else:
            analysis = {
                "symbol": self._config.symbol,
                "engine_running": self._running,
                "mt5_connected": self.connected,
                "auto_trade_enabled": self._enable_auto_trade,
                "demo_mode": self.demo_mode_enabled,
            }

        # --- account (metrics用) ---
        acc = self._account_info
        account = {
            "balance": acc.balance if acc else 0.0,
            "equity": acc.equity if acc else 0.0,
            "margin": acc.margin if acc else 0.0,
            "free_margin": acc.free_margin if acc else 0.0,
            "profit": acc.profit if acc else 0.0,
        }

        # --- radar (シグナル履歴からHOLD除外・信頼度降順) ---
        grouped: dict[str, list] = {}
        for s in self._signal_history:
            if s.signal_type.value != "HOLD":
                grouped.setdefault(s.symbol, []).append(s)
        radar = {
            sym: sorted(sigs, key=lambda x: x.confidence, reverse=True)
            for sym, sigs in grouped.items()
        }
        radar_serialized = {
            sym: [
                {
                    "signal_id": s.signal_id,
                    "signal_type": s.signal_type.value,
                    "timeframe": s.timeframe.value
                    if hasattr(s.timeframe, "value")
                    else str(s.timeframe),
                    "confidence": s.confidence,
                    "confidence_level": s.confidence_level.value
                    if hasattr(s.confidence_level, "value")
                    else str(s.confidence_level),
                    "reasoning": s.reasoning,
                }
                for s in sigs
            ]
            for sym, sigs in radar.items()
        }

        # --- indicators (エンジン計算済みデータから取得) ---
        indicators: dict[str, dict] = {}
        if self._bot and hasattr(self._bot, "_market_data"):
            for tf in self._bot._market_data:
                indicators[tf] = self._extract_indicators(tf)

        # --- active_alerts (UI通知用) ---
        analysis["active_alerts"] = self._build_active_alerts(cs)

        return {
            "analysis": analysis,
            "account": account,
            "positions": self._cached_positions,
            "radar": radar_serialized,
            "indicators": indicators,
        }

    def _build_active_alerts(
        self,
        cs: ConsolidatedSignal | None,
    ) -> list[dict[str, str]]:
        """UI通知用のアクティブアラートを構築

        Args:
            cs: 最新の統合シグナル

        Returns:
            list[dict[str, str]]: アラートリスト
                各要素は {type, message, severity} を持つ
        """
        alerts: list[dict[str, str]] = []

        # 1. ポジション制限（現在状態をリアルタイムチェック）
        self._check_position_limit_alerts(alerts)

        # 2. 週末カットオフ接近/発動
        now = datetime.now(UTC)
        if now.weekday() == 4:  # 金曜日
            pm_cfg = self._pm.config
            cutoff_h = pm_cfg.weekend_close_hour
            cutoff_m = pm_cfg.weekend_close_minute
            cutoff_total = cutoff_h * 60 + cutoff_m
            now_total = now.hour * 60 + now.minute
            remaining = cutoff_total - now_total
            if remaining <= 0:
                alerts.append({
                    "type": "weekend_cutoff",
                    "message": "週末カットオフ発動中",
                    "severity": "danger",
                })
            elif remaining <= 120:
                alerts.append({
                    "type": "weekend_cutoff",
                    "message": (
                        f"週末カットオフまで {remaining} 分"
                    ),
                    "severity": "warning",
                })

        # 3. HOLD理由（スコアがベース閾値以上だが制約でHOLD）
        if cs is not None and cs.direction.value == "HOLD":
            base_th = self._bot.config.consensus_threshold
            _score = max(
                cs.buy_score or 0, cs.sell_score or 0,
            )
            if _score >= base_th and cs.rationale:
                _reason = cs.rationale
                # ファンダフィルターは指標イベントアラートと重複
                if _reason == "ファンダフィルター":
                    pass
                elif _reason == "primary_tfデータなし":
                    alerts.append({
                        "type": "hold_constraint",
                        "message": _reason,
                        "severity": "warning",
                    })
                else:
                    alerts.append({
                        "type": "hold_constraint",
                        "message": _reason,
                        "severity": "info",
                    })

        # デバッグアラート注入
        if (
            self._engine_manager is not None
            and self._engine_manager._debug_alerts
        ):
            alerts.extend(self._engine_manager._debug_alerts)

        return alerts

    def _check_position_limit_alerts(
        self,
        alerts: list[dict[str, str]],
    ) -> None:
        """ポジション制限の現在状態を即時チェックしてアラート追加

        _cached_positions を使ってリアルタイムに制限状態を判定する。
        _last_entry_skip_reason のステイル問題を回避する。

        Args:
            alerts: アラートリスト（in-placeで追加）
        """
        # シンボル別ポジション制限
        cfg = self._bot.config
        base_max = (
            cfg.demo_max_positions if cfg.demo_mode else cfg.max_positions
        )
        sym_positions = [
            p for p in self._cached_positions
            if p.get("symbol") == self._active_symbol
        ]
        if base_max > 0 and len(sym_positions) >= base_max:
            alerts.append({
                "type": "position_limit_symbol",
                "message": (
                    f"ポジション上限 "
                    f"{len(sym_positions)}/{base_max}"
                ),
                "severity": "warning",
            })

        # グローバルポジション制限
        if (
            self._global_max_positions > 0
            and self._get_global_position_count is not None
        ):
            _g_count = self._get_global_position_count()
            if _g_count >= self._global_max_positions:
                alerts.append({
                    "type": "position_limit_global",
                    "message": (
                        f"グローバルポジション上限 "
                        f"{_g_count}/{self._global_max_positions}"
                    ),
                    "severity": "warning",
                })

        # グローバルロット制限
        if (
            self._global_max_exposure_lot > 0
            and self._get_global_exposure_lot is not None
        ):
            _g_lot = self._get_global_exposure_lot()
            if _g_lot >= self._global_max_exposure_lot:
                alerts.append({
                    "type": "exposure_limit",
                    "message": (
                        f"ロット上限 "
                        f"{_g_lot:.2f}/"
                        f"{self._global_max_exposure_lot:.1f}"
                    ),
                    "severity": "warning",
                })

        # JPY同方向制限
        if (
            self._max_same_direction_jpy > 0
            and self._get_jpy_direction_count is not None
            and self._active_symbol.endswith("JPY")
        ):
            for _dir in ("BUY", "SELL"):
                _cnt = self._get_jpy_direction_count(_dir)
                if _cnt >= self._max_same_direction_jpy:
                    alerts.append({
                        "type": f"jpy_direction_{_dir.lower()}",
                        "message": (
                            f"JPY {_dir}上限 "
                            f"{_cnt}/"
                            f"{self._max_same_direction_jpy}"
                        ),
                        "severity": "warning",
                    })

        # ポートフォリオDD警告・緊急停止
        if self._engine_manager is not None:
            mgr = self._engine_manager
            if mgr.dd_emergency_active:
                alerts.append({
                    "type": "portfolio_dd_emergency",
                    "message": (
                        f"DD緊急停止 "
                        f"{mgr.current_dd_pct:.2f}% "
                        f"(>= 5%) — 全決済済・エントリー停止中"
                    ),
                    "severity": "danger",
                })
            elif mgr.dd_warning_active:
                alerts.append({
                    "type": "portfolio_dd_warning",
                    "message": (
                        f"DD警告 "
                        f"{mgr.current_dd_pct:.2f}% "
                        f"(>= 3%)"
                    ),
                    "severity": "warning",
                })

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
    ):
        """ローソク足データ取得（public API）

        Args:
            symbol: 通貨ペア
            timeframe: 時間足文字列またはTimeframe
            limit: 取得本数

        Returns:
            pd.DataFrame: ローソク足DataFrame
        """
        return await self._data_provider.get_candles_from_pos(
            symbol, timeframe, limit
        )

    async def get_candles_before(
        self,
        symbol: str,
        timeframe: str,
        end_time: datetime,
        limit: int,
    ):
        """指定時刻より前のローソク足データ取得

        Args:
            symbol: 通貨ペア
            timeframe: 時間足文字列
            end_time: この時刻より前のデータを取得（排他）
            limit: 取得本数

        Returns:
            pd.DataFrame: ローソク足DataFrame
        """
        tf_val = timeframe.value if hasattr(timeframe, "value") else timeframe
        tf_enum = Timeframe(tf_val)
        tf_sec = tf_enum.minutes() * 60
        # 休場日を考慮して3倍マージンで開始時刻を推定
        start = end_time - timedelta(seconds=tf_sec * limit * 3)
        df = await self._data_provider.get_candles_async(
            symbol, tf_enum, start, end_time
        )
        if df.empty:
            return df
        # end_time未満にフィルタし末尾limit件を返す
        df = df[df["time"] < end_time]
        return df.tail(limit).reset_index(drop=True)

    def get_indicators(self, timeframe: str) -> dict | None:
        """計算済み指標取得（public API）

        Args:
            timeframe: 時間足文字列

        Returns:
            dict | None: 指標辞書（データなしの場合は空dict）
        """
        return self._extract_indicators(timeframe)

    def _extract_indicators(self, timeframe: str) -> dict:
        """計算済み市場データから指標値を抽出

        Args:
            timeframe: 時間足文字列

        Returns:
            dict: renderIndicators()が期待するフィールド辞書
        """
        import math

        md = self._bot._market_data if self._bot else {}
        df = md.get(timeframe)
        if df is None or df.empty:
            return {}

        row = df.iloc[-1]

        def _v(col: str) -> float | None:
            """NaN/欠損を None に変換"""
            try:
                v = row[col]
                return None if math.isnan(float(v)) else float(v)
            except (KeyError, TypeError, ValueError):
                return None

        return {
            "rsi": _v("rsi_14"),
            "macd": _v("macd"),
            "macd_signal": _v("macd_signal"),
            "macd_hist": _v("macd_histogram"),
            "adx": _v("adx"),
            "plus_di": _v("plus_di"),
            "minus_di": _v("minus_di"),
            "bb_upper": _v("bb_upper"),
            "bb_middle": _v("bb_middle"),
            "bb_lower": _v("bb_lower"),
            "atr": _v("atr_14"),
            "ema_fast": _v("ema_12"),
            "ema_slow": _v("ema_26"),
        }

    def _consolidated_to_signal(
        self,
        cs: ConsolidatedSignal,
    ) -> Signal:
        """ConsolidatedSignalをSignalエンティティに変換

        Args:
            cs: 統合シグナル

        Returns:
            Signal: シグナルエンティティ
        """
        return Signal(
            signal_id=str(uuid.uuid4()),
            symbol=self._active_symbol,
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

    async def _load_historical_data(self) -> None:
        """起動時に過去データをTradeBotに供給

        全TFのデータを一括収集してから設定。
        （個別set_market_dataは辞書を上書きするため）
        """
        symbol = self._active_symbol
        lookback = self._config.candle_lookback
        timeframes = self._bot.timeframes

        logger.info(
            "過去データ読込: %s %d本 x %d時間足",
            symbol,
            lookback,
            len(timeframes),
        )

        all_data: dict[str, pd.DataFrame] = {}
        for tf_str in timeframes:
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                logger.warning("未知の時間足: %s", tf_str)
                continue

            # M1/M5はMT5サーバー時間オフセットにより多めに取得
            tf_lookback = max(lookback, 500) if tf_str in ("M1", "M5") else lookback
            df = await self._data_provider.get_candles_from_pos(
                symbol, tf, tf_lookback
            )
            if df.empty:
                logger.warning("データなし: %s %s", symbol, tf_str)
                continue

            all_data[tf_str] = df
            logger.info(
                "データ読込完了: %s %s %d本",
                symbol,
                tf_str,
                len(df),
            )

        if all_data:
            all_data = self._calc_indicators(all_data)
            self._bot.set_market_data(all_data)
            logger.info("全TFデータ設定完了: %d時間足", len(all_data))

    async def _update_market_data(self) -> None:
        """最新ローソク足データを取得してTradeBotに設定

        時間足確定を待たずリアルタイム評価するため、全TFの最後の
        バーのclose/high/lowを現在のtick価格で上書きしてから
        インジケータを再計算する。
        """
        symbol = self._active_symbol
        # 全TFのデータを一括収集してから設定
        # MT5サーバー時間(UTC+2/3)とUTCのずれにより、
        # M1は200本だとSMA(50)のウォームアップ範囲(先頭49本)に
        # 現在行が入るため、M1/M5は500本取得する
        # （個別set_market_dataは辞書を上書きするため）
        all_data: dict[str, pd.DataFrame] = {}
        for tf_str in self._bot.timeframes:
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                continue

            lookback = 500 if tf_str in ("M1", "M5") else 200
            df = await self._data_provider.get_candles_from_pos(
                symbol, tf, lookback
            )
            if not df.empty:
                all_data[tf_str] = df

        # リアルタイム評価: キャッシュ済みtick価格で最後のバーを更新
        # _tick_price_update()が0.1秒毎にキャッシュするため追加API不要。
        # インジケータ・アナリティクスは同じ1秒サイクルで同期して更新される。
        tick = self._last_tick_data
        if tick:
            bid = float(tick.get("bid", 0.0))
            ask = float(tick.get("ask", 0.0))
            mid = (bid + ask) / 2.0
            if mid > 0:
                for tf_str, df in all_data.items():
                    if df.empty:
                        continue
                    df = df.copy()
                    idx = df.index[-1]
                    df.at[idx, "close"] = mid
                    if mid > float(df.at[idx, "high"]):
                        df.at[idx, "high"] = mid
                    if mid < float(df.at[idx, "low"]):
                        df.at[idx, "low"] = mid
                    all_data[tf_str] = df

        if all_data:
            # MT5取得に失敗したTFは既存データで補完し、
            # set_market_dataで誤って削除されないようにする
            existing = self._bot.market_data
            for tf_str in self._bot.timeframes:
                if tf_str not in all_data and tf_str in existing:
                    logger.debug(
                        "市場データ補完: %s（MT5取得失敗のため前回値使用）",
                        tf_str,
                    )
                    all_data[tf_str] = existing[tf_str]

            all_data = self._calc_indicators(all_data)
            self._bot.set_market_data(all_data)

    def _calc_indicators(
        self,
        data: dict[str, pd.DataFrame],
    ) -> dict[str, pd.DataFrame]:
        """生OHLCVデータにテクニカル指標を計算して付加

        Args:
            data: 時間足別生OHLCVデータ

        Returns:
            dict[str, pd.DataFrame]: 指標付きデータ
        """
        calc = TechnicalIndicatorBatch()
        result: dict[str, pd.DataFrame] = {}
        for tf, df in data.items():
            try:
                result[tf] = calc.calculate_basic(df.copy())
            except Exception as e:
                logger.warning("指標計算失敗: %s %s", tf, e)
                result[tf] = df
        return result

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

        return True

    async def _execute_entry(self, signal: Signal) -> None:
        """エントリー実行

        Args:
            signal: トレードシグナル
        """
        # ポートフォリオDD緊急停止チェック
        if (
            self._engine_manager is not None
            and self._engine_manager.dd_emergency_active
        ):
            self._last_entry_skip_reason = (
                f"DD緊急停止 "
                f"{self._engine_manager.current_dd_pct:.1f}%"
            )
            return

        # ホットリロード中はエントリーをスキップ
        if self._entry_blocked:
            self._last_entry_skip_reason = "ホットリロード中"
            logger.info("エントリーブロック中（ホットリロード）— スキップ")
            return

        # 既存ポジションチェック（設定値に基づく上限）
        positions = await self._executor.get_open_positions_async(
            self._active_symbol
        )
        # MT5接続エラー時はエントリーを安全にスキップ
        if positions is None:
            self._last_entry_skip_reason = "MT5接続エラー"
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
            self._last_entry_skip_reason = (
                f"ポジション上限 {len(positions)}/{max_pos}"
            )
            logger.info(
                "[%s] 既存ポジション上限(%d)、エントリースキップ",
                self._active_symbol,
                max_pos,
            )
            return

        # グローバルポジション制限チェック
        if (
            self._global_max_positions > 0
            and self._get_global_position_count is not None
        ):
            _g_count = self._get_global_position_count()
            if _g_count >= self._global_max_positions:
                self._last_entry_skip_reason = (
                    f"グローバルポジション上限 "
                    f"{_g_count}/{self._global_max_positions}"
                )
                logger.info(
                    "[%s] グローバルポジション上限"
                    "(%d/%d)、エントリースキップ",
                    self._active_symbol,
                    _g_count,
                    self._global_max_positions,
                )
                return

        # グローバルエクスポージャー制限チェック
        if (
            self._global_max_exposure_lot > 0
            and self._get_global_exposure_lot is not None
        ):
            _g_lot = self._get_global_exposure_lot()
            if _g_lot >= self._global_max_exposure_lot:
                self._last_entry_skip_reason = (
                    f"ロット上限 "
                    f"{_g_lot:.2f}/{self._global_max_exposure_lot:.1f}"
                )
                logger.info(
                    "[%s] グローバルロット上限"
                    "(%.2f/%.1f)、エントリースキップ",
                    self._active_symbol,
                    _g_lot,
                    self._global_max_exposure_lot,
                )
                return

        # JPY同方向制限チェック
        if (
            self._max_same_direction_jpy > 0
            and self._get_jpy_direction_count is not None
            and self._active_symbol.endswith("JPY")
        ):
            _dir = signal.signal_type.value
            _dir_count = self._get_jpy_direction_count(_dir)
            if _dir_count >= self._max_same_direction_jpy:
                self._last_entry_skip_reason = (
                    f"JPY {_dir}上限 "
                    f"{_dir_count}/"
                    f"{self._max_same_direction_jpy}"
                )
                logger.info(
                    "[%s] JPY %s方向上限"
                    "(%d/%d)、エントリースキップ",
                    self._active_symbol,
                    _dir,
                    _dir_count,
                    self._max_same_direction_jpy,
                )
                return

        # 全制限チェック通過 → スキップ理由をクリア
        self._last_entry_skip_reason = None

        # ロット計算
        if self._account_info is None:
            logger.warning(
                "[%s] 口座情報なし、エントリースキップ",
                self._active_symbol,
            )
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

        # 現在のエクスポージャーを計算
        _buy_lot = sum(
            p.volume for p in positions
            if p.signal_type == SignalType.BUY
        )
        _sell_lot = sum(
            p.volume for p in positions
            if p.signal_type == SignalType.SELL
        )
        _local_exposure = _buy_lot + _sell_lot
        # グローバルエクスポージャーが取得可能なら使用
        if self._get_global_exposure_lot is not None:
            _exposure_lot = self._get_global_exposure_lot()
        else:
            _exposure_lot = _local_exposure
        _same_dir_lot = (
            _buy_lot
            if signal.signal_type == SignalType.BUY
            else _sell_lot
        )

        # 連続負け数（直近のclosed_tradesから計算）
        _consec_losses = 0
        for t in reversed(self._closed_trades):
            _pnl = t.get("pnl_pips", 0)
            if _pnl < 0:
                _consec_losses += 1
            else:
                break

        # 現在のDD%（equity vs balance）
        _dd_pct = 0.0
        if self._account_info.balance > 0:
            _dd_pct = max(
                0.0,
                (
                    1.0
                    - self._account_info.equity
                    / self._account_info.balance
                )
                * 100.0,
            )

        sizing_ctx = SizingContext(
            equity=self._account_info.equity,
            sl_pips=sl_pips if sl_pips > 0 else 30.0,
            confidence=signal.confidence,
            regime=regime,
            consecutive_losses=_consec_losses,
            current_dd_pct=_dd_pct,
            initial_equity=self._account_info.balance,
            open_exposure_lot=_exposure_lot,
            open_same_direction_lot=_same_dir_lot,
        )
        sizing_result = self._sizer.calculate(sizing_ctx)

        if sizing_result.blocked:
            logger.warning(
                "[%s] サイジング拒否: %s",
                self._active_symbol,
                sizing_result.reasoning,
            )
            return

        lot = sizing_result.lot

        if lot <= 0:
            logger.warning(
                "[%s] ロット計算結果=0、エントリースキップ",
                self._active_symbol,
            )
            return

        # Signal にlotを付与
        signal_with_lot = signal.model_copy(update={"lot": lot})

        # MT5発注（発注直前のtick価格を取得してentry_priceに使用）
        entry_tick = await self._data_provider.get_tick(self._active_symbol)
        result = await self._executor.open_position_async(signal_with_lot, lot)

        if result.success:
            logger.info(
                "[%s] エントリー成功: ticket=%d %.2f lots",
                self._active_symbol,
                result.ticket or 0,
                lot,
            )
            # PositionManagerに登録
            trade_id = ""
            if result.ticket:
                await self._register_new_position(
                    result.ticket, signal_with_lot, lot, entry_tick
                )
                # DB書き込み（エントリー記録）
                trade_id = (
                    self._write_entry_to_db(
                        result.ticket, signal_with_lot, lot, entry_tick
                    )
                    or ""
                )
                if trade_id:
                    self._open_trades[result.ticket] = trade_id

            # _cached_positionsに即時追加（次tick待ち不要）
            entry_price = 0.0
            if entry_tick:
                price_key = (
                    "ask"
                    if signal_with_lot.signal_type == SignalType.BUY
                    else "bid"
                )
                entry_price = (
                    float(entry_tick.get(price_key, 0)) or entry_price
                )
            self._cached_positions.append(
                {
                    "position_id": str(result.ticket or 0),
                    "trade_id": trade_id,
                    "ticket": result.ticket or 0,
                    "symbol": self._active_symbol,
                    "signal_type": (signal_with_lot.signal_type.value),
                    "volume": lot,
                    "entry_price": entry_price,
                    "current_price": entry_price,
                    "stop_loss": signal_with_lot.stop_loss,
                    "take_profit": signal_with_lot.take_profit,
                    "opened_at": datetime.now(UTC).isoformat(),
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
            get_event_bus().publish_nowait(
                "position.opened",
                {"symbol": self._active_symbol},
            )
        else:
            logger.error(
                "[%s] エントリー失敗: %s",
                self._active_symbol,
                result.message,
            )

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
        # エントリー価格（実際のask/bid価格）
        is_buy = signal.signal_type == SignalType.BUY
        entry_price = 0.0
        if entry_tick:
            entry_price = float(
                entry_tick.get("ask", 0)
                if is_buy
                else entry_tick.get("bid", 0)
            )
        if entry_price <= 0:
            logger.warning(
                "[%s] entry_price取得失敗、PM登録スキップ",
                self._active_symbol,
            )
            return

        # signal.stop_loss/take_profitはpips値 → 価格レベルに変換
        pip_size = self._get_pip_size(signal.symbol)
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
            "[%s] PM登録: ticket=%d entry=%.3f sl=%.3f tp=%.3f",
            self._active_symbol,
            ticket,
            entry_price,
            sl_price,
            tp_price,
        )

        # モードは常にUNIVERSAL
        mode = UNIVERSAL_MODE

        # UNIVERSALプランを取得し
        # regimeとselection_reasonを付与
        _base_plan = TradingPlan.create_universal()
        plan = dataclasses.replace(
            _base_plan,
            selection_reason="live",
            regime=signal.regime,
        )
        logger.info(
            "PM登録プラン: mode=%s primary_tf=%s",
            mode,
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
            entry_own_score=signal.consensus_score or 0.0,
        )
        # 新規登録時に管理状態をローカルDBに保存
        self._save_position_state(str(ticket))

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
        from autotrader.adapters.database.connection import get_session
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
            pip_size = self._get_pip_size(signal.symbol)
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
                    entry_own_score=signal.consensus_score or 0.0,
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

    async def _handle_external_close(self, ticket: int) -> None:
        """外部決済（SL/TP/手動）をMT5約定履歴から取得してDB記録。

        Args:
            ticket: MT5ポジションID
        """
        # 初回のみログ出力（リトライ中のログスパム防止）
        if ticket not in self._ext_close_retries:
            logger.info(
                "[%s] 外部決済検出（手動/SL/TP）: ticket=%d",
                self._active_symbol,
                ticket,
            )

        # DBからポジションのシンボルを確認し、自エンジンと一致しない場合はスキップ
        trade_symbol = self._get_trade_symbol_from_db(ticket)
        if trade_symbol and trade_symbol != self._active_symbol:
            logger.info(
                "他シンボルのポジション: ticket=%d symbol=%s"
                " (自エンジン=%s) → スキップ",
                ticket,
                trade_symbol,
                self._active_symbol,
            )
            # _open_tradesから除去（誤復元分）
            self._open_trades.pop(ticket, None)
            return

        # 5分 lookback で約定履歴を検索
        deal = await self._executor.get_deal_by_position_async(ticket)
        if not deal:
            # フォールバック: 全履歴から検索（エンジン停止が長かった場合）
            deal = await self._executor.get_deal_by_position_id_async(
                ticket
            )
            if deal:
                logger.info(
                    "全履歴検索で約定取得: ticket=%d", ticket
                )
        if deal:
            exit_price = deal["price"]
            profit_loss = deal["profit"]
            exit_reason = mt5_reason_to_exit_reason(deal["reason_code"])
            logger.info(
                "外部決済詳細: ticket=%d reason=%s price=%.5f profit=%.2f",
                ticket,
                exit_reason,
                exit_price,
                profit_loss,
            )
            # 確定したのでリトライカウンターをクリア
            self._ext_close_retries.pop(ticket, None)
        else:
            # 決済約定が見つからない＝まだオープンの可能性
            # MT5が一時的に空リストを返した場合の誤検知を防止
            # （_close_ghost_db_recordsと同じ安全ガード）
            retry = self._ext_close_retries.get(ticket, 0) + 1
            self._ext_close_retries[ticket] = retry
            if retry < self._EXT_CLOSE_MAX_RETRIES:
                logger.info(
                    "外部決済の約定履歴未検出: ticket=%d"
                    " → 確認待機中 (%d/%d)",
                    ticket,
                    retry,
                    self._EXT_CLOSE_MAX_RETRIES,
                )
                # _open_tradesから除去しない → 次tickで再検出
                return
            # 閾値到達: 本当に決済済みと判断
            logger.warning(
                "外部決済の約定履歴が%d回連続未検出:"
                " ticket=%d → フォールバック記録",
                self._EXT_CLOSE_MAX_RETRIES,
                ticket,
            )
            exit_price = 0.0
            _tick_symbol = trade_symbol or self._active_symbol
            try:
                tick = await self._data_provider.get_tick(_tick_symbol)
                _bid = float(tick.get("bid", 0))
                _ask = float(tick.get("ask", 0))
                if _bid > 0 and _ask > 0:
                    exit_price = (_bid + _ask) / 2
                elif _bid > 0:
                    exit_price = _bid
            except (KeyError, ValueError, TypeError):
                pass
            profit_loss = 0.0
            exit_reason = ExitReason.EXTERNAL_CLOSE.value
            self._ext_close_retries.pop(ticket, None)
        self._write_close_to_db(ticket, exit_price, exit_reason, profit_loss)
        # ローカルDB管理状態を削除
        self._delete_position_state(str(ticket))

    def _get_trade_symbol_from_db(self, ticket: int) -> str | None:
        """DBからトレードのシンボルを取得

        Args:
            ticket: MT5チケットID

        Returns:
            str | None: シンボル文字列。未取得時None。
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.models import (
            TradeRecord,
        )
        from autotrader.config.settings import get_settings

        try:
            db_url = get_settings().database_url
            with get_session(db_url) as db:
                record = (
                    db.query(TradeRecord.symbol)
                    .filter(TradeRecord.ticket == ticket)
                    .first()
                )
                return record[0] if record else None
        except Exception:
            return None

    def _update_sl_in_db(
        self, ticket: int, new_sl: float
    ) -> None:
        """DB上のオープントレードのSLを更新

        トレーリングストップ/ブレークイーブン移動後に
        REST APIフォールバックでも最新値を返せるようにする。

        Args:
            ticket: MT5チケットID
            new_sl: 新しいSL価格
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.models import (
            TradeRecord,
        )
        from autotrader.config.settings import get_settings

        trade_id = self._open_trades.get(ticket)
        if not trade_id:
            return
        try:
            db_url = get_settings().database_url
            with get_session(db_url) as db:
                record = (
                    db.query(TradeRecord)
                    .filter(
                        TradeRecord.trade_id == trade_id,
                    )
                    .first()
                )
                if record:
                    record.stop_loss = new_sl
                    db.flush()
        except Exception:
            logger.debug(
                "DB SL更新スキップ: ticket=%d", ticket
            )

    def _update_volume_in_db(
        self,
        ticket: int,
        new_volume: float,
        partial_profit: float = 0.0,
    ) -> None:
        """部分決済後のDB volumeを更新

        Args:
            ticket: MT5チケットID
            new_volume: 残ロット数
            partial_profit: 部分決済の確定損益
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.repositories import (
            TradeRepository,
        )
        from autotrader.config.settings import get_settings

        trade_id = self._open_trades.get(ticket)
        if not trade_id:
            return
        try:
            db_url = get_settings().database_url
            with get_session(db_url) as db:
                repo = TradeRepository(db)
                repo.update_volume(
                    trade_id, new_volume, partial_profit
                )
            logger.info(
                "DB部分決済記録: ticket=%d"
                " 残vol=%.2f partial_pnl=%.2f",
                ticket,
                new_volume,
                partial_profit,
            )
        except Exception:
            logger.debug(
                "DB volume更新スキップ: ticket=%d",
                ticket,
            )

    def _write_close_to_db(
        self,
        ticket: int,
        current_price: float,
        action_reason: str,
        profit_loss: float = 0.0,
    ) -> None:
        """決済をDBに更新

        Args:
            ticket: MT5チケットID
            current_price: 決済時の価格
            action_reason: 決済理由
            profit_loss: 確定損益（金額）
        """
        from autotrader.adapters.database.connection import get_session
        from autotrader.adapters.database.repositories import (
            TradeRepository,
        )
        from autotrader.config.settings import get_settings

        trade_id = self._open_trades.get(ticket)
        if not trade_id:
            return
        try:
            pos = self._pm.get_position(str(ticket))
            pnl_pips = 0.0
            if pos and current_price > 0:
                pip_size = self._get_pip_size(self._active_symbol)
                price_diff = (
                    current_price - pos.entry_price
                    if pos.direction == SignalType.BUY
                    else pos.entry_price - current_price
                )
                pnl_pips = price_diff / pip_size
                # MT5から損益が取得できなかった場合、
                # pnl_pipsとvolumeから概算（スプレッド・スワップ除く）
                if profit_loss == 0.0 and abs(pnl_pips) > 0:
                    pip_val = self._get_pip_value(self._active_symbol)
                    # ManagedPositionはremaining_volumeを使用
                    _vol = pos.remaining_volume
                    profit_loss = round(pnl_pips * _vol * pip_val, 2)
            closed_at = datetime.now(UTC)
            db_url = get_settings().database_url
            # 最終SL/TP（トレーリング/BE移動後の値）
            final_sl = pos.current_sl if pos else None
            final_tp = pos.original_tp if pos else None
            with get_session(db_url) as db:
                repo = TradeRepository(db)
                repo.close(
                    trade_id=trade_id,
                    exit_price=current_price,
                    closed_at=closed_at,
                    exit_reason=action_reason,
                    profit_loss=profit_loss,
                    profit_loss_pips=pnl_pips,
                    final_stop_loss=final_sl,
                    final_take_profit=final_tp,
                )
            # DB書き込み成功後にpop（失敗時は次回tickで再試行）
            self._open_trades.pop(ticket, None)
            self._closed_trades.append(
                {
                    "trade_id": trade_id,
                    "ticket": ticket,
                    "exit_price": current_price,
                    "exit_reason": action_reason,
                    "pnl_pips": round(pnl_pips, 1),
                    "closed_at": closed_at.isoformat(),
                }
            )
            logger.info(
                "DB記録（決済）: trade_id=%s ticket=%d"
                " pnl_pips=%.1f profit_loss=%.2f",
                trade_id,
                ticket,
                pnl_pips,
                profit_loss,
            )
            # EventBus経由で決済イベントをUIに即時通知
            get_event_bus().publish_nowait(
                "position.closed",
                {"symbol": self._active_symbol},
            )
        except Exception as e:
            # trade_idは_open_tradesに残るため次回tickで再試行
            logger.error("DB書き込みエラー（決済）: %s", e)

    async def _manage_positions(self) -> None:
        """既存ポジションの管理

        PositionManager.evaluateで各ポジションを評価し、
        SL変更・部分決済・全決済をMT5で実行。
        _cached_positionsをMT5の現在状態で更新する。
        """
        # 自シンボルのポジションのみ取得
        # （他シンボルのポジションはそのシンボルのエンジンが管理）
        positions = await self._executor.get_open_positions_async(
            self._active_symbol
        )
        # MT5接続エラー時は_cached_positionsを更新しない
        # （一時的な切断時にUIが空になるのを防ぐ）
        if positions is None:
            logger.warning("MT5ポジション取得失敗 — 管理スキップ")
            return
        current_tickets = {pos.ticket for pos in positions}

        # _open_tradesが未復元の場合、DBから復元（起動タイミング対応）
        if not self._open_trades and positions:
            self._restore_open_trades_from_db(
                [pos.ticket for pos in positions]
            )

        # MT5に復帰したチケットのリトライカウンターをリセット
        if self._ext_close_retries:
            recovered = set(self._ext_close_retries.keys()) & current_tickets
            for t in recovered:
                logger.info(
                    "MT5にポジション復帰: ticket=%d"
                    " → 外部決済リトライ取消",
                    t,
                )
                self._ext_close_retries.pop(t, None)

        # 外部決済（手動/SL/TP）の検出:
        # _open_tradesにあるが現在MT5に存在しないticket
        if self._open_trades:
            externally_closed = set(self._open_trades.keys()) - current_tickets
            for ticket in externally_closed:
                await self._handle_external_close(ticket)

        if not positions:
            self._cached_positions = []
            return

        # ATR取得（ポジション管理で使用）
        # USDJPY換算で約20pips相当を最小値とする
        _min_atr = 0.20 if "JPY" in self._active_symbol.upper() else 0.0020
        try:
            latest = await self._data_provider.get_latest_candle_async(
                self._active_symbol, Timeframe.M15
            )
            # ATRは簡易計算（最新の高値-安値）
            _h = float(latest.get("high", 0))
            _l = float(latest.get("low", 0))
            atr = _h - _l if (_h > 0 and _l > 0) else _min_atr
            atr = max(atr, _min_atr)
        except (KeyError, ValueError, TypeError, MT5DataError):
            atr = 0.3  # デフォルト（USDJPY: 約30pips）

        # 現在のシグナル方向（反転チェック用）
        current_signal_type = None
        if self._last_signal:
            current_signal_type = self._last_signal.direction

        cache_list: list[dict] = []
        for position in positions:
            # 通貨ペア別にpip計算（全通貨ペア対応）
            pip_factor = self._get_pip_size(position.symbol)
            pip_value = self._get_pip_value(position.symbol)

            pos_id = str(position.ticket)

            # ティック取得（キャッシュ＋管理評価で共用）
            current_price = position.entry_price
            try:
                tick = await self._data_provider.get_tick(position.symbol)
                price_key = (
                    "bid" if position.signal_type == SignalType.BUY else "ask"
                )
                fetched = float(tick.get(price_key, 0))
                if fetched > 0:
                    current_price = fetched
            except (KeyError, ValueError, TypeError, MT5DataError):
                pass

            # キャッシュエントリ構築（自シンボルのポジション）
            pip_diff = (current_price - position.entry_price) / pip_factor
            if position.signal_type == SignalType.SELL:
                pip_diff = -pip_diff
            # 保有時間を計算
            managed = self._pm.get_position(pos_id)
            remaining_minutes = None
            max_hold_minutes = None
            elapsed_minutes = None
            # PM管理ポジション: entry_time（UTC aware）を使用
            if managed is not None and hasattr(managed, "entry_time"):
                try:
                    elapsed_sec = (
                        datetime.now(UTC) - managed.entry_time
                    ).total_seconds()
                    elapsed_minutes = max(
                        0, int(elapsed_sec / 60)
                    )
                except TypeError:
                    pass
            # フォールバック: MT5のopened_at（UTC aware）を使用
            if elapsed_minutes is None and hasattr(
                position.opened_at, "timestamp"
            ):
                try:
                    elapsed_sec = (
                        datetime.now(UTC) - position.opened_at
                    ).total_seconds()
                    elapsed_minutes = max(
                        0, int(elapsed_sec / 60)
                    )
                except TypeError:
                    pass
            if managed is not None:
                try:
                    from autotrader.config.tf_params_registry import (
                        get_holding_minutes,
                    )

                    dtf = getattr(managed.plan, "dynamic_entry_tf", None)
                    etf = getattr(managed.plan, "entry_tf", None)
                    entry_tf = (
                        dtf
                        if isinstance(dtf, str)
                        else etf
                        if isinstance(etf, str)
                        else None
                    )
                    if entry_tf is not None and elapsed_minutes is not None:
                        max_hold_minutes = get_holding_minutes(entry_tf)
                        remaining_minutes = max(
                            0,
                            int(max_hold_minutes - elapsed_minutes),
                        )
                except (KeyError, ValueError, TypeError, AttributeError):
                    pass

            cache_list.append(
                {
                    "position_id": str(position.ticket),
                    "trade_id": self._open_trades.get(position.ticket, ""),
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
                    "unrealized_pnl": (pip_diff * position.volume * pip_value),
                    "unrealized_pnl_pips": pip_diff,
                    "remaining_minutes": remaining_minutes,
                    "max_hold_minutes": max_hold_minutes,
                    "elapsed_minutes": elapsed_minutes,
                }
            )

            # PM未登録 or 自動取引OFFなら評価スキップ
            # OFF時はMT5手動決済に委ねる
            if managed is None:
                continue
            if not self._enable_auto_trade:
                continue

            try:
                # ファンダメンタルコンテキスト取得
                _fund_ctx = None
                if self._fundamental_memory:
                    _fund_ctx = (
                        self._fundamental_memory
                        .get_context_for_llm(
                            self._active_symbol,
                            datetime.now(UTC),
                        )
                    )
                # ポジション評価
                _cs = self._last_analysis
                action = self._pm.evaluate(
                    position_id=pos_id,
                    current_price=current_price,
                    current_time=datetime.now(UTC),
                    atr=atr,
                    current_signal=current_signal_type,
                    buy_score=(_cs.buy_score or 0.0) if _cs else 0.0,
                    sell_score=(_cs.sell_score or 0.0) if _cs else 0.0,
                    fundamental_assessment=_fund_ctx,
                )

                # アクション実行
                await self._execute_action(position, action, current_price)

                # 管理状態をローカルDBに保存（毎tick）
                self._save_position_state(pos_id)
            except Exception as e:
                logger.error(
                    "ポジション管理エラー(ticket=%d): %s",
                    position.ticket,
                    e,
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
                        # DB上のSLも同期更新
                        self._update_sl_in_db(
                            position.ticket, action.new_sl
                        )

        elif action.action_type == ManagementActionType.PARTIAL_CLOSE:
            close_vol = round(position.volume * action.close_ratio, 2)
            if close_vol > 0:
                result = await self._executor.close_partial_async(
                    position, close_vol, action.reason
                )
                if result.success:
                    logger.info(
                        "部分決済: ticket=%d %.2f lots (%s)",
                        position.ticket,
                        close_vol,
                        action.reason,
                    )
                    # DBのvolumeを更新
                    remaining = round(
                        position.volume - close_vol, 2
                    )
                    _partial_pnl = 0.0
                    try:
                        deal = await self._executor.get_deal_by_position_async(
                            position.ticket
                        )
                        if deal:
                            _partial_pnl = deal["profit"]
                    except (
                        KeyError,
                        ValueError,
                        TypeError,
                        MT5DataError,
                    ):
                        pass
                    self._update_volume_in_db(
                        position.ticket,
                        remaining,
                        _partial_pnl,
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
                            sl_result = (
                                await self._executor.modify_position_async(
                                    position,
                                    stop_loss=action.new_sl,
                                )
                            )
                            if sl_result.success:
                                self._update_sl_in_db(
                                    position.ticket,
                                    action.new_sl,
                                )

        elif action.action_type == ManagementActionType.FULL_CLOSE:
            result = await self._executor.close_position_async(
                position, action.reason
            )
            if result.success:
                logger.info(
                    "全決済: ticket=%d (%s)",
                    position.ticket,
                    action.reason,
                )
                # DB記録（決済）
                # unregister_positionより先に行うことで、
                # _write_close_to_db内のpos取得・pnl_pips計算を可能にする
                _actual_price = (
                    result.exit_price
                    if result.exit_price and result.exit_price > 0
                    else current_price
                )
                _profit_loss = 0.0
                try:
                    deal = await self._executor.get_deal_by_position_async(
                        position.ticket
                    )
                    if deal:
                        _profit_loss = deal["profit"]
                        if deal["price"] > 0:
                            _actual_price = deal["price"]
                except (KeyError, ValueError, TypeError, MT5DataError):
                    pass
                if _actual_price > 0:
                    _exit_reason_str = (
                        action.exit_reason.value
                        if action.exit_reason
                        else "FORCE_CLOSE"
                    )
                    self._write_close_to_db(
                        position.ticket,
                        _actual_price,
                        _exit_reason_str,
                        _profit_loss,
                    )
                # ローカルDB管理状態を削除
                self._delete_position_state(str(position.ticket))
                # DB記録後にPMからポジションを削除
                self._pm.unregister_position(str(position.ticket))

    async def sync_positions_on_toggle(self) -> None:
        """自動取引ON切替時のポジション同期

        auto_tradeがONにトグルされた際にrouterから呼ばれる。
        MT5の既存ポジションとPMを同期し、ローカルDBから
        管理状態（フラグ・追跡値）を復元する。
        失敗時は次回tickの通常フローで自己修復される。
        """
        if not self.running:
            return
        try:
            await self._sync_positions()
        except (MT5Error, TradingError, OSError, RuntimeError):
            logger.error(
                "トグル時ポジション同期失敗",
                exc_info=True,
            )

    async def _sync_positions(self) -> None:
        """MT5の既存ポジションとPositionManagerを同期

        エンジン起動時・auto_tradeトグルON時に呼び出される。
        DBから is_open=True のレコードを検索して
        _open_trades（ticket→trade_id）を復元する。
        ローカルDBから管理状態（フラグ・追跡値）も復元する。
        """
        positions = await self._executor.get_open_positions_async(
            self._active_symbol
        )

        # MT5接続エラー時はゴースト掃除をスキップ
        # None=取得失敗、[]=ポジション0件の区別が必要
        if positions is None:
            logger.warning(
                "MT5ポジション取得失敗 — ゴースト掃除・復元をスキップ"
            )
            return

        # DBゴーストレコード掃除（MT5に存在しないis_open=true）
        active_tickets = {p.ticket for p in positions} if positions else set()
        await self._close_ghost_db_records(active_tickets)

        if not positions:
            logger.info("同期対象ポジションなし")
            return

        # DBからopenトレードを復元（再起動対応）
        self._restore_open_trades_from_db([pos.ticket for pos in positions])
        # 同期後に不要なリトライカウンターを掃除
        self._ext_close_retries = {
            k: v
            for k, v in self._ext_close_retries.items()
            if k in self._open_trades
        }

        # ローカルDBから管理状態を一括取得
        saved_states = self._load_position_states()

        logger.info("%d件のポジションを同期", len(positions))
        for pos in positions:
            # PMに未登録なら簡易登録
            pos_id = str(pos.ticket)
            if self._pm.get_position(pos_id) is None:
                import dataclasses as _dc

                plan = TradingPlan.create_universal(
                    self._bot.config,
                )
                plan = _dc.replace(
                    plan,
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

            # MT5最新値でSLとvolumeを補正
            managed = self._pm.get_position(pos_id)
            if managed is not None:
                managed.current_sl = pos.stop_loss or managed.current_sl
                managed.remaining_volume = pos.volume

                # ローカルDB管理状態の復元
                if pos_id in saved_states:
                    self._pm.import_state(pos_id, saved_states[pos_id])
                    logger.info(
                        "管理状態復元: ticket=%s flags=%s",
                        pos_id,
                        {
                            k: v
                            for k, v in saved_states[pos_id].items()
                            if isinstance(v, bool) and v
                        },
                    )

        # MT5に存在しない陳腐化レコードを削除
        active_ids = {str(p.ticket) for p in positions}
        self._cleanup_stale_states(active_ids)

    async def _close_ghost_db_records(self, active_tickets: set[int]) -> None:
        """MT5に存在しないDBゴーストレコードを決済済みに更新

        エンジン停止中にMT5側で決済されたポジションの
        is_open=trueレコードをクリーンアップする。
        MT5口座履歴から正確な決済データを復元する。

        同期DB操作は asyncio.to_thread() で実行し、
        イベントループをブロックしない。

        Note:
            _active_symbolのレコードのみ対象。
            他シンボルのゴーストはfix_ghost_positions.pyで対応。

        Args:
            active_tickets: MT5で現在有効なチケットIDの集合
        """
        try:
            # 同期DB読み取りをスレッドプールで実行
            ghost_data = await asyncio.to_thread(
                self._fetch_ghost_records,
                active_tickets,
            )
            if not ghost_data:
                return

            # MT5履歴取得（非同期）でゴーストの決済データを収集
            updates: list[dict] = []
            for ticket, trade_id in ghost_data:
                deal = await self._executor.get_deal_by_position_id_async(
                    ticket
                )
                if deal:
                    closed_at = (
                        datetime.fromtimestamp(
                            deal["time"],
                            tz=UTC,
                        )
                        if deal["time"] > 0
                        else datetime.now(UTC)
                    )
                    updates.append(
                        {
                            "ticket": ticket,
                            "trade_id": trade_id,
                            "exit_price": deal["price"],
                            "profit_loss": deal["profit"],
                            "exit_reason": (
                                mt5_reason_to_exit_reason(deal["reason_code"])
                            ),
                            "closed_at": closed_at,
                        }
                    )
                    logger.info(
                        "ゴースト復元(MT5履歴):"
                        " ticket=%s reason=%s"
                        " price=%.5f profit=%.2f",
                        ticket,
                        updates[-1]["exit_reason"],
                        updates[-1]["exit_price"],
                        updates[-1]["profit_loss"],
                    )
                else:
                    # 決済約定が見つからない＝まだオープンの可能性
                    # MT5ポジション一覧が一時的に不完全だった場合に
                    # オープン中ポジションを誤閉鎖しないようスキップ
                    logger.info(
                        "ゴースト候補スキップ(決済履歴なし):"
                        " ticket=%s trade_id=%s",
                        ticket,
                        trade_id,
                    )

            # 同期DB更新をスレッドプールで実行
            if updates:
                await asyncio.to_thread(
                    self._apply_ghost_updates,
                    updates,
                )
                logger.info(
                    "ゴーストレコード %d件を is_open=false に更新",
                    len(updates),
                )
        except Exception as e:
            logger.warning("ゴーストレコード掃除スキップ: %s", e)

    def _fetch_ghost_records(
        self, active_tickets: set[int]
    ) -> list[tuple[int, str]]:
        """DBからゴーストレコードを同期取得

        イベントループをブロックしないよう
        asyncio.to_thread() から呼び出す。

        Args:
            active_tickets: MT5で有効なチケットIDの集合

        Returns:
            list[tuple[int, str]]: (ticket, trade_id) のリスト
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.models import (
            TradeRecord,
        )
        from autotrader.config.settings import get_settings

        db_url = get_settings().database_url
        with get_session(db_url) as db:
            records = (
                db.query(TradeRecord)
                .filter(
                    TradeRecord.is_open.is_(True),
                    TradeRecord.symbol == (self._active_symbol),
                )
                .all()
            )
            return [
                (r.ticket, r.trade_id)
                for r in records
                if r.ticket not in active_tickets
            ]

    def _apply_ghost_updates(self, updates: list[dict]) -> None:
        """ゴーストレコードの決済情報をDBに同期書き込み

        イベントループをブロックしないよう
        asyncio.to_thread() から呼び出す。

        Args:
            updates: 各ゴーストの更新データリスト
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.models import (
            TradeRecord,
        )
        from autotrader.config.settings import get_settings

        db_url = get_settings().database_url
        with get_session(db_url) as db:
            for upd in updates:
                record = (
                    db.query(TradeRecord)
                    .filter(
                        TradeRecord.ticket == upd["ticket"],
                        TradeRecord.is_open.is_(True),
                    )
                    .first()
                )
                if record is None:
                    continue
                record.is_open = False
                record.exit_reason = upd["exit_reason"]
                record.closed_at = upd["closed_at"]
                if upd["exit_price"] is not None:
                    record.exit_price = upd["exit_price"]
                if upd["profit_loss"] is not None:
                    record.profit_loss = upd["profit_loss"]
                if upd.get("profit_loss_pips") is not None:
                    record.profit_loss_pips = (
                        upd["profit_loss_pips"]
                    )
                if upd.get("final_stop_loss") is not None:
                    record.stop_loss = upd["final_stop_loss"]
                if upd.get("final_take_profit") is not None:
                    record.take_profit = (
                        upd["final_take_profit"]
                    )
            db.flush()

    def _restore_open_trades_from_db(self, tickets: list[int]) -> None:
        """DBからオープントレードのtrade_idを復元

        エンジン再起動時に _open_trades マッピングを
        DBの is_open=True レコードから復元する。
        自エンジンのシンボルに一致するレコードのみ復元する。

        Args:
            tickets: 現在のMT5チケットIDリスト
        """
        from autotrader.adapters.database.connection import (
            get_session,
        )
        from autotrader.adapters.database.models import (
            TradeRecord,
        )
        from autotrader.config.settings import get_settings

        try:
            db_url = get_settings().database_url
            with get_session(db_url) as db:
                records = (
                    db.query(TradeRecord)
                    .filter(
                        TradeRecord.is_open.is_(True),
                        TradeRecord.ticket.in_(tickets),
                        TradeRecord.symbol == self._active_symbol,
                    )
                    .all()
                )
                for r in records:
                    if r.ticket not in self._open_trades:
                        self._open_trades[r.ticket] = r.trade_id
                        logger.info(
                            "trade_id復元: ticket=%d trade_id=%s",
                            r.ticket,
                            r.trade_id,
                        )
        except Exception as e:
            logger.warning("trade_id復元スキップ: %s", e)

    # ==== ポジション管理状態の永続化 ====

    def _load_position_states(
        self,
    ) -> dict[str, dict]:
        """ローカルDBから全管理状態を取得

        Returns:
            dict[str, dict]: position_id→状態dictの辞書
        """
        from autotrader.adapters.database.connection import (
            get_local_session,
        )
        from autotrader.adapters.database.repositories import (
            PositionStateRepository,
        )

        result: dict[str, dict] = {}
        try:
            with get_local_session() as session:
                repo = PositionStateRepository(session)
                for rec in repo.get_all():
                    result[rec.position_id] = {
                        "position_id": rec.position_id,
                        "entry_own_score": rec.entry_own_score or 0.0,
                        "highest_price": rec.highest_price,
                        "lowest_price": rec.lowest_price,
                        "highest_r": rec.highest_r,
                        "bars_held": rec.bars_held,
                        "trailing_activated": (rec.trailing_activated),
                        "partial_closed_1r": (rec.partial_closed_1r),
                        "partial_closed_2r": (rec.partial_closed_2r),
                        "tp_disabled": rec.tp_disabled,
                        "early_be_applied": (rec.early_be_applied),
                        "insurance_sl_applied": (rec.insurance_sl_applied),
                        "insurance_partial_applied": (
                            rec.insurance_partial_applied
                        ),
                        "half_r_partial_applied": (rec.half_r_partial_applied),
                    }
        except Exception as e:
            logger.warning("管理状態ロードスキップ: %s", e)
        return result

    def _save_position_state(
        self,
        position_id: str,
    ) -> None:
        """ポジション管理状態をローカルDBに保存

        Args:
            position_id: ポジションID
        """
        from autotrader.adapters.database.connection import (
            get_local_session,
        )
        from autotrader.adapters.database.repositories import (
            PositionStateRepository,
        )

        state = self._pm.export_state(position_id)
        if state is None:
            return
        try:
            with get_local_session() as session:
                repo = PositionStateRepository(session)
                repo.upsert(state)
        except Exception as e:
            logger.warning(
                "管理状態保存エラー(pos=%s): %s",
                position_id,
                e,
            )

    def _delete_position_state(
        self,
        position_id: str,
    ) -> None:
        """ポジション管理状態をローカルDBから削除

        Args:
            position_id: ポジションID
        """
        from autotrader.adapters.database.connection import (
            get_local_session,
        )
        from autotrader.adapters.database.repositories import (
            PositionStateRepository,
        )

        try:
            with get_local_session() as session:
                repo = PositionStateRepository(session)
                repo.delete(position_id)
        except Exception as e:
            logger.warning(
                "管理状態削除エラー(pos=%s): %s",
                position_id,
                e,
            )

    def _cleanup_stale_states(
        self,
        active_ids: set[str],
    ) -> None:
        """MT5に存在しない陳腐化レコードを削除

        Args:
            active_ids: 現在MT5で有効なポジションIDの集合
        """
        from autotrader.adapters.database.connection import (
            get_local_session,
        )
        from autotrader.adapters.database.repositories import (
            PositionStateRepository,
        )

        try:
            with get_local_session() as session:
                repo = PositionStateRepository(session)
                stale_ids = [
                    rec.position_id
                    for rec in repo.get_all()
                    if rec.position_id not in active_ids
                ]
                for pid in stale_ids:
                    repo.delete(pid)
                    logger.info(
                        "陳腐化管理状態削除: %s",
                        pid,
                    )
        except Exception as e:
            logger.warning("陳腐化状態クリーンアップエラー: %s", e)

    # ==== ファンダメンタル統合メソッド ====

    def _init_fundamental(self, cfg: FundamentalConfig) -> None:
        """ファンダメンタル機能を初期化

        Args:
            cfg: FundamentalConfig
        """
        try:
            from autotrader.adapters.fundamental.collector import (
                FundamentalDataCollector,
            )
            from autotrader.adapters.fundamental.deterministic_event_analyzer import (  # noqa: E501
                DeterministicEventAnalyzer,
            )
            from autotrader.adapters.fundamental.memory import (
                FundamentalMemoryService,
            )

            # 決定論的イベント分析器（リアルタイム用）
            analyzer = DeterministicEventAnalyzer()

            # 共有コレクターがあれば再利用
            if self._shared_fundamental_collector:
                self._fundamental_collector = (
                    self._shared_fundamental_collector
                )
            else:
                self._fundamental_collector = FundamentalDataCollector(
                    fetch_interval_minutes=(cfg.fetch_interval_minutes),
                    use_mt5_calendar=(cfg.use_mt5_calendar),
                    use_forex_factory=(cfg.use_forex_factory),
                    use_ff_holidays=(cfg.use_ff_holidays),
                    use_exchange_calendars=(cfg.use_exchange_calendars),
                )
            self._fundamental_memory = FundamentalMemoryService(
                event_guard_minutes=cfg.event_guard_minutes,
                cached_events_getter=(
                    self._fundamental_collector.get_cached_events
                ),
                analyzer=analyzer,
            )
            # RSSニュース収集・分析（オプション）
            if cfg.use_rss_news:
                from autotrader.adapters.fundamental.news_llm_analyzer import (
                    NewsLLMAnalyzer,
                )
                from autotrader.adapters.fundamental.rss_collector import (
                    RSSCollector,
                )

                # 共有RSSコレクターがあれば再利用
                if self._shared_rss_collector:
                    self._rss_collector = self._shared_rss_collector
                else:
                    self._rss_collector = RSSCollector(
                        poll_interval=(cfg.rss_poll_interval_minutes * 60),
                    )
                self._news_analyzer = NewsLLMAnalyzer(
                    sentiment_ttl_hours=(cfg.rss_sentiment_ttl_hours),
                )
                logger.info("[Fundamental] RSSニュース機能初期化完了")

            logger.info("[Fundamental] ファンダメンタル機能初期化完了")
        except Exception as e:
            logger.error(
                "[Fundamental] 初期化失敗（無効化）: %s",
                e,
            )
            self._fundamental_memory = None
            self._fundamental_collector = None

    def _init_calendar_only(self) -> None:
        """カレンダー＋RSSの軽量初期化（ファンダメンタル無効時）

        MT5 MQL5サービス（CalendarExporter）のCSVからカレンダー取得。
        ForexFactoryは休日データのフォールバックとして使用。
        RSSニュースはDB/LLM不要で軽量ポーリング（タイトル+リンク表示用）。
        """
        try:
            from autotrader.adapters.fundamental.collector import (
                FundamentalDataCollector,
            )

            # 共有コレクターがあれば再利用
            if self._shared_fundamental_collector:
                self._fundamental_collector = (
                    self._shared_fundamental_collector
                )
            else:
                self._fundamental_collector = FundamentalDataCollector(
                    fetch_interval_minutes=60,
                    use_mt5_calendar=True,
                    use_forex_factory=False,
                    use_ff_holidays=False,
                    use_exchange_calendars=True,
                )
            logger.info(
                "[Calendar] 軽量カレンダー初期化完了"
                "（MT5 CSV + exchange_calendars休日）"
            )
        except Exception as e:
            logger.error("[Calendar] 軽量初期化失敗: %s", e)
            self._fundamental_collector = None

        # RSS軽量ポーリング（DB・LLM不要）
        try:
            from autotrader.adapters.fundamental.rss_collector import (
                RSSCollector,
            )

            # 共有RSSコレクターがあれば再利用
            if self._shared_rss_collector:
                self._rss_collector = self._shared_rss_collector
            else:
                self._rss_collector = RSSCollector(
                    poll_interval=300,
                )
            logger.info("[RSS] 軽量RSSポーリング初期化完了")
        except Exception as e:
            logger.warning("[RSS] RSS初期化スキップ: %s", e)
            self._rss_collector = None

        # カレンダーベースの指標前ブロック用メモリサービス初期化
        # analyzer=None: LLM分析なし、イベント検知のみ
        if self._fundamental_collector:
            try:
                from autotrader.adapters.fundamental.memory import (
                    FundamentalMemoryService,
                )

                self._fundamental_memory = FundamentalMemoryService(
                    event_guard_minutes=30,
                    cached_events_getter=(
                        self._fundamental_collector.get_cached_events
                    ),
                    analyzer=None,
                )
                logger.info(
                    "[Calendar] 指標前エントリーブロック有効化"
                )
            except Exception as e:
                logger.error(
                    "[Calendar] メモリサービス初期化失敗: %s",
                    e,
                )

    async def _start_fundamental_tasks(self) -> None:
        """ファンダメンタル収集タスクを起動

        共有コレクター使用時（_owns_collectors=False）は
        最初のエンジンが起動済みのため、起動をスキップする。
        """
        if not self._owns_collectors:
            return
        if self._fundamental_collector:
            await self._fundamental_collector.start()
            logger.info("[Fundamental] 収集タスク起動")
        if self._rss_collector:
            await self._rss_collector.start(callback=self._on_rss_news)
            logger.info("[Fundamental] RSSポーリング起動")

    async def _stop_fundamental_tasks(self) -> None:
        """ファンダメンタル収集タスクを停止

        共有コレクター使用時（_owns_collectors=False）は
        停止をスキップし、バッファのみクリアする。
        """
        if self._owns_collectors:
            if self._fundamental_collector:
                await self._fundamental_collector.stop()
            if self._rss_collector:
                await self._rss_collector.stop()
        self._news_buffer.clear()

    def get_news_for_symbol(
        self,
        symbol: str,
        limit: int = 50,
    ) -> list:
        """指定シンボルに関連するニュースをフィルタリング

        Args:
            symbol: 通貨ペアシンボル（例: USDJPY）
            limit: 最大取得件数

        Returns:
            list: フィルタ済みニュースアイテム
        """
        base = symbol[:3].upper()
        quote = symbol[3:6].upper()
        filtered = [
            n
            for n in self._news_buffer
            if base in n.currencies or quote in n.currencies
        ]
        filtered.sort(
            key=lambda n: getattr(n, "published_at", datetime.min),
            reverse=True,
        )
        return filtered[:limit]

    async def _on_rss_news(self, news_item) -> None:
        """RSSニュース受信コールバック

        受信したNewsItemをグローバルバッファに蓄積する。
        3日以上古いニュースは自動削除（メモリ軽量化）。
        WebSocket経由でダッシュボードにもリアルタイム配信する。

        Args:
            news_item: 受信したNewsItem
        """
        # 全ニュースをグローバルバッファに追加
        self._news_buffer.append(news_item)

        # active_symbol 関連のキーワードセンチメント分析・永続化
        symbol = self._active_symbol
        base = symbol[:3].upper()
        quote = symbol[3:6].upper()
        if base in news_item.currencies or quote in news_item.currencies:
            headlines = [
                n.title
                for n in self._news_buffer
                if base in n.currencies or quote in n.currencies
            ]
            if headlines:
                from autotrader.adapters.fundamental.sentiment_store import (
                    SentimentRecord,
                )

                result = self._keyword_scorer.score(
                    headlines,
                    symbol,
                )
                if result.headlines_used > 0:
                    record = SentimentRecord(
                        timestamp=datetime.now(
                            UTC,
                        ).isoformat(),
                        score=result.score,
                        method="keyword",
                        confidence=min(
                            result.headlines_used / 10,
                            1.0,
                        ),
                        news_count=result.headlines_used,
                        top_headlines=headlines[:3],
                    )
                    self._sentiment_store.save(
                        symbol,
                        record,
                    )

        # 3日超の古いニュースを削除
        _TTL_HOURS = 72
        now = datetime.now(UTC)
        self._news_buffer = [
            n
            for n in self._news_buffer
            if (now - getattr(n, "published_at", now)).total_seconds()
            < _TTL_HOURS * 3600
        ]
        # バッファ上限（メモリリーク防止）
        _MAX_BUFFER = 500
        if len(self._news_buffer) > _MAX_BUFFER:
            self._news_buffer = self._news_buffer[-_MAX_BUFFER:]
        # EventBus経由でダッシュボードにリアルタイム配信
        # （active_symbol 関連のみ配信）
        if base in news_item.currencies or quote in news_item.currencies:
            get_event_bus().publish_nowait(
                "news.received",
                {
                    "news_id": getattr(news_item, "news_id", ""),
                    "published_at": str(
                        getattr(news_item, "published_at", "")
                    ),
                    "title": getattr(news_item, "title", ""),
                    "source_name": getattr(news_item, "source_name", ""),
                    "source_url": getattr(news_item, "source_url", ""),
                    "currencies": getattr(news_item, "currencies", []),
                    "snippet": getattr(news_item, "snippet", None),
                    "symbol": symbol,
                },
            )

    @staticmethod
    def _blend_news_sentiment(
        ctx,
        sentiment: float,
        weight: float = 0.15,
    ):
        """ニュースセンチメントを FundamentalContext にブレンド

        バックテストの BacktestFundamentalProvider
        ._merge_news_into_context() と同じ重み（0.15）で
        direction_bias にブレンドする。

        Args:
            ctx: FundamentalContext
            sentiment: センチメントスコア (-1.0~+1.0)
            weight: ブレンド重み（デフォルト0.15）

        Returns:
            FundamentalContext: ブレンド済みコンテキスト
        """
        from dataclasses import replace

        blended_bias = ctx.direction_bias * (1.0 - weight) + sentiment * weight
        return replace(
            ctx,
            direction_bias=blended_bias,
            sentiment_score=sentiment,
        )

    async def _run_morning_update(self) -> None:
        """毎朝のLLM市場観更新

        UTC21時（日本時間6時）に実行。当日実行済みならスキップ。
        LLMが利用できない場合は警告ログのみ。
        """
        if not self._fundamental_memory:
            return

        now = datetime.now(UTC)
        today = now.date()

        # 当日実行済みチェック
        if (
            self._morning_update_done_date
            and self._morning_update_done_date == today
        ):
            return

        # 設定の更新時刻に達しているか確認
        cfg = self._config.fundamental_config
        if now.hour != cfg.morning_update_utc_hour:
            return

        try:
            from autotrader.adapters.ollama.client import OllamaClient

            llm_client = OllamaClient()

            # 現在価格取得
            symbol = self._active_symbol
            upcoming_events = self._fundamental_memory.get_upcoming_events(
                symbol,
                now,
                window_minutes=168,  # 7日間
            )
            upcoming_dicts = [
                {
                    "name": ev.event_name,
                    "minutes_until": ev.minutes_until(now),
                    "impact": ev.impact.value,
                }
                for ev in upcoming_events
            ]

            result = await llm_client.analyze_market_outlook_async(
                symbol=symbol,
                timestamp=now.isoformat(),
                current_price=0.0,  # 価格なしでも分析可能
                upcoming_events=upcoming_dicts,
                valid_days=7,
            )

            self._fundamental_memory.write_macro_bias(
                symbol=symbol,
                direction_score=result.direction_score,
                confidence=result.confidence,
                summary=result.macro_summary,
                llm_reasoning=str(result.key_factors),
            )
            self._morning_update_done_date = today
            logger.info(
                f"[Fundamental] 朝の市場観更新完了: "
                f"score={result.direction_score:+.2f}"
            )

        except Exception as e:
            logger.warning(f"[Fundamental] 朝の市場観更新失敗: {e}")

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
        """指標後バイアス分析を実行しDBに保存

        重要指標発表後30分以内に呼び出す。

        Args:
            event_name: イベント名
            currency: 通貨コード
            actual: 実績値
            forecast: 予測値
            previous: 前回値
            current_price: 現在価格
            price_change: 指標発表後の価格変化率
        """
        if not self._fundamental_memory:
            return

        try:
            from autotrader.adapters.ollama.client import OllamaClient

            llm_client = OllamaClient()
            now = datetime.now(UTC)
            symbol = self._active_symbol

            result = await llm_client.analyze_post_event_async(
                symbol=symbol,
                timestamp=now.isoformat(),
                event_name=event_name,
                currency=currency,
                actual=actual,
                forecast=forecast,
                previous=previous,
                current_price=current_price,
                price_change=price_change,
            )

            self._fundamental_memory.write_post_event_bias(
                symbol=symbol,
                direction_score=result.bias_score,
                confidence=0.7,
                summary=result.analysis[:100],
                source_event=event_name,
                llm_reasoning=result.analysis,
            )
            logger.info(
                f"[Fundamental] 指標後バイアス保存: "
                f"{event_name} score={result.bias_score:+.2f}"
            )

        except Exception as e:
            logger.warning(f"[Fundamental] 指標後分析失敗: {e}")

    # ------------------------------------------------------------------
    # ホットリロード
    # ------------------------------------------------------------------

    async def reload_trade_logic(self) -> dict:
        """トレードロジックをホットリロードする

        既存ポジションの継続保証:
        - _entry_blocked=True でエントリーを一時停止
        - モジュールリロード後に新インスタンスを生成
        - _sync_positions() で状態を復元（WebUI再起動と同一パス）
        - 完了後 _entry_blocked=False に戻す
        - エラー時は旧インスタンスへロールバック

        Returns:
            dict: {"success": bool, "reloaded_at": str, "error": str | None}
        """
        # 既にリロード中なら即時返却
        if self._reload_lock.locked():
            return {
                "success": False,
                "error": "リロード実行中です",
                "reloaded_at": None,
            }

        async with self._reload_lock:
            # 旧インスタンスを退避（ロールバック用）
            old_bot = self._bot
            old_pm = self._pm
            old_sizer = self._sizer

            self._entry_blocked = True
            try:
                await asyncio.wait_for(
                    self._do_reload(),
                    timeout=self._config.reload_config.reload_timeout_sec,
                )
                self._last_reload_at = datetime.now(UTC)
                self._reloader.mark_reloaded()
                get_event_bus().publish_nowait(
                    "logic.reloaded",
                    {
                        "symbol": self._active_symbol,
                        "reloaded_at": (
                            self._last_reload_at.isoformat()
                        ),
                    },
                )
                logger.info(
                    "ホットリロード完了: symbol=%s",
                    self._active_symbol,
                )
                return {
                    "success": True,
                    "error": None,
                    "reloaded_at": self._last_reload_at.isoformat(),
                }
            except Exception as e:
                # 旧インスタンスへロールバック
                self._bot = old_bot
                self._pm = old_pm
                self._sizer = old_sizer
                logger.error(
                    "ホットリロード失敗 — ロールバック: %s", e, exc_info=True
                )
                get_event_bus().publish_nowait(
                    "logic.reload_failed",
                    {
                        "symbol": self._active_symbol,
                        "error": str(e),
                    },
                )
                return {
                    "success": False,
                    "error": str(e),
                    "reloaded_at": None,
                }
            finally:
                self._entry_blocked = False

    async def _do_reload(self) -> None:
        """実際のリロード処理（reload_trade_logic の内部実装）"""
        # モジュールをリロード（1回のみ実行 — sys.modules 共有）
        self._reloader.reload_modules()

        # 新インスタンスを動的インポートで生成
        new_bot = self._reloader.create_new_bot(
            self._config.bot_config
        )
        new_pm = self._reloader.create_new_pm(
            self._config.symbol,
        )
        sizer_config = self._build_sizer_config(
            self._config.bot_config, self._config.symbol
        )
        new_sizer = self._reloader.create_new_sizer(sizer_config)

        # インスタンスを差し替え
        self._bot = new_bot
        self._pm = new_pm
        self._sizer = new_sizer

        # ポジション状態を復元（WebUI再起動と同一パス）
        await self._sync_positions()

    async def _auto_reload_loop(self) -> None:
        """ファイル変更を定期ポーリングしてホットリロードを制御する

        auto_reload_on_change=True 時: 変更検知→自動リロード実行
        auto_reload_on_change=False 時: 変更検知→WebSocketに通知のみ
        """
        interval = (
            self._config.reload_config.auto_reload_poll_interval_sec
        )
        while self._running:
            await asyncio.sleep(interval)
            try:
                changed = self._reloader.check_changed()
                if not changed:
                    continue

                logger.info(
                    "トレードロジック変更検知: %d ファイル",
                    len(changed),
                )
                get_event_bus().publish_nowait(
                    "logic.change_detected",
                    {
                        "symbol": self._active_symbol,
                        "changed_files": changed,
                    },
                )

                if self._config.reload_config.auto_reload_on_change:
                    logger.info("自動ホットリロード開始")
                    await self.reload_trade_logic()
                else:
                    # 通知のみモード: スナップショットを更新して
                    # 次回ポーリングで同じ変更を再検知しないようにする
                    self._reloader.mark_reloaded()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    "変更検知ループエラー: %s", e, exc_info=True
                )
