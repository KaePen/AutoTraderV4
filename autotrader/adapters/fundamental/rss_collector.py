"""RSS フィード収集モジュール（ライブトレード用）

RSSフィードを非同期ポーリングして新着ニュースを収集する。
ライブトレードエンジンから起動し、新着ニュースをコールバックで通知する。

依存: feedparser>=6.0.11
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from loguru import logger

from autotrader.adapters.fundamental.news_schemas import (
    CURRENCY_KEYWORDS,
    NewsItem,
    NewsSource,
)

try:
    import feedparser as _feedparser_module
except ImportError:
    _feedparser_module = None  # type: ignore[assignment]

# RSSフィードURL定義（通貨別）
RSS_FEEDS: dict[str, list[str]] = {
    "general": [
        "https://feeds.reuters.com/reuters/businessNews",
        "https://www.ft.com/rss/home/us",
        "https://feeds.feedburner.com/reuters/businessNews",
    ],
    "USD": [
        "https://feeds.reuters.com/reuters/USDollarRSS",
        "https://feeds.feedburner.com/forex/usd",
    ],
    "JPY": [
        "https://feeds.reuters.com/reuters/JPYenRSS",
    ],
    "EUR": [
        "https://feeds.reuters.com/reuters/EurEuroRSS",
    ],
    "GBP": [
        "https://feeds.reuters.com/reuters/GBPoundRSS",
    ],
}

# 重複排除バッファの上限（メモリリーク防止）
_MAX_SEEN_IDS = 10_000


class RSSCollector:
    """RSS フィードコレクター

    指定通貨に関連するRSSフィードを非同期ポーリングし、
    新着ニュースをコールバック経由で通知する。

    Args:
        currencies: 対象通貨コードリスト
        poll_interval: ポーリング間隔（秒）。デフォルト300秒
    """

    def __init__(
        self,
        currencies: list[str],
        poll_interval: int = 300,
        article_fetcher: object | None = None,
    ) -> None:
        """初期化

        Args:
            currencies: 対象通貨コードリスト
            poll_interval: ポーリング間隔（秒）
            article_fetcher: ArticleFetcher インスタンス（任意）。
                指定時は新着ニュースの本文を自動取得する。
        """
        self._currencies = [c.upper() for c in currencies]
        self._poll_interval = poll_interval
        self._article_fetcher = article_fetcher
        self._running = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        # OrderedDict で上限付き重複排除（メモリリーク防止）
        self._seen_ids: OrderedDict[str, None] = OrderedDict()
        self._feeds = self._build_feed_list()

    def _build_feed_list(self) -> list[str]:
        """対象通貨に関連するフィードURLリストを構築

        Returns:
            list[str]: フィードURLリスト
        """
        feeds: list[str] = list(RSS_FEEDS.get("general", []))
        for currency in self._currencies:
            currency_feeds = RSS_FEEDS.get(currency, [])
            feeds.extend(currency_feeds)
        # 重複排除
        return list(dict.fromkeys(feeds))

    async def start(
        self,
        callback: Callable[[NewsItem], Awaitable[None]],
    ) -> None:
        """非同期ポーリング開始

        Args:
            callback: 新着ニュースを受け取るコールバック関数
        """
        if _feedparser_module is None:
            logger.error(
                "[RSSCollector] feedparser が必要です: "
                "pip install feedparser"
            )
            return

        self._running = True
        self._task = asyncio.create_task(
            self._poll_loop(callback)
        )
        logger.info(
            f"[RSSCollector] 開始: {len(self._feeds)}フィード, "
            f"間隔{self._poll_interval}秒"
        )

    async def stop(self) -> None:
        """ポーリング停止"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[RSSCollector] 停止")

    async def _poll_loop(
        self,
        callback: Callable[[NewsItem], Awaitable[None]],
    ) -> None:
        """ポーリングループ

        Args:
            callback: 新着ニュースコールバック
        """
        while self._running:
            try:
                items = await self._fetch_all_feeds()
                await self._enrich_content(items)
                for item in items:
                    if item.news_id not in self._seen_ids:
                        self._seen_ids[item.news_id] = None
                        # 上限超過時は最古エントリを削除
                        if len(self._seen_ids) > _MAX_SEEN_IDS:
                            self._seen_ids.popitem(last=False)
                        try:
                            await callback(item)
                        except Exception as e:
                            logger.warning(
                                f"[RSSCollector] コールバック"
                                f"エラー: {e}"
                            )
            except Exception as e:
                logger.error(
                    f"[RSSCollector] ポーリングエラー: {e}"
                )

            await asyncio.sleep(self._poll_interval)

    async def _enrich_content(
        self, items: list[NewsItem]
    ) -> None:
        """新着アイテムの本文をベストエフォートで取得

        article_fetcher が設定されている場合のみ動作する。
        取得失敗してもアイテムは変更しない（title + snippet で配信）。

        Args:
            items: 本文取得対象のニュースアイテムリスト
        """
        if self._article_fetcher is None:
            return

        loop = asyncio.get_running_loop()
        for item in items:
            if item.content:
                continue
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        self._article_fetcher.fetch,  # type: ignore[union-attr]
                        item.source_url,
                        None,
                    ),
                    timeout=10.0,
                )
                if result.status == "ok" and result.content:
                    item.content = result.content
            except Exception as e:
                logger.debug(
                    f"[RSSCollector] 本文取得失敗 "
                    f"{item.source_url}: {e}"
                )

    async def _fetch_all_feeds(self) -> list[NewsItem]:
        """全フィードから記事を取得

        Returns:
            list[NewsItem]: 取得したニュースアイテムリスト
        """
        loop = asyncio.get_running_loop()
        all_items: list[NewsItem] = []

        for feed_url in self._feeds:
            try:
                items = await loop.run_in_executor(
                    None, self._fetch_feed, feed_url
                )
                all_items.extend(items)
            except Exception as e:
                logger.debug(
                    f"[RSSCollector] フィード取得失敗 "
                    f"{feed_url}: {e}"
                )

        logger.debug(
            f"[RSSCollector] {len(all_items)}件取得"
        )
        return all_items

    def _fetch_feed(self, feed_url: str) -> list[NewsItem]:
        """単一フィードから記事を同期取得

        Args:
            feed_url: RSSフィードURL

        Returns:
            list[NewsItem]: 取得したニュースアイテムリスト
        """
        if _feedparser_module is None:
            return []

        try:
            feed = _feedparser_module.parse(feed_url)
        except Exception as e:
            logger.warning(
                f"[RSSCollector] パースエラー {feed_url}: {e}"
            )
            return []

        if feed.bozo:
            logger.warning(
                f"[RSSCollector] 不正フォーマットフィード "
                f"{feed_url}: {feed.bozo_exception}"
            )

        items: list[NewsItem] = []
        feed_name = (
            getattr(feed.feed, "title", "")
            or feed_url.split("/")[2]
        )

        for entry in feed.entries:
            item = self._parse_entry(entry, feed_name)
            if item:
                items.append(item)

        return items

    def _parse_entry(
        self, entry: object, feed_name: str
    ) -> NewsItem | None:
        """フィードエントリをNewsItemに変換

        Args:
            entry: feedparserのエントリオブジェクト
            feed_name: フィード名称

        Returns:
            NewsItem | None: 変換済みアイテム
        """
        url = getattr(entry, "link", "") or ""
        if not url:
            return None

        title = (
            getattr(entry, "title", "") or ""
        ).strip()
        if not title:
            return None

        # 公開日時パース
        published_at = _parse_rss_date(entry)
        if published_at is None:
            published_at = datetime.now(UTC)

        # タイトルから通貨を抽出
        currencies = _extract_currencies(
            title, self._currencies
        )
        if not currencies:
            # summaryも確認
            summary = (
                getattr(entry, "summary", "") or ""
            ).lower()
            currencies = _extract_currencies(
                summary, self._currencies
            )
        if not currencies:
            currencies = list(self._currencies)

        # スニペット取得（summary冒頭200文字）
        summary_raw = getattr(entry, "summary", "") or ""
        snippet = summary_raw[:200] if summary_raw else None

        news_id = NewsItem.make_id(NewsSource.RSS, url)

        return NewsItem(
            news_id=news_id,
            published_at=published_at,
            title=title,
            source_name=feed_name,
            source_url=url,
            currencies=currencies,
            source_type=NewsSource.RSS,
            snippet=snippet,
        )


