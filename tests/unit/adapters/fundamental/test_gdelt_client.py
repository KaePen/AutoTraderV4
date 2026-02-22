"""GDELTDocClient のユニットテスト"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from autotrader.adapters.fundamental.gdelt_client import (
    GDELTDocClient,
    _extract_currencies,
    _fmt_gdelt_dt,
    _parse_gdelt_date,
)
from autotrader.adapters.fundamental.news_schemas import (
    NewsItem,
    NewsSource,
)


class TestFmtGdeltDt:
    """_fmt_gdelt_dt のテスト"""

    def test_フォーマット変換(self) -> None:
        """UTC datetimeをGDELT形式に変換"""
        dt = datetime(2024, 1, 15, 8, 30, 0, tzinfo=timezone.utc)
        result = _fmt_gdelt_dt(dt)
        assert result == "20240115083000"


class TestParseGdeltDate:
    """_parse_gdelt_date のテスト"""

    def test_YYYYMMDDTHHMMSSZ形式(self) -> None:
        """GDELT標準形式をパース"""
        result = _parse_gdelt_date("20240115T083000Z")
        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 8
        assert result.tzinfo == timezone.utc

    def test_YYYYMMDDHHMMSS形式(self) -> None:
        """TZなし形式もパース可能"""
        result = _parse_gdelt_date("20240115083000")
        assert result is not None
        assert result.year == 2024

    def test_空文字列(self) -> None:
        """空文字列はNoneを返す"""
        assert _parse_gdelt_date("") is None

    def test_不正な日付(self) -> None:
        """不正な日付はNoneを返す"""
        assert _parse_gdelt_date("invalid") is None


class TestExtractCurrencies:
    """_extract_currencies のテスト"""

    def test_USD検出(self) -> None:
        """USD関連キーワードを検出"""
        text = "Federal Reserve raises interest rates"
        result = _extract_currencies(text, ["USD", "JPY"])
        assert "USD" in result

    def test_JPY検出(self) -> None:
        """JPY関連キーワードを検出"""
        text = "Bank of Japan holds policy steady"
        result = _extract_currencies(text, ["USD", "JPY"])
        assert "JPY" in result

    def test_対象外通貨は検出しない(self) -> None:
        """ターゲット外の通貨は検出しない"""
        text = "ECB raises rates for eurozone"
        result = _extract_currencies(text, ["USD", "JPY"])
        assert "EUR" not in result

    def test_通貨コード直接検出(self) -> None:
        """通貨コード直接記載も検出"""
        text = "USD strengthens against JPY"
        result = _extract_currencies(text, ["USD", "JPY"])
        assert "USD" in result
        assert "JPY" in result

    def test_空テキスト(self) -> None:
        """空テキストは空リストを返す"""
        result = _extract_currencies("", ["USD"])
        assert result == []


class TestGDELTDocClientParseArticle:
    """GDELTDocClient._parse_article のテスト"""

    def setup_method(self) -> None:
        """セットアップ"""
        self.client = GDELTDocClient()

    def test_有効な記事をNewsItemに変換(self) -> None:
        """有効な記事dictをNewsItemに変換する"""
        article = {
            "url": "https://reuters.com/article/123",
            "title": "Federal Reserve holds rates",
            "seendate": "20240115T143000Z",
            "domain": "reuters.com",
            "socialimage": None,
        }
        item = self.client._parse_article(
            article, ["USD", "JPY"]
        )
        assert item is not None
        assert item.source_type == NewsSource.GDELT
        assert item.title == "Federal Reserve holds rates"
        assert item.source_name == "reuters.com"
        assert item.published_at.year == 2024
        assert "USD" in item.currencies

    def test_URLなし記事はNone(self) -> None:
        """URLが空の記事はNoneを返す"""
        article = {
            "url": "",
            "title": "Some news",
            "seendate": "20240115T143000Z",
        }
        item = self.client._parse_article(
            article, ["USD"]
        )
        assert item is None

    def test_タイトルなし記事はNone(self) -> None:
        """タイトルが空の記事はNoneを返す"""
        article = {
            "url": "https://example.com/1",
            "title": "",
            "seendate": "20240115T143000Z",
        }
        item = self.client._parse_article(article, ["USD"])
        assert item is None

    def test_通貨未検出時はtarget通貨を付与(self) -> None:
        """タイトルに通貨が含まれない場合はターゲット通貨を付与"""
        article = {
            "url": "https://example.com/2",
            "title": "Market volatility increases",
            "seendate": "20240115T143000Z",
            "domain": "example.com",
        }
        item = self.client._parse_article(
            article, ["USD", "JPY"]
        )
        assert item is not None
        assert "USD" in item.currencies
        assert "JPY" in item.currencies


class TestGDELTDocClientFetchNewsWeek:
    """GDELTDocClient.fetch_news_week のテスト"""

    def setup_method(self) -> None:
        """セットアップ"""
        self.client = GDELTDocClient(
            request_interval_sec=0.0
        )

    @patch(
        "autotrader.adapters.fundamental.gdelt_client"
        "._requests_module"
    )
    def test_正常レスポンス(
        self, mock_requests: MagicMock
    ) -> None:
        """APIレスポンスからNewsItemリストを返す"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "articles": [
                {
                    "url": "https://reuters.com/1",
                    "title": "Fed raises rates by 25 bps",
                    "seendate": "20240105T143000Z",
                    "domain": "reuters.com",
                },
                {
                    "url": "https://bloomberg.com/1",
                    "title": "BOJ holds steady",
                    "seendate": "20240105T100000Z",
                    "domain": "bloomberg.com",
                },
            ]
        }
        mock_requests.get.return_value = mock_response

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 8, tzinfo=timezone.utc)
        items = self.client.fetch_news_week(
            ["USD", "JPY"], start, end
        )

        assert len(items) == 2
        assert isinstance(items[0], NewsItem)

    @patch(
        "autotrader.adapters.fundamental.gdelt_client"
        "._requests_module"
    )
    def test_API失敗時は空リスト(
        self, mock_requests: MagicMock
    ) -> None:
        """APIエラー時は空リストを返す"""
        mock_requests.get.side_effect = Exception(
            "Connection error"
        )

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 8, tzinfo=timezone.utc)
        items = self.client.fetch_news_week(
            ["USD"], start, end
        )

        assert items == []

    @patch(
        "autotrader.adapters.fundamental.gdelt_client"
        "._requests_module"
    )
    def test_articlesなしは空リスト(
        self, mock_requests: MagicMock
    ) -> None:
        """articles キーなしは空リスト"""
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_requests.get.return_value = mock_response

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 8, tzinfo=timezone.utc)
        items = self.client.fetch_news_week(
            ["USD"], start, end
        )
        assert items == []

    def test_requests未インストール時は空リスト(self) -> None:
        """_requests_module が None の場合は空リストを返す"""
        import autotrader.adapters.fundamental.gdelt_client as m
        original = m._requests_module
        m._requests_module = None  # type: ignore[assignment]
        try:
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 8, tzinfo=timezone.utc)
            items = self.client.fetch_news_week(
                ["USD"], start, end
            )
            assert items == []
        finally:
            m._requests_module = original


class TestGDELTDocClientBuildQuery:
    """GDELTDocClient._build_query のテスト"""

    def setup_method(self) -> None:
        """セットアップ"""
        self.client = GDELTDocClient()

    def test_USDクエリ構築(self) -> None:
        """USD単独のクエリを構築"""
        query = self.client._build_query(["USD"])
        assert "Federal Reserve" in query or "Fed rate" in query

    def test_複数通貨クエリ(self) -> None:
        """複数通貨のOR条件クエリを構築"""
        query = self.client._build_query(["USD", "JPY"])
        assert " OR " in query

    def test_未知通貨はフォールバック(self) -> None:
        """未知通貨コードはデフォルトキーワードを使用"""
        query = self.client._build_query(["XYZ"])
        assert "forex" in query or "currency" in query
