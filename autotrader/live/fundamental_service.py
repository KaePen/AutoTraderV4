"""ファンダメンタルデータサービス

カレンダー・ニュース・RSS・LLM分析を管理する独立サービス。
engine.py から分離し、WebUI ルーターとトレードロジックの
両方から利用可能にする。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from autotrader.core.event_bus import event_bus
from autotrader.live.config import FundamentalConfig

logger = logging.getLogger(__name__)

# ニュースバッファ上限
_MAX_BUFFER = 200
# ニュースTTL（時間）
_TTL_HOURS = 72


class FundamentalDataService:
    """ファンダメンタルデータ管理サービス

    カレンダーイベント・RSSニュース・LLM分析を一元管理する。
    engine.py から独立しており、DIで各コンポーネントに注入される。

    Attributes:
        _symbol: 対象通貨ペア
        _config: ファンダメンタル設定
        _fundamental_collector: カレンダーイベント収集
        _fundamental_memory: ファンダメンタルコンテキスト管理
        _rss_collector: RSSニュース収集
        _news_analyzer: ニュースLLM分析
        _news_buffer: シンボル別ニュースバッファ
    """

    def __init__(
        self,
        symbol: str,
        config: FundamentalConfig,
    ) -> None:
        """初期化

        Args:
            symbol: 対象通貨ペア
            config: ファンダメンタル設定
        """
        self._symbol = symbol
        self._config = config
        self._fundamental_collector = None
        self._fundamental_memory = None
        self._rss_collector = None
        self._news_analyzer = None
        self._news_buffer: dict[str, list] = {}
        self._morning_update_done_date: datetime | None = None

        if config.enabled:
            self._init_full(config)
        else:
            self._init_calendar_only()

    # --- 公開API ---

    def get_cached_calendar_events(self) -> list:
        """キャッシュ済みカレンダーイベントを返す

        Returns:
            list: EconomicEvent のリスト
        """
        if self._fundamental_collector is None:
            return []
        try:
            return self._fundamental_collector.get_cached_events()
        except Exception:
            return []

    def get_news_buffer(self, symbol: str) -> list:
        """シンボル別ニュースバッファを返す

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            list: NewsItem のリスト
        """
        return list(self._news_buffer.get(symbol, []))

    def get_fundamental_context(
        self,
        symbol: str,
        now_utc: datetime,
    ):
        """ファンダメンタルコンテキストを取得

        Args:
            symbol: 通貨ペアシンボル
            now_utc: 現在時刻(UTC)

        Returns:
            FundamentalContext | None
        """
        if self._fundamental_memory is None:
            return None
        return self._fundamental_memory.get_context_for_llm(
            symbol, now_utc
        )

    @property
    def fundamental_memory(self):
        """FundamentalMemoryServiceへの参照（LLM更新用）"""
        return self._fundamental_memory

    @property
    def news_analyzer(self):
        """NewsLLMAnalyzerへの参照"""
        return self._news_analyzer

    @property
    def config(self) -> FundamentalConfig:
        """ファンダメンタル設定"""
        return self._config

    def consume_news_for_analysis(
        self, symbol: str
    ) -> list:
        """分析用にニュースバッファを取得しクリア

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            list: NewsItem のリスト（クリア前のコピー）
        """
        items = list(self._news_buffer.get(symbol, []))
        self._news_buffer[symbol] = []
        return items

    # --- ライフサイクル ---

    async def start(self) -> None:
        """収集タスクを起動"""
        if self._fundamental_collector:
            await self._fundamental_collector.start()
            logger.info(
                "[Fundamental] 収集タスク起動"
            )
        if self._rss_collector:
            await self._rss_collector.start(
                callback=self._on_rss_news
            )
            logger.info(
                "[Fundamental] RSSポーリング起動"
            )

    async def stop(self) -> None:
        """収集タスクを停止"""
        if self._fundamental_collector:
            await self._fundamental_collector.stop()
        if self._rss_collector:
            await self._rss_collector.stop()
        self._news_buffer.clear()

    # --- 初期化（内部） ---

    def _init_full(self, cfg: FundamentalConfig) -> None:
        """フル機能初期化（DB + LLM対応）

        Args:
            cfg: FundamentalConfig
        """
        try:
            import functools

            from autotrader.adapters.database.connection import (
                get_session,
            )
            from autotrader.adapters.fundamental.collector import (
                FundamentalDataCollector,
            )
            from autotrader.adapters.fundamental.deterministic_event_analyzer import (  # noqa: E501
                DeterministicEventAnalyzer,
            )
            from autotrader.adapters.fundamental.memory import (
                FundamentalMemoryService,
            )
            from autotrader.config.settings import (
                get_settings,
            )

            db_url = get_settings().database_url
            # settings の URL を束縛したセッションファクトリ
            session_factory = functools.partial(
                get_session, db_url
            )

            # 決定論的イベント分析器（リアルタイム用）
            analyzer = DeterministicEventAnalyzer()

            self._fundamental_collector = (
                FundamentalDataCollector(
                    fetch_interval_minutes=(
                        cfg.fetch_interval_minutes
                    ),
                    use_mt5_calendar=cfg.use_mt5_calendar,
                    use_forex_factory=cfg.use_forex_factory,
                    use_ff_holidays=cfg.use_ff_holidays,
                )
            )
            self._fundamental_memory = (
                FundamentalMemoryService(
                    session_factory=session_factory,
                    event_guard_minutes=(
                        cfg.event_guard_minutes
                    ),
                    cached_events_getter=(
                        self._fundamental_collector.get_cached_events
                    ),
                    analyzer=analyzer,
                )
            )
            # RSSニュース収集・分析（オプション）
            if cfg.use_rss_news:
                from autotrader.adapters.fundamental.news_llm_analyzer import (  # noqa: E501
                    NewsLLMAnalyzer,
                )
                from autotrader.adapters.fundamental.rss_collector import (  # noqa: E501
                    RSSCollector,
                )

                # シンボルから通貨コードを抽出
                currencies = [
                    self._symbol[:3],
                    self._symbol[3:6],
                ]
                self._rss_collector = RSSCollector(
                    currencies=currencies,
                    poll_interval=(
                        cfg.rss_poll_interval_minutes * 60
                    ),
                )
                self._news_analyzer = NewsLLMAnalyzer(
                    sentiment_ttl_hours=(
                        cfg.rss_sentiment_ttl_hours
                    ),
                )
                logger.info(
                    "[Fundamental] RSSニュース機能初期化完了"
                )

            logger.info(
                "[Fundamental] ファンダメンタル機能初期化完了"
            )
        except Exception as e:
            logger.error(
                "[Fundamental] 初期化失敗（無効化）: %s", e
            )
            self._fundamental_memory = None
            self._fundamental_collector = None

    def _init_calendar_only(self) -> None:
        """カレンダー＋RSSの軽量初期化"""
        try:
            from autotrader.adapters.fundamental.collector import (
                FundamentalDataCollector,
            )

            self._fundamental_collector = (
                FundamentalDataCollector(
                    fetch_interval_minutes=60,
                    use_mt5_calendar=True,
                    use_forex_factory=False,
                    use_ff_holidays=True,
                )
            )
            logger.info(
                "[Calendar] 軽量カレンダー初期化完了"
                "（MT5 CSV + FF休日）"
            )
        except Exception as e:
            logger.error(
                "[Calendar] 軽量初期化失敗: %s", e
            )
            self._fundamental_collector = None

        try:
            from autotrader.adapters.fundamental.rss_collector import (  # noqa: E501
                RSSCollector,
            )

            currencies = [
                self._symbol[:3],
                self._symbol[3:6],
            ]
            self._rss_collector = RSSCollector(
                currencies=currencies,
                poll_interval=300,
            )
            logger.info(
                "[RSS] 軽量RSSポーリング初期化完了"
            )
        except Exception as e:
            logger.warning(
                "[RSS] RSS初期化スキップ: %s", e
            )
            self._rss_collector = None

    # --- コールバック ---

    async def _on_rss_news(self, news_item) -> None:
        """RSSニュース受信コールバック

        Args:
            news_item: 受信したNewsItem
        """
        symbol = self._symbol
        base = symbol[:3].upper()
        quote = symbol[3:6].upper()
        if (
            base in news_item.currencies
            or quote in news_item.currencies
        ):
            if symbol not in self._news_buffer:
                self._news_buffer[symbol] = []
            self._news_buffer[symbol].append(news_item)
            # TTLに基づく古いニュース削除
            now = datetime.now(UTC)
            self._news_buffer[symbol] = [
                n
                for n in self._news_buffer[symbol]
                if (
                    now
                    - getattr(n, "published_at", now)
                ).total_seconds()
                < _TTL_HOURS * 3600
            ]
            # バッファ上限
            if (
                len(self._news_buffer[symbol])
                > _MAX_BUFFER
            ):
                self._news_buffer[symbol] = (
                    self._news_buffer[symbol][
                        -_MAX_BUFFER:
                    ]
                )
            # EventBus経由でダッシュボードにリアルタイム配信
            event_bus.publish_nowait(
                "news.received",
                {
                    "news_id": getattr(
                        news_item, "news_id", ""
                    ),
                    "published_at": str(
                        getattr(
                            news_item,
                            "published_at",
                            "",
                        )
                    ),
                    "title": getattr(
                        news_item, "title", ""
                    ),
                    "source_name": getattr(
                        news_item, "source_name", ""
                    ),
                    "source_url": getattr(
                        news_item, "source_url", ""
                    ),
                    "currencies": getattr(
                        news_item, "currencies", []
                    ),
                    "snippet": getattr(
                        news_item, "snippet", None
                    ),
                    "symbol": symbol,
                },
            )
