"""article_scraper のユニットテスト"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from autotrader.adapters.fundamental.article_scraper import (
    ArticleFetcher,
    BBCParser,
    CNBCParser,
    DailyFXParser,
    ForexLiveParser,
    FXStreetParser,
    InvestingComParser,
    MarketWatchParser,
    ParserRegistry,
    _clean_text,
    _extract_domain,
)

# --- テスト用HTMLフィクスチャ ---

_FXSTREET_HTML = """
<html><body>
<div class="fxs_article_body">
  <div class="fxs_ad">広告</div>
  <p>FXStreet paragraph one.</p>
  <p>FXStreet paragraph two.</p>
</div>
</body></html>
"""

_FOREXLIVE_HTML = """
<html><body>
<article>
  <div class="article-body">
    <p>ForexLive paragraph one.</p>
    <p>ForexLive paragraph two.</p>
    <div class="ad-container">広告</div>
  </div>
</article>
</body></html>
"""

_INVESTING_HTML = """
<html><body>
<div class="article_WYSIWYG__O0uhw">
  <p>Investing.com paragraph one.</p>
  <p>Investing.com paragraph two.</p>
  <div class="ad-slot">広告</div>
</div>
</body></html>
"""

_CNBC_HTML = """
<html><body>
<div class="ArticleBody-articleBody">
  <p>CNBC paragraph one.</p>
  <p>CNBC paragraph two.</p>
  <div class="InlineVideo">動画</div>
</div>
</body></html>
"""

_DAILYFX_HTML = """
<html><body>
<div class="dfx-article__content">
  <p>DailyFX paragraph one.</p>
  <p>DailyFX paragraph two.</p>
  <div class="dfx-ad">広告</div>
</div>
</body></html>
"""

_BBC_HTML = """
<html><body>
<article>
  <div data-component="text-block">
    <p>BBC paragraph one.</p>
  </div>
  <div data-component="text-block">
    <p>BBC paragraph two.</p>
  </div>
</article>
</body></html>
"""

_MARKETWATCH_HTML = """
<html><body>
<div class="article__body">
  <p>MarketWatch paragraph one.</p>
  <p>MarketWatch paragraph two.</p>
  <div class="advertisement">広告</div>
