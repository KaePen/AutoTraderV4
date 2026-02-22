"""RSSCollector のユニットテスト"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autotrader.adapters.fundamental.news_schemas import (
    NewsItem,
    NewsSource,
)
from autotrader.adapters.fundamental.rss_collector import (
    RSSCollector,
    _extract_currencies,
    _parse_rss_date,
)


class TestExtractCurrencies:
    """_extract_currencies のテスト"""

    def test_USD検出(self) -> None:
        """USD関連キーワードを検出"""
        text = "Federal Reserve raises interest rates today"
        result = _extract_currencies(text, ["USD", "JPY"])
        assert "USD" in result

    def test_JPY検出(self) -> None:
        """JPY関連キーワードを検出"""
        text = "Bank of Japan holds steady on policy"
        result = _extract_currencies(text, ["USD", "JPY"])
        assert "JPY" in result

    def test_大文字小文字を区別しない(self) -> None:
        """大文字小文字を区別せずに検出"""
        text = "FED RAISES RATES"
        result = _extract_currencies(text, ["USD"])
        assert "USD" in result

    def test_複数通貨同時検出(self) -> None:
        """複数通貨を同時に検出"""
        text = "Fed and BOJ diverge on policy paths"
        result = _extract_currencies(text, ["USD", "JPY"])
        assert "USD" in result
        assert "JPY" in result

    def test_空テキストは空リスト(self) -> None:
        """空テキストは空リストを返す"""
        result = _extract_currencies("", ["USD", "JPY"])
        assert result == []

    def test_対象通貨コード直接検出(self) -> None:
        """通貨コードが直接含まれる場合に検出"""
        text = "USD/JPY pair shows volatility"
        result = _extract_currencies(text, ["USD", "JPY"])
        assert "USD" in result
        assert "JPY" in result


class TestParseRssDate:
    """_parse_rss_date のテスト"""

    def test_published_parsed利用(self) -> None:
        """published_parsed から日時を取得"""
        entry = MagicMock()
        entry.published_parsed = (
            2024, 1, 15, 8, 30, 0, 0, 0, 0
        )
        result = _parse_rss_date(entry)
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_published文字列利用(self) -> None:
        """published_parsed がない場合に文字列をパース"""
        entry = MagicMock()
        entry.published_parsed = None
        entry.published = (
            "Mon, 15 Jan 2024 08:30:00 +0000"
        )
        result = _parse_rss_date(entry)
        assert result is not None
        assert result.year == 2024

    def test_日付なしはNone(self) -> None:
        """日付情報がない場合はNone"""
        entry = MagicMock()
        entry.published_parsed = None
        entry.published = ""
        result = _parse_rss_date(entry)
        assert result is None


class TestRSSCollectorParseEntry:
    """RSSCollector._parse_entry のテスト"""

    def setup_method(self) -> None:
        """セットアップ"""
        self.collector = RSSCollector(
            currencies=["USD", "JPY"]
        )

    def test_有効なエントリをNewsItemに変換(self) -> None:
        """有効なエントリをNewsItemに変換する"""
        entry = MagicMock()
        entry.link = "https://reuters.com/article/1"
        entry.title = "Federal Reserve holds rates steady"
        entry.published_parsed = (
            2024, 1, 15, 8, 30, 0, 0, 0, 0
        )
        entry.summary = "The Fed decided to hold..."

        item = self.collector._parse_entry(entry, "reuters")

        assert item is not None
        assert item.source_type == NewsSource.RSS
        assert item.title == "Federal Reserve holds rates steady"
        assert item.source_name == "reuters"
        assert "USD" in item.currencies

    def test_リンクなしエントリはNone(self) -> None:
        """linkが空のエントリはNoneを返す"""
        entry = MagicMock()
        entry.link = ""
        entry.title = "Some news"

        item = self.collector._parse_entry(entry, "reuters")
        assert item is None

    def test_タイトルなしエントリはNone(self) -> None:
        """titleが空のエントリはNoneを返す"""
        entry = MagicMock()
        entry.link = "https://reuters.com/1"
        entry.title = ""

        item = self.collector._parse_entry(entry, "reuters")
        assert item is None

    def test_通貨未検出時は全通貨を付与(self) -> None:
        """タイトルから通貨が検出できない場合は全対象通貨を付与"""
        entry = MagicMock()
        entry.link = "https://reuters.com/1"
        entry.title = "Global markets show mixed signals"
        entry.published_parsed = (
            2024, 1, 15, 8, 30, 0, 0, 0, 0
        )
        entry.summary = ""

        item = self.collector._parse_entry(entry, "reuters")

        assert item is not None
        assert "USD" in item.currencies
        assert "JPY" in item.currencies

    def test_スニペット冒頭200文字(self) -> None:
        """スニペットはsummaryの冒頭200文字"""
        entry = MagicMock()
        entry.link = "https://reuters.com/1"
        entry.title = "Federal Reserve news"
        entry.published_parsed = (
            2024, 1, 15, 8, 30, 0, 0, 0, 0
        )
        entry.summary = "X" * 300  # 300文字

        item = self.collector._parse_entry(entry, "reuters")

        assert item is not None
        assert item.snippet is not None
        assert len(item.snippet) == 200


class TestRSSCollectorFeedList:
    """RSSCollector フィードリスト構築のテスト"""

    def test_通貨別フィード統合(self) -> None:
        """対象通貨のフィードがgeneralと合わせて含まれる"""
        collector = RSSCollector(currencies=["USD", "JPY"])
        feeds = collector._build_feed_list()
        # generalフィードが含まれる
        assert len(feeds) > 0

    def test_重複排除(self) -> None:
        """同じURLが重複しない"""
        collector = RSSCollector(
            currencies=["USD", "USD"]
        )
        feeds = collector._build_feed_list()
        assert len(feeds) == len(set(feeds))


class TestRSSCollectorAsync:
    """RSSCollectorの非同期テスト"""

    @pytest.mark.asyncio
    async def test_feedparser未インストール時は起動しない(
        self,
    ) -> None:
        """feedparser が None の場合は start が即終了する"""
        import autotrader.adapters.fundamental.rss_collector as m
        original = m._feedparser_module
        m._feedparser_module = None  # type: ignore[assignment]
        try:
            collector = RSSCollector(currencies=["USD"])
            callback_called = False

            async def callback(item: NewsItem) -> None:
                nonlocal callback_called
                callback_called = True

            await collector.start(callback)
            assert not collector._running
        finally:
            m._feedparser_module = original

    @pytest.mark.asyncio
    async def test_stop後は停止する(self) -> None:
        """stop()でポーリングループが停止する"""
        import autotrader.adapters.fundamental.rss_collector as m
        mock_feedparser = MagicMock()
        mock_feedparser.parse.return_value = MagicMock(
            entries=[], feed=MagicMock(title="Test")
        )
        original = m._feedparser_module
        m._feedparser_module = mock_feedparser  # type: ignore[assignment]
        try:
            collector = RSSCollector(
                currencies=["USD"],
                poll_interval=3600,  # 1時間（テスト中は発火しない）
            )
            received: list[NewsItem] = []

            async def callback(item: NewsItem) -> None:
                received.append(item)

            await collector.start(callback)
            assert collector._running
            await collector.stop()
            assert not collector._running
        finally:
            m._feedparser_module = original

    @pytest.mark.asyncio
    async def test_重複ニュースを除外(self) -> None:
        """同じnews_idのニュースは2回コールバックしない"""
        import autotrader.adapters.fundamental.rss_collector as m

        # モックエントリを準備
        mock_entry = MagicMock()
        mock_entry.link = "https://reuters.com/1"
        mock_entry.title = "Fed holds rates"
        mock_entry.published_parsed = (
            2024, 1, 15, 8, 30, 0, 0, 0, 0
        )
        mock_entry.summary = "..."

        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_feed.feed = MagicMock(title="Reuters")

        mock_feedparser = MagicMock()
        mock_feedparser.parse.return_value = mock_feed

        original = m._feedparser_module
        m._feedparser_module = mock_feedparser  # type: ignore[assignment]
        try:
            collector = RSSCollector(currencies=["USD"])
            received: list[NewsItem] = []

            async def callback(item: NewsItem) -> None:
                received.append(item)

            # 同じフィードを2回フェッチ（重複チェック）
            items1 = await collector._fetch_all_feeds()
            for item in items1:
                if item.news_id not in collector._seen_ids:
                    collector._seen_ids.add(item.news_id)
                    await callback(item)

            items2 = await collector._fetch_all_feeds()
            for item in items2:
                if item.news_id not in collector._seen_ids:
                    collector._seen_ids.add(item.news_id)
                    await callback(item)

            # 2回目は同じIDなのでコールバックされない
            assert len(received) == 1
        finally:
            m._feedparser_module = original
