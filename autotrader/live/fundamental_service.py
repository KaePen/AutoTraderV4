"""ファンダメンタルデータサービス

経済指標カレンダー・RSSニュース・LLM市場分析を担当する。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from autotrader.core.event_bus import event_bus
from autotrader.live.config import FundamentalConfig

logger = logging.getLogger(__name__)


class FundamentalService:
    """ファンダメンタルデータ管理サービス

    経済指標カレンダー取得・RSSニュース収集・
    LLM市場分析の初期化/起動/停止を行う。

    Attributes:
        _fundamental_memory: FundamentalMemoryService
        _fundamental_collector: FundamentalDataCollector
        _rss_collector: RSSCollector
        _news_analyzer: NewsLLMAnalyzer
        _news_buffer: ニュースバッファ
        _keyword_scorer: キーワードセンチメントスコアラ
        _sentiment_store: センチメント永続化ストア
    """

    def __init__(
        self,
        shared_fundamental_collector=None,
        shared_rss_collector=None,
    ) -> None:
        """初期化

        Args:
            shared_fundamental_collector: 共有コレクター
            shared_rss_collector: 共有RSSコレクター
        """
        self.fundamental_memory = None
        self.fundamental_collector = None
        self.morning_update_done_date: datetime | None = None
        self.rss_collector = None
        self.news_analyzer = None
        self.news_buffer: list = []
        self._shared_fundamental_collector = shared_fundamental_collector
        self._shared_rss_collector = shared_rss_collector
        self._owns_collectors = shared_fundamental_collector is None

        # キーワードセンチメント分析・永続化（常時有効）
        from autotrader.adapters.fundamental.keyword_sentiment import (
            KeywordSentimentScorer,
        )
        from autotrader.adapters.fundamental.sentiment_store import (
            SentimentStore,
        )

        self.keyword_scorer = KeywordSentimentScorer()
        self.sentiment_store = SentimentStore()

    def init_fundamental(
        self,
        cfg: FundamentalConfig,
    ) -> None:
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

            analyzer = DeterministicEventAnalyzer()

            if self._shared_fundamental_collector:
                self.fundamental_collector = self._shared_fundamental_collector
            else:
                self.fundamental_collector = FundamentalDataCollector(
                    fetch_interval_minutes=(cfg.fetch_interval_minutes),
                    use_mt5_calendar=(cfg.use_mt5_calendar),
                    use_forex_factory=(cfg.use_forex_factory),
                    use_ff_holidays=(cfg.use_ff_holidays),
                    use_exchange_calendars=(cfg.use_exchange_calendars),
                )
            self.fundamental_memory = FundamentalMemoryService(
                event_guard_minutes=(cfg.event_guard_minutes),
                cached_events_getter=(
                    self.fundamental_collector.get_cached_events
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

                if self._shared_rss_collector:
                    self.rss_collector = self._shared_rss_collector
                else:
                    self.rss_collector = RSSCollector(
                        poll_interval=(cfg.rss_poll_interval_minutes * 60),
                    )
                self.news_analyzer = NewsLLMAnalyzer(
                    sentiment_ttl_hours=(cfg.rss_sentiment_ttl_hours),
                )
                logger.info("[Fundamental] RSSニュース機能初期化完了")

            logger.info("[Fundamental] ファンダメンタル機能初期化完了")
        except Exception as e:
            logger.error(
                "[Fundamental] 初期化失敗（無効化）: %s",
                e,
            )
            self.fundamental_memory = None
            self.fundamental_collector = None

    def init_calendar_only(self) -> None:
        """カレンダー＋RSSの軽量初期化（ファンダメンタル無効時）"""
        try:
            from autotrader.adapters.fundamental.collector import (
                FundamentalDataCollector,
            )

            if self._shared_fundamental_collector:
                self.fundamental_collector = self._shared_fundamental_collector
            else:
                self.fundamental_collector = FundamentalDataCollector(
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
            self.fundamental_collector = None

        # RSS軽量ポーリング（DB・LLM不要）
        try:
            from autotrader.adapters.fundamental.rss_collector import (
                RSSCollector,
            )

            if self._shared_rss_collector:
                self.rss_collector = self._shared_rss_collector
            else:
                self.rss_collector = RSSCollector(
                    poll_interval=300,
                )
            logger.info("[RSS] 軽量RSSポーリング初期化完了")
        except Exception as e:
            logger.warning("[RSS] RSS初期化スキップ: %s", e)
            self.rss_collector = None

    async def start_tasks(self) -> None:
        """ファンダメンタル収集タスクを起動"""
        if not self._owns_collectors:
            return
        if self.fundamental_collector:
            await self.fundamental_collector.start()
            logger.info("[Fundamental] 収集タスク起動")
        if self.rss_collector:
            await self.rss_collector.start(callback=self.on_rss_news)
            logger.info("[Fundamental] RSSポーリング起動")

    async def stop_tasks(self) -> None:
        """ファンダメンタル収集タスクを停止"""
        if self._owns_collectors:
            if self.fundamental_collector:
                await self.fundamental_collector.stop()
            if self.rss_collector:
                await self.rss_collector.stop()
        self.news_buffer.clear()

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
            for n in self.news_buffer
            if base in n.currencies or quote in n.currencies
        ]
        filtered.sort(
            key=lambda n: getattr(n, "published_at", datetime.min),
            reverse=True,
        )
        return filtered[:limit]

    async def on_rss_news(self, news_item) -> None:
        """RSSニュース受信コールバック

        Args:
            news_item: 受信したNewsItem
        """
        self.news_buffer.append(news_item)

        # active_symbol 関連のキーワードセンチメント分析
        # NOTE: 呼び出し元がsymbolを設定する必要あり
        symbol = getattr(self, "_active_symbol", "")
        if not symbol:
            return

        base = symbol[:3].upper()
        quote = symbol[3:6].upper()
        if base in news_item.currencies or quote in news_item.currencies:
            headlines = [
                n.title
                for n in self.news_buffer
                if base in n.currencies or quote in n.currencies
            ]
            if headlines:
                from autotrader.adapters.fundamental.sentiment_store import (
                    SentimentRecord,
                )

                result = self.keyword_scorer.score(
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
                    self.sentiment_store.save(
                        symbol,
                        record,
                    )

        # 3日超の古いニュースを削除
        _TTL_HOURS = 72
        now = datetime.now(UTC)
        self.news_buffer = [
            n
            for n in self.news_buffer
            if (now - getattr(n, "published_at", now)).total_seconds()
            < _TTL_HOURS * 3600
        ]
        # バッファ上限（メモリリーク防止）
        _MAX_BUFFER = 500
        if len(self.news_buffer) > _MAX_BUFFER:
            self.news_buffer = self.news_buffer[-_MAX_BUFFER:]
        # EventBus経由でダッシュボードに配信
        if base in news_item.currencies or quote in news_item.currencies:
            event_bus.publish_nowait(
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
    def blend_news_sentiment(
        ctx,
        sentiment: float,
        weight: float = 0.15,
    ):
        """ニュースセンチメントをFundamentalContextにブレンド

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

    async def run_morning_update(
        self,
        active_symbol: str,
        fundamental_config: FundamentalConfig,
    ) -> None:
        """毎朝のLLM市場観更新

        Args:
            active_symbol: アクティブシンボル
            fundamental_config: ファンダメンタル設定
        """
        if not self.fundamental_memory:
            return

        now = datetime.now(UTC)
        today = now.date()

        if (
            self.morning_update_done_date
            and self.morning_update_done_date == today
        ):
            return

        if now.hour != fundamental_config.morning_update_utc_hour:
            return

        try:
            from autotrader.adapters.ollama.client import (
                OllamaClient,
            )

            llm_client = OllamaClient()

            upcoming_events = self.fundamental_memory.get_upcoming_events(
                active_symbol,
                now,
                window_minutes=168,
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
                symbol=active_symbol,
                timestamp=now.isoformat(),
                current_price=0.0,
                upcoming_events=upcoming_dicts,
                valid_days=7,
            )

            self.fundamental_memory.write_macro_bias(
                symbol=active_symbol,
                direction_score=result.direction_score,
                confidence=result.confidence,
                summary=result.macro_summary,
                llm_reasoning=str(result.key_factors),
            )
            self.morning_update_done_date = today
            logger.info(
                "[Fundamental] 朝の市場観更新完了: score=%+.2f",
                result.direction_score,
            )

        except Exception as e:
            logger.warning("[Fundamental] 朝の市場観更新失敗: %s", e)

    async def handle_post_event_analysis(
        self,
        event_name: str,
        currency: str,
        actual: float | None,
        forecast: float | None,
        previous: float | None,
        current_price: float,
        active_symbol: str,
        price_change: float = 0.0,
    ) -> None:
        """指標後バイアス分析を実行しDBに保存

        Args:
            event_name: イベント名
            currency: 通貨コード
            actual: 実績値
            forecast: 予測値
            previous: 前回値
            current_price: 現在価格
            active_symbol: アクティブシンボル
            price_change: 価格変化率
        """
        if not self.fundamental_memory:
            return

        try:
            from autotrader.adapters.ollama.client import (
                OllamaClient,
            )

            llm_client = OllamaClient()
            now = datetime.now(UTC)

            result = await llm_client.analyze_post_event_async(
                symbol=active_symbol,
                timestamp=now.isoformat(),
                event_name=event_name,
                currency=currency,
                actual=actual,
                forecast=forecast,
                previous=previous,
                current_price=current_price,
                price_change=price_change,
            )

            self.fundamental_memory.write_post_event_bias(
                symbol=active_symbol,
                direction_score=result.bias_score,
                confidence=0.7,
                summary=result.analysis[:100],
                source_event=event_name,
                llm_reasoning=result.analysis,
            )
            logger.info(
                "[Fundamental] 指標後バイアス保存: %s score=%+.2f",
                event_name,
                result.bias_score,
            )

        except Exception as e:
            logger.warning("[Fundamental] 指標後分析失敗: %s", e)