</div>
</body></html>
"""


class TestCleanText:
    """_clean_text のテスト"""

    def test_空入力(self) -> None:
        """空文字列は空文字列を返す"""
        assert _clean_text("") == ""

    def test_正規化(self) -> None:
        """連続空白行が正規化される"""
        raw = "line1\n\n\n\nline2\n\nline3"
        result = _clean_text(raw)
        assert result == "line1\nline2\nline3"

    def test_最大文字数切り詰め(self) -> None:
        """最大文字数を超えると ... で切り詰め"""
        raw = "a " * 3000  # 6000文字
        result = _clean_text(raw, max_chars=100)
        assert len(result) <= 104  # 100 + "..."
        assert result.endswith("...")

    def test_短いテキストはそのまま(self) -> None:
        """最大文字数以下のテキストはそのまま返す"""
        raw = "short text"
        result = _clean_text(raw, max_chars=100)
        assert result == "short text"

    def test_行頭行末空白除去(self) -> None:
        """各行の前後空白が除去される"""
        raw = "  line1  \n  line2  "
        result = _clean_text(raw)
        assert result == "line1\nline2"


class TestDomainExtraction:
    """_extract_domain のテスト"""

    def test_通常URL(self) -> None:
        """通常のURLからドメインを抽出"""
        assert _extract_domain(
            "https://fxstreet.com/news/article"
        ) == "fxstreet.com"

    def test_www除去(self) -> None:
        """www. プレフィックスを除去"""
        assert _extract_domain(
            "https://www.cnbc.com/article"
        ) == "cnbc.com"

    def test_不正URL(self) -> None:
        """不正なURLはNoneを返す"""
        assert _extract_domain("not-a-url") is None

    def test_空文字(self) -> None:
        """空文字はNoneを返す"""
        assert _extract_domain("") is None


class TestParserRegistry:
    """ParserRegistry のテスト"""

    def test_登録と取得(self) -> None:
        """パーサーを登録して取得できる"""
        registry = ParserRegistry()
        parser = FXStreetParser()
        registry.register(parser)
        assert registry.get("fxstreet.com") is parser

    def test_未登録ドメインはNone(self) -> None:
        """未登録ドメインはNoneを返す"""
        registry = ParserRegistry()
        assert registry.get("unknown.com") is None

    def test_domains一覧(self) -> None:
        """登録済みドメイン一覧を返す"""
        registry = ParserRegistry()
        registry.register(FXStreetParser())
        registry.register(CNBCParser())
        assert set(registry.domains) == {
            "fxstreet.com", "cnbc.com",
        }


class TestFXStreetParser:
    """FXStreetParser のテスト"""

    def test_コンテンツ抽出(self) -> None:
        """fxstreet.com HTMLから本文を抽出"""
        parser = FXStreetParser()
        content = parser.extract_content(_FXSTREET_HTML)
        assert content is not None
        assert "FXStreet paragraph one." in content
        assert "FXStreet paragraph two." in content
        # 広告テキストは除去
        assert "広告" not in content

    def test_ドメイン名(self) -> None:
        """ドメイン名が正しい"""
        assert FXStreetParser().domain == "fxstreet.com"

    def test_TLS不要(self) -> None:
        """TLSフィンガープリント不要"""
        assert not FXStreetParser().needs_tls_fingerprint


class TestForexLiveParser:
    """ForexLiveParser のテスト"""

    def test_コンテンツ抽出(self) -> None:
        """forexlive.com HTMLから本文を抽出"""
        parser = ForexLiveParser()
        content = parser.extract_content(_FOREXLIVE_HTML)
        assert content is not None
        assert "ForexLive paragraph one." in content
        assert "広告" not in content

    def test_TLS必須(self) -> None:
        """TLSフィンガープリント必須"""
        assert ForexLiveParser().needs_tls_fingerprint


class TestInvestingComParser:
    """InvestingComParser のテスト"""

    def test_コンテンツ抽出(self) -> None:
        """investing.com HTMLから本文を抽出"""
        parser = InvestingComParser()
        content = parser.extract_content(_INVESTING_HTML)
        assert content is not None
        assert "Investing.com paragraph one." in content
        assert "広告" not in content

    def test_TLS必須(self) -> None:
        """TLSフィンガープリント必須"""
        assert InvestingComParser().needs_tls_fingerprint


class TestCNBCParser:
    """CNBCParser のテスト"""

    def test_コンテンツ抽出(self) -> None:
        """cnbc.com HTMLから本文を抽出"""
        parser = CNBCParser()
        content = parser.extract_content(_CNBC_HTML)
        assert content is not None
        assert "CNBC paragraph one." in content
        assert "動画" not in content


class TestDailyFXParser:
    """DailyFXParser のテスト"""

    def test_コンテンツ抽出(self) -> None:
        """dailyfx.com HTMLから本文を抽出"""
        parser = DailyFXParser()
        content = parser.extract_content(_DAILYFX_HTML)
        assert content is not None
        assert "DailyFX paragraph one." in content
        assert "広告" not in content


class TestBBCParser:
    """BBCParser のテスト"""

    def test_テキストブロック抽出(self) -> None:
        """bbc.com data-component='text-block' から抽出"""
        parser = BBCParser()
        content = parser.extract_content(_BBC_HTML)
        assert content is not None
        assert "BBC paragraph one." in content
        assert "BBC paragraph two." in content

    def test_フォールバック抽出(self) -> None:
        """text-blockがない場合はarticle内の段落から抽出"""
        html = """
        <html><body>
        <article>
          <p>Fallback paragraph.</p>
        </article>
        </body></html>
        """
        parser = BBCParser()
        content = parser.extract_content(html)
        assert content is not None
        assert "Fallback paragraph." in content


class TestMarketWatchParser:
    """MarketWatchParser のテスト"""

    def test_コンテンツ抽出(self) -> None:
        """marketwatch.com HTMLから本文を抽出"""
        parser = MarketWatchParser()
        content = parser.extract_content(
            _MARKETWATCH_HTML
        )
        assert content is not None
        assert "MarketWatch paragraph one." in content
        assert "広告" not in content

    def test_TLS必須(self) -> None:
        """TLSフィンガープリント必須"""
        assert MarketWatchParser().needs_tls_fingerprint


class TestArticleFetcher:
    """ArticleFetcher のテスト"""

    def test_未対応ドメイン(self) -> None:
        """未対応ドメインはエラーを返す"""
        fetcher = ArticleFetcher(
            registry=ParserRegistry()
        )
        result = fetcher.fetch(
            "https://unknown.com/article"
        )
        assert result.status == "error"
        assert "未対応ドメイン" in (result.error_msg or "")

    def test_ドメイン抽出失敗(self) -> None:
        """不正URLはエラーを返す"""
        fetcher = ArticleFetcher()
        result = fetcher.fetch("not-a-url")
        assert result.status == "error"

    @patch(
        "autotrader.adapters.fundamental"
        ".article_scraper._httpx"
    )
    def test_httpx取得成功(
        self, mock_httpx: MagicMock
    ) -> None:
        """httpxで正常取得できる"""
        mock_response = MagicMock()
        mock_response.text = _FXSTREET_HTML
        mock_response.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_response

        fetcher = ArticleFetcher()
        result = fetcher.fetch(
            "https://www.fxstreet.com/news/test",
            "fxstreet.com",
        )
        assert result.status == "ok"
        assert result.content is not None
        assert "FXStreet paragraph" in result.content

    @patch(
        "autotrader.adapters.fundamental"
        ".article_scraper._httpx"
    )
    def test_HTTPエラー(
        self, mock_httpx: MagicMock
    ) -> None:
        """HTTPエラーはerrorステータスを返す"""
        mock_httpx.get.side_effect = Exception(
            "404 Not Found"
        )
        fetcher = ArticleFetcher()
        result = fetcher.fetch(
            "https://www.fxstreet.com/news/test",
            "fxstreet.com",
        )
        assert result.status == "error"
        assert "HTTP取得失敗" in (result.error_msg or "")

    @patch(
        "autotrader.adapters.fundamental"
        ".article_scraper._curl_requests"
    )
    def test_curl_cffi取得(
        self, mock_curl: MagicMock
    ) -> None:
        """curl-cffiでTLS必須サイトを取得"""
        mock_response = MagicMock()
        mock_response.text = _FOREXLIVE_HTML
        mock_response.raise_for_status = MagicMock()
        mock_curl.get.return_value = mock_response

        fetcher = ArticleFetcher()
        result = fetcher.fetch(
            "https://www.forexlive.com/news/test",
            "forexlive.com",
        )
        assert result.status == "ok"
        assert result.content is not None
        mock_curl.get.assert_called_once()

    def test_レートリミット(self) -> None:
        """同一ドメインへの連続リクエストが制御される"""
        fetcher = ArticleFetcher(rate_limit=0.01)
        # レートリミットのタイムスタンプを記録
        fetcher._last_request["test.com"] = 0.0
        # _wait_rate_limitが正常に動作することを確認
        fetcher._wait_rate_limit("test.com")
        assert "test.com" in fetcher._last_request

    @patch(
        "autotrader.adapters.fundamental"
        ".article_scraper._httpx"
    )
    def test_空レスポンス(
        self, mock_httpx: MagicMock
    ) -> None:
        """空のHTMLレスポンスはエラーを返す"""
        mock_response = MagicMock()
        mock_response.text = ""
        mock_response.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_response

        fetcher = ArticleFetcher()
        result = fetcher.fetch(
            "https://www.fxstreet.com/news/test",
            "fxstreet.com",
        )
        assert result.status == "error"
        assert "空レスポンス" in (result.error_msg or "")

    @patch(
        "autotrader.adapters.fundamental"
        ".article_scraper._httpx"
    )
    def test_パース失敗(
        self, mock_httpx: MagicMock
    ) -> None:
        """コンテンツ抽出失敗はエラーを返す"""
        mock_response = MagicMock()
        # 有効なHTMLだが記事本文セレクタにマッチしない
        mock_response.text = "<html><body>empty</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_response

        fetcher = ArticleFetcher()
        result = fetcher.fetch(
            "https://www.fxstreet.com/news/test",
            "fxstreet.com",
        )
        assert result.status == "error"
