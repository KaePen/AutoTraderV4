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
from autotrader.live.config import FundamentalConfig, LiveTradingConfig
from autotrader.decision.unified.signal_consolidator import (
    ConsolidatedSignal,
)
from autotrader.live.tick_entry_optimizer import TickEntryOptimizer
from autotrader.calculator.technical.batch import TechnicalIndicatorBatch

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
        # クローズ済みトレード履歴（インメモリ）
        self._closed_trades: list[dict] = []
        # MT5 tick高速ポーリング用（最終tickのms単位時刻）
        self._last_mt5_tick_ms: int = 0
        # 直近tick価格キャッシュ（_tick_price_update→_update_market_dataで共用）
        self._last_tick_data: dict | None = None
        # フル処理（ローソク足+指標+シグナル）最終実行時刻
        self._last_full_tick_time: float = 0.0

        # ファンダメンタル関連（FundamentalConfig.enabled=Trueのみ初期化）
        self._fundamental_memory = None
        self._fundamental_collector = None
        self._morning_update_done_date: datetime | None = None
        if config.fundamental_config.enabled:
            self._init_fundamental(config.fundamental_config)

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
        """クローズ済みトレード履歴"""
        return self._closed_trades

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

    def get_current_entry_threshold(
        self, mode_str: str | None = None,
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
            self._build_sizer_config(new_config, self._config.symbol)
        )
        logger.info(
            "BotConfig更新完了 demo_mode=%s", new_config.demo_mode
        )

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
        return 0.01 if "JPY" in symbol.upper() else 0.0001

    @staticmethod
    def _get_pip_value(symbol: str) -> float:
        """通貨ペアの1lot/1pipあたりの価値を返す（JPY系=1000、その他=10）

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            float: pip価値（円）
        """
        return 1000.0 if "JPY" in symbol.upper() else 10.0

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
                    await self._tick_price_update()
            except Exception as e:
                logger.error(
                    "ティック処理エラー: %s", e, exc_info=True
                )
            await asyncio.sleep(0.1)

    async def _tick_price_update(self) -> None:
        """軽量tick処理: MT5のbid/askを取得して価格をbroadcast

        ローソク足取得・指標計算を行わない高速版。
        前回と同じtickであればbroadcastをスキップする。
        """
        try:
            tick = await self._data_provider.get_tick_fast(
                self._config.symbol
            )
        except Exception:
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

        try:
            from autotrader.web.websocket.handlers import (
                broadcast_price_update,
            )
            asyncio.create_task(
                broadcast_price_update({
                    "symbol": self._config.symbol,
                    "bid": bid,
                    "ask": ask,
                    "time_ms": tick_ms,
                })
            )
        except Exception:
            pass

    async def _tick(self) -> None:
        """1ティック分の処理

        口座情報→ローソク足→ポジション管理→シグナル生成→エントリー判定
        ポジション管理を先行させることで、SL/TP発動等による
        ポジション減少を即座にエントリー判断へ反映する。
        """
        # 1. 口座情報更新
        self._account_info = (
            await self._data_provider.get_account_info()
        )

        # 2. 最新ローソク足データ取得・設定
        await self._update_market_data()

        # 3. ポジション管理（シグナル生成前に実行）
        # SL/TP発動・手動決済による減少を_cached_positionsへ即時反映し、
        # 同一tick内のエントリー判断で最新ポジション数を使えるようにする。
        await self._manage_positions()

        # [FUNDAMENTAL] ファンダメンタルコンテキスト取得・指標前スキップ
        now_utc = datetime.now(timezone.utc)
        if self._fundamental_memory:
            fundamental_ctx = (
                self._fundamental_memory.get_context_for_llm(
                    self._config.symbol, now_utc
                )
            )
            if fundamental_ctx.has_high_impact_within_30min:
                logger.info(
                    "[Fundamental] 重要指標直前のためスキップ"
                )
                return
        else:
            fundamental_ctx = None

        # 4. シグナル生成
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

        # 5. tick完了: 全UIデータをWebSocketで一括配信
        asyncio.create_task(self._broadcast_tick_update())

    async def _broadcast_tick_update(self) -> None:
        """tick完了後に全UIデータをダッシュボードへ一括配信

        analysis / account / positions / radar を1ペイロードで送信。
        フロントエンドはこのイベントを受信してUIを全更新する。
        """
        try:
            from autotrader.web.websocket.handlers import (
                broadcast_tick_update,
            )
            payload = self._build_tick_payload()
            await broadcast_tick_update(payload)
        except Exception:
            pass  # ブロードキャスト失敗は無視

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
                    k: dict(v)
                    for k, v
                    in cs.tf_score_breakdowns.items()
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
            sym: sorted(
                sigs, key=lambda x: x.confidence, reverse=True
            )
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

        return {
            "analysis": analysis,
            "account": account,
            "positions": self._cached_positions,
            "radar": radar_serialized,
            "indicators": indicators,
        }

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int,
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
            all_data = self._calc_indicators(all_data)
            self._bot.set_market_data(all_data)
            logger.info(
                "全TFデータ設定完了: %d時間足", len(all_data)
            )

    async def _update_market_data(self) -> None:
        """最新ローソク足データを取得してTradeBotに設定

        時間足確定を待たずリアルタイム評価するため、全TFの最後の
        バーのclose/high/lowを現在のtick価格で上書きしてから
        インジケータを再計算する。
        """
        symbol = self._config.symbol
        # 全TFのデータを一括収集してから設定
        # sma_50計算に50本必要なためバッファを含め200本取得
        # （個別set_market_dataは辞書を上書きするため）
        all_data: dict[str, pd.DataFrame] = {}
        for tf_str in self._bot.timeframes:
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                continue

            df = await self._data_provider.get_candles_from_pos(
                symbol, tf, 200
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
                logger.warning(
                    "指標計算失敗: %s %s", tf, e
                )
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
        # 既存ポジションチェック（設定値に基づく上限）
        positions = await self._executor.get_open_positions_async(
            self._config.symbol
        )
        cfg = self._bot.config
        base_max = (
            cfg.demo_max_positions
            if cfg.demo_mode
            else cfg.max_positions
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
            trade_id = ""
            if result.ticket:
                await self._register_new_position(
                    result.ticket, signal_with_lot, lot, entry_tick
                )
                # DB書き込み（エントリー記録）
                trade_id = self._write_entry_to_db(
                    result.ticket, signal_with_lot, lot, entry_tick
                ) or ""
                if trade_id:
                    self._open_trades[result.ticket] = trade_id

            # _cached_positionsに即時追加（次tick待ち不要）
            entry_price = signal_with_lot.stop_loss or 0.0
            if entry_tick:
                price_key = (
                    "ask"
                    if signal_with_lot.signal_type == SignalType.BUY
                    else "bid"
                )
                entry_price = float(
                    entry_tick.get(price_key, 0)
                ) or entry_price
            self._cached_positions.append({
                "position_id": str(result.ticket or 0),
                "trade_id": trade_id,
                "ticket": result.ticket or 0,
                "symbol": self._config.symbol,
                "signal_type": (
                    signal_with_lot.signal_type.value
                ),
                "volume": lot,
                "entry_price": entry_price,
                "current_price": entry_price,
                "stop_loss": signal_with_lot.stop_loss,
                "take_profit": signal_with_lot.take_profit,
                "opened_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "unrealized_pnl": 0.0,
                "unrealized_pnl_pips": 0.0,
            })

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
            TradingModeSelector,
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
        pip_size = self._get_pip_size(signal.symbol)
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

        # signal.modeから実際のモードを解決（デフォルト: UNIVERSAL）
        mode = TradingStrategyMode.UNIVERSAL
        if signal.mode:
            try:
                mode = TradingStrategyMode(signal.mode.upper())
            except ValueError:
                logger.warning(
                    "不明なモード: %s、UNIVERSALを使用",
                    signal.mode,
                )

        # ModeSelector経由でモード別プランパラメータを取得し
        # regimeとselection_reasonを付与
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
            entry_time=datetime.now(timezone.utc),
            sl=sl_price,
            tp=tp_price,
            volume=volume,
            plan=plan,
        )

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
                    entry_tick.get("ask", 0) if is_buy
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
                        entry_price - sl_dist if is_buy
                        else entry_price + sl_dist
                    )
                if signal.take_profit and signal.take_profit > 0:
                    tp_dist = signal.take_profit * pip_size
                    tp_price = (
                        entry_price + tp_dist if is_buy
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
                    opened_at=datetime.now(timezone.utc),
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    ticket=ticket,
                )
                trade_id = trade.trade_id
            logger.info(
                "DB記録（エントリー）: trade_id=%s ticket=%d",
                trade_id, ticket,
            )
            return trade_id
        except Exception as e:
            logger.error("DB書き込みエラー（エントリー）: %s", e)
            return None

    async def _handle_external_close(
        self, ticket: int
    ) -> None:
        """外部決済（SL/TP/手動）をMT5約定履歴から取得してDB記録。

        Args:
            ticket: MT5ポジションID
        """
        logger.info(
            "外部決済検出（手動/SL/TP）: ticket=%d", ticket
        )
        from autotrader.core.enums import ExitReason
        deal = await self._executor.get_deal_by_position_async(
            ticket
        )
        if deal:
            exit_price = deal["price"]
            profit_loss = deal["profit"]
            # MT5 DEAL_REASON: 4=SL, 5=TP, その他=手動/外部
            rc = deal["reason_code"]
            if rc == 4:
                exit_reason = ExitReason.STOP_LOSS.value
            elif rc == 5:
                exit_reason = ExitReason.TAKE_PROFIT.value
            else:
                exit_reason = ExitReason.EXTERNAL_CLOSE.value
            logger.info(
                "外部決済詳細: ticket=%d reason=%s"
                " price=%.5f profit=%.2f",
                ticket, exit_reason, exit_price, profit_loss,
            )
        else:
            # 約定履歴取得失敗時はティック価格をフォールバックに使用
            exit_price = 0.0
            try:
                tick = await self._data_provider.get_tick(
                    self._config.symbol
                )
                _bid = float(tick.get("bid", 0))
                _ask = float(tick.get("ask", 0))
                # 方向不明のためmid価格をフォールバック
                if _bid > 0 and _ask > 0:
                    exit_price = (_bid + _ask) / 2
                elif _bid > 0:
                    exit_price = _bid
            except Exception:
                pass
            profit_loss = 0.0
            exit_reason = ExitReason.EXTERNAL_CLOSE.value
            logger.warning(
                "外部決済の約定履歴取得失敗: ticket=%d"
                " → フォールバック価格=%.5f で記録",
                ticket, exit_price,
            )
        self._write_close_to_db(
            ticket, exit_price, exit_reason, profit_loss
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
        trade_id = self._open_trades.pop(ticket, None)
        if not trade_id:
            return
        try:
            pos = self._pm.get_position(str(ticket))
            pnl_pips = 0.0
            if pos and current_price > 0:
                pip_size = self._get_pip_size(self._config.symbol)
                price_diff = (
                    current_price - pos.entry_price
                    if pos.direction == SignalType.BUY
                    else pos.entry_price - current_price
                )
                pnl_pips = price_diff / pip_size
                # MT5から損益が取得できなかった場合、
                # pnl_pipsとvolumeから概算（スプレッド・スワップ除く）
                if profit_loss == 0.0 and abs(pnl_pips) > 0:
                    pip_val = self._get_pip_value(self._config.symbol)
                    # ManagedPositionはremaining_volumeを使用
                    _vol = pos.remaining_volume
                    profit_loss = round(
                        pnl_pips * _vol * pip_val, 2
                    )
            closed_at = datetime.now(timezone.utc)
            db_url = get_settings().database_url
            with get_session(db_url) as db:
                repo = TradeRepository(db)
                trade_record = repo.get_by_id(trade_id)
                if trade_record:
                    repo.close(
                        trade=trade_record,
                        exit_price=current_price,
                        closed_at=closed_at,
                        exit_reason=action_reason,
                        profit_loss=profit_loss,
                        profit_loss_pips=pnl_pips,
                    )
            self._closed_trades.append({
                "trade_id": trade_id,
                "ticket": ticket,
                "exit_price": current_price,
                "exit_reason": action_reason,
                "pnl_pips": round(pnl_pips, 1),
                "closed_at": closed_at.isoformat(),
            })
            logger.info(
                "DB記録（決済）: trade_id=%s ticket=%d"
                " pnl_pips=%.1f profit_loss=%.2f",
                trade_id, ticket, pnl_pips, profit_loss,
            )
            # WebSocketで決済イベントをUIに即時通知
            try:
                from autotrader.web.websocket.handlers import (
                    broadcast_position_update,
                )
                asyncio.get_running_loop().create_task(
                    broadcast_position_update(
                        {"symbol": self._config.symbol}
                    )
                )
            except Exception:
                pass
        except Exception as e:
            logger.error("DB書き込みエラー（決済）: %s", e)

    async def _manage_positions(self) -> None:
        """既存ポジションの管理

        PositionManager.evaluateで各ポジションを評価し、
        SL変更・部分決済・全決済をMT5で実行。
        _cached_positionsをMT5の現在状態で更新する。
        """
        # 全通貨ペアのポジションを取得（UI表示用）
        positions = await self._executor.get_open_positions_async(
            None
        )
        current_tickets = {pos.ticket for pos in positions}

        # _open_tradesが未復元の場合、DBから復元（起動タイミング対応）
        if not self._open_trades and positions:
            self._restore_open_trades_from_db(
                [pos.ticket for pos in positions]
            )

        # 外部決済（手動/SL/TP）の検出:
        # _open_tradesにあるが現在MT5に存在しないticket
        if self._open_trades:
            externally_closed = (
                set(self._open_trades.keys()) - current_tickets
            )
            for ticket in externally_closed:
                await self._handle_external_close(ticket)

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

        cache_list: list[dict] = []
        for position in positions:
            # 通貨ペア別にpip計算（全通貨ペア対応）
            pip_factor = self._get_pip_size(position.symbol)
            pip_value = self._get_pip_value(position.symbol)

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
                "trade_id": self._open_trades.get(
                    position.ticket, ""
                ),
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
                except Exception:
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
                # DB記録後にPMからポジションを削除
                self._pm.unregister_position(
                    str(position.ticket)
                )

    async def _sync_positions(self) -> None:
        """MT5の既存ポジションとPositionManagerを同期

        エンジン起動時に呼び出される。
        DBから is_open=True のレコードを検索して
        _open_trades（ticket→trade_id）を復元する。
        """
        positions = await self._executor.get_open_positions_async(
            self._config.symbol
        )
        if not positions:
            logger.info("同期対象ポジションなし")
            return

        # DBからopenトレードを復元（再起動対応）
        self._restore_open_trades_from_db(
            [pos.ticket for pos in positions]
        )

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
                    mode=TradingStrategyMode.UNIVERSAL,
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

    def _restore_open_trades_from_db(
        self, tickets: list[int]
    ) -> None:
        """DBからオープントレードのtrade_idを復元

        エンジン再起動時に _open_trades マッピングを
        DBの is_open=True レコードから復元する。

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
                    )
                    .all()
                )
                for r in records:
                    if r.ticket not in self._open_trades:
                        self._open_trades[r.ticket] = (
                            r.trade_id
                        )
                        logger.info(
                            "trade_id復元: ticket=%d"
                            " trade_id=%s",
                            r.ticket, r.trade_id,
                        )
        except Exception as e:
            logger.warning(
                "trade_id復元スキップ: %s", e
            )

    # ==== ファンダメンタル統合メソッド ====

    def _init_fundamental(
        self, cfg: FundamentalConfig
    ) -> None:
        """ファンダメンタル機能を初期化

        Args:
            cfg: FundamentalConfig
        """
        try:
            from autotrader.adapters.fundamental.memory import (
                FundamentalMemoryService,
            )
            from autotrader.adapters.fundamental.collector import (
                FundamentalDataCollector,
            )
            from autotrader.adapters.database.connection import (
                DatabaseManager,
            )

            # DBセッションファクトリー取得
            db_manager = DatabaseManager.get_instance()

            self._fundamental_collector = FundamentalDataCollector(
                session_factory=db_manager.get_session,
                fetch_interval_minutes=cfg.fetch_interval_minutes,
                use_mt5_calendar=cfg.use_mt5_calendar,
                use_forex_factory=cfg.use_forex_factory,
            )
            self._fundamental_memory = FundamentalMemoryService(
                session_factory=db_manager.get_session,
                event_guard_minutes=cfg.event_guard_minutes,
                cached_events_getter=(
                    self._fundamental_collector.get_cached_events
                ),
            )
            logger.info(
                "[Fundamental] ファンダメンタル機能初期化完了"
            )
        except Exception as e:
            logger.error(
                f"[Fundamental] 初期化失敗（無効化）: {e}"
            )
            self._fundamental_memory = None
            self._fundamental_collector = None

    async def _start_fundamental_tasks(self) -> None:
        """ファンダメンタル収集タスクを起動"""
        if self._fundamental_collector:
            await self._fundamental_collector.start()
            logger.info(
                "[Fundamental] 収集タスク起動"
            )

    async def _stop_fundamental_tasks(self) -> None:
        """ファンダメンタル収集タスクを停止"""
        if self._fundamental_collector:
            await self._fundamental_collector.stop()

    async def _run_morning_update(self) -> None:
        """毎朝のLLM市場観更新

        UTC21時（日本時間6時）に実行。当日実行済みならスキップ。
        LLMが利用できない場合は警告ログのみ。
        """
        if not self._fundamental_memory:
            return

        now = datetime.now(timezone.utc)
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
            symbol = self._config.symbol
            upcoming_events = (
                self._fundamental_memory.get_upcoming_events(
                    symbol, now, window_minutes=168  # 7日間
                )
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
            logger.warning(
                f"[Fundamental] 朝の市場観更新失敗: {e}"
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
            now = datetime.now(timezone.utc)
            symbol = self._config.symbol

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
            logger.warning(
                f"[Fundamental] 指標後分析失敗: {e}"
            )