def _parse_rss_date(entry: object) -> datetime | None:
    """feedparserエントリから公開日時を取得

    Args:
        entry: feedparserのエントリオブジェクト

    Returns:
        datetime | None: UTC aware datetime
    """
    # published_parsed（time.struct_time）を試みる
    parsed = getattr(entry, "published_parsed", None)
    if parsed:
        try:
            return datetime(
                *parsed[:6], tzinfo=UTC
            )
        except (ValueError, TypeError):
            pass

    # published文字列を試みる
    published_str = getattr(entry, "published", "") or ""
    if published_str:
        try:
            dt = parsedate_to_datetime(published_str)
            return dt.astimezone(UTC)
        except Exception:
            pass

    return None


def _extract_currencies(
    text: str, target_currencies: list[str]
) -> list[str]:
    """テキストから対象通貨コードを抽出

    Args:
        text: 検索対象テキスト
        target_currencies: 対象通貨コードリスト

    Returns:
        list[str]: 検出された通貨コードリスト
    """
    found: list[str] = []
    text_lower = text.lower()

    for currency in target_currencies:
        cur_upper = currency.upper()
        keywords = CURRENCY_KEYWORDS.get(cur_upper, [])
        if cur_upper.lower() in text_lower or any(
            kw.lower() in text_lower for kw in keywords
        ):
            if cur_upper not in found:
                found.append(cur_upper)

    return found
