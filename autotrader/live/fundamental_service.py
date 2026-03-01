"""ファンダメンタル関連サービス

ファンダメンタルデータ収集・RSSニュース・センチメント分析の
初期化・起動・停止・コンテキスト取得を担当する。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from autotrader.core.event_bus import event_bus
from autotrader.live.config import FundamentalConfig

logger = logging.getLogger(__name__)


class FundamentalService:
    """ファンダメンタル関連サービス

    Attributes:
        _config: ファンダメンタル設定
        _symbol: 通貨ペアシンボル
        _data_provider: MT5データプロバイダ
        _fundamental_memory: ファンダメンタルメモリサービス
        _fundamental_collector: ファンダメンタルデータコレクター
        _morning_update_done_date: 朝の更新完了日
        _rss_collector: RSSコレクター
        _news_analyzer: ニュースLLM分析器
        _news_buffer: ニュースバッファ
        _keyword_scorer: キーワードセンチメントスコアラー
        _sentiment_store: センチメント永続化ストア
        _owns_collectors: コレクター所有フラグ
    """

    def __init__(
        self,
        config: FundamentalConfig,
        symbol: str,
        data_provider: object,
        shared_fundamental_collector: object | None = None,
        shared_rss_collector: object | None = None,
    ) -> None:
        """初期化

        Args:
            config: ファンダメンタル設定
            symbol: 通貨ペアシンボル
            data_provider: MT5データプロバイダ
            shared_fundamental_collector: 共有ファンダメンタル
                コレクター（EngineManager経由）
            shared_rss_collector: 共有RSSコレクター
                （EngineManager経由）
        """
        self._config = config
        self._symbol = symbol
        self._data_provider = data_provider

        # ファンダメンタル関連属性
        self._fundamental_memory = None
        self._fundamental_collector = None
        self._morning_update_done_date: datetime | None = None
        self._rss_collector = None
        self._news_analyzer = None
        self._news_buffer: list = []

        # 共有コレクター
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

        self._keyword_scorer = KeywordSentimentScorer()
        self._sentiment_store = SentimentStore()

        # 初期化
        if config.enabled:
            self.init_fundamental(config)
        else:
            self.init_calendar_only()

    @property
    def fundamental_collector(self) -> object | None:
        """ファンダメンタルデータコレクター"""
        return self._fundamental_collector

    @property
    def rss_collector(self) -> object | None:
        """RSSコレクター"""
        return self._rss_collector

    @property
    def news_buffer(self) -> list:
        """ニュースバッファ"""
        return self._news_buffer

    @news_buffer.setter
    def news_buffer(self, value: list) -> None:
        """ニュースバッファ設定"""
        self._news_buffer = value

    @property
    def fundamental_memory(self) -> object | None:
        """ファンダメンタルメモリサービス"""
        return self._fundamental_memory

    @property
    def sentiment_store(self) -> object:
        """センチメント永続化ストア"""
        return self._sentiment_store

    @property
    def keyword_scorer(self) -> object:
        """キーワードセンチメントスコアラー"""
        return self._keyword_scorer

    @property
    def news_analyzer(self) -> object | None:
        """ニュースLLM分析器"""
        return self._news_analyzer

    @property
    def owns_collectors(self) -> bool:
        """コレクター所有フラグ"""
        return self._owns_collectors

    @property
    def symbol(self) -> str:
        """通貨ペアシンボル"""
        return self._symbol

    @symbol.setter
    def symbol(self, value: str) -> None:
        """通貨ペアシンボル設定"""
        self._symbol = value

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

    def init_calendar_only(self) -> None:
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
                    use_ff_holidays=True,
                )
            logger.info(
                "[Calendar] 軽量カレンダー初期化完了（MT5 CSV + FF休日）"
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

    async def start_tasks(self) -> None:
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
            await self._rss_collector.start(callback=self.on_rss_news)
            logger.info("[Fundamental] RSSポーリング起動")

    async def stop_tasks(self) -> None:
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

    async def on_rss_news(self, news_item) -> None:
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
        symbol = self._symbol
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
        ttl_hours = 72
        now = datetime.now(UTC)
        self._news_buffer = [
            n
            for n in self._news_buffer
            if (now - getattr(n, "published_at", now)).total_seconds()
            < ttl_hours * 3600
        ]
        # バッファ上限（メモリリーク防止）
        max_buffer = 500
        if len(self._news_buffer) > max_buffer:
            self._news_buffer = self._news_buffer[-max_buffer:]
        # EventBus経由でダッシュボードにリアルタイム配信
        # （active_symbol 関連のみ配信）
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

    def get_fundamental_context(
        self,
        symbol: str,
    ) -> object | None:
        """ファンダメンタルコンテキストを取得

        _tick()内のファンダメンタルコンテキスト取得ロジックを
        サービスメソッドとして抽出。

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            object | None: FundamentalContext
                またはNone（ファンダメンタル無効時）
        """
        now_utc = datetime.now(UTC)
        if self._fundamental_memory:
            fundamental_ctx = self._fundamental_memory.get_context_for_llm(
                symbol, now_utc
            )
            if fundamental_ctx.has_high_impact_within_30min:
                logger.info("[Fundamental] 重要指標直前のためスキップ")
                return "SKIP"  # type: ignore[return-value]
            return fundamental_ctx

        # ファンダメンタルメモリなし: SentimentStoreからフォールバック
        persisted = self._sentiment_store.load_latest(
            symbol,
        )
        if persisted and persisted.score != 0.0:
            from autotrader.adapters.fundamental.schemas import (
                FundamentalContext,
            )

            return FundamentalContext(
                sentiment_score=persisted.score,
                direction_bias=(persisted.score * 0.15),
            )
        return None

    async def process_news_sentiment(
        self,
        symbol: str,
        fundamental_ctx,
    ):
        """ニュースセンチメントをブレンドしてコンテキストを更新

        Args:
            symbol: 通貨ペアシンボル
            fundamental_ctx: 現在のFundamentalContext

        Returns:
            FundamentalContext: 更新済みコンテキスト
        """
        if fundamental_ctx is None or self._news_analyzer is None:
            return fundamental_ctx

        news_items = self.get_news_for_symbol(symbol)
        if news_items:
            sentiment = await self._news_analyzer.analyze(news_items, symbol)
            fundamental_ctx = self.blend_news_sentiment(
                fundamental_ctx, sentiment
            )
            # ファイル永続化
            from autotrader.adapters.fundamental.sentiment_store import (
                SentimentRecord,
            )

            self._sentiment_store.save(
                symbol,
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
            base = symbol[:3].upper()
            quote = symbol[3:6].upper()
            self._news_buffer = [
                n
                for n in self._news_buffer
                if base not in n.currencies and quote not in n.currencies
            ]
        else:
            # バッファ空でもキャッシュから取得
            sentiment = self._news_analyzer.get_current_sentiment(symbol)
            if sentiment != 0.0:
                fundamental_ctx = self.blend_news_sentiment(
                    fundamental_ctx, sentiment
                )

        return fundamental_ctx

    async def run_morning_update(
        self,
        symbol: str,
    ) -> None:
        """毎朝のLLM市場観更新

        UTC21時（日本時間6時）に実行。当日実行済みならスキップ。
        LLMが利用できない場合は警告ログのみ。

        Args:
            symbol: 通貨ペアシンボル
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
        if now.hour != self._config.morning_update_utc_hour:
            return

        try:
            from autotrader.adapters.ollama.client import (
                OllamaClient,
            )

            llm_client = OllamaClient()

            # 現在価格取得
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
                current_price=0.0,
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
                "[Fundamental] 朝の市場観更新完了: score=%+.2f",
                result.direction_score,
            )

        except Exception as e:
            logger.warning("[Fundamental] 朝の市場観更新失敗: %s", e)

    async def handle_post_event_analysis(
        self,
        symbol: str,
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
            symbol: 通貨ペアシンボル
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
            from autotrader.adapters.ollama.client import (
                OllamaClient,
            )

            llm_client = OllamaClient()
            now = datetime.now(UTC)

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
                "[Fundamental] 指標後バイアス保存: %s score=%+.2f",
                event_name,
                result.bias_score,
            )

        except Exception as e:
            logger.warning("[Fundamental] 指標後分析失敗: %s", e)
