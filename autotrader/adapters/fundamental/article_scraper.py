"""記事本文スクレイピングモジュール

FX専門ニュースサイトからHTML記事本文を取得し、
プレーンテキストに変換するドメイン別パーサーフレームワーク。

対応サイト:
  - fxstreet.com
  - forexlive.com（curl-cffi: Cloudflare対策）
  - investing.com（curl-cffi: ボット検知対策）
  - cnbc.com
  - dailyfx.com
  - bbc.com
  - marketwatch.com（curl-cffi）

依存: beautifulsoup4, lxml, httpx
オプション依存: curl-cffi（TLSフィンガープリント必須サイト用）
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urlparse

from loguru import logger

try:
    from bs4 import BeautifulSoup as _BeautifulSoup
except ImportError:
    _BeautifulSoup = None  # type: ignore[assignment,misc]

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

try:
    from curl_cffi import requests as _curl_requests
except ImportError:
    _curl_requests = None  # type: ignore[assignment]

# Wayback Machine API
_WAYBACK_API = (
    "https://archive.org/wayback/available?url={url}"
)
# DataDome等でブロックされるドメイン（Waybackフォールバック対象）
_WAYBACK_FALLBACK_DOMAINS = frozenset(
    {"marketwatch.com"}
)

# デフォルト設定
_DEFAULT_TIMEOUT = 15.0
_DEFAULT_RATE_LIMIT = 2.0
_MAX_CONTENT_CHARS = 5000
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class ScrapeResult:
    """スクレイピング結果

    Args:
        content: 抽出テキスト（成功時）
        status: 結果ステータス（ok, error, timeout, blocked）
        error_msg: エラーメッセージ（失敗時）
    """

    content: str | None = None
    status: str = "ok"
    error_msg: str | None = None


class ArticleParser(ABC):
    """記事パーサー抽象基底クラス

    各ドメイン固有のHTML構造に対応するパーサーを定義する。
    """

    @property
    @abstractmethod
    def domain(self) -> str:
        """対象ドメイン名"""

    @property
    def needs_tls_fingerprint(self) -> bool:
        """curl-cffiによるTLSフィンガープリントが必要か"""
        return False

    @abstractmethod
    def extract_content(self, html: str) -> str | None:
        """HTMLから記事本文を抽出

        Args:
            html: ページHTML文字列

        Returns:
            str | None: プレーンテキスト本文（抽出失敗時None）
        """


class FXStreetParser(ArticleParser):
    """fxstreet.com パーサー"""

    @property
    def domain(self) -> str:
        return "fxstreet.com"

    def extract_content(self, html: str) -> str | None:
        """fxstreet.com の記事本文を抽出"""
        if _BeautifulSoup is None:
            return None
        soup = _BeautifulSoup(html, "lxml")
        # 広告・不要要素を除去
        for tag in soup.select(
            ".fxs_ad, script, style, aside, nav, footer, "
            ".fxs_related, .fxs_sharing"
        ):
            tag.decompose()
        # 本文セレクタ（フォールバック付き）
        body = (
            soup.select_one("div.fxs_article_body")
            or soup.select_one("article .fxs_entry_content")
            or soup.select_one("article")
        )
        if not body:
            return None
        return _extract_paragraphs(body)


class ForexLiveParser(ArticleParser):
    """forexlive.com パーサー（Cloudflare保護）"""

    @property
    def domain(self) -> str:
        return "forexlive.com"

    @property
    def needs_tls_fingerprint(self) -> bool:
        return True

    def extract_content(self, html: str) -> str | None:
        """forexlive.com の記事本文を抽出"""
        if _BeautifulSoup is None:
            return None
        soup = _BeautifulSoup(html, "lxml")
        for tag in soup.select(
            "script, style, .ad-container, aside, nav, "
            "footer, .sidebar"
        ):
            tag.decompose()
        body = (
            soup.select_one("article .article-body")
            or soup.select_one("article .post-content")
            or soup.select_one(".article-content")
            or soup.select_one("article")
        )
        if not body:
            return None
        return _extract_paragraphs(body)


class InvestingComParser(ArticleParser):
    """investing.com パーサー（ボット検知対策）"""

    @property
    def domain(self) -> str:
        return "investing.com"

    @property
    def needs_tls_fingerprint(self) -> bool:
        return True

    def extract_content(self, html: str) -> str | None:
        """investing.com の記事本文を抽出"""
        if _BeautifulSoup is None:
            return None
        soup = _BeautifulSoup(html, "lxml")
        for tag in soup.select(
            "script, style, .ad-slot, .disclaimer, "
            "aside, nav, footer"
        ):
            tag.decompose()
        body = (
            soup.select_one(
                "div.article_WYSIWYG__O0uhw"
            )
            or soup.select_one(
                "div[data-test='article-body']"
            )
            or soup.select_one(".articlePage")
            or soup.select_one("article")
        )
        if not body:
            return None
        return _extract_paragraphs(body)


class CNBCParser(ArticleParser):
    """cnbc.com パーサー"""

    @property
    def domain(self) -> str:
        return "cnbc.com"

    def extract_content(self, html: str) -> str | None:
        """cnbc.com の記事本文を抽出"""
        if _BeautifulSoup is None:
            return None
        soup = _BeautifulSoup(html, "lxml")
        for tag in soup.select(
            "script, style, .InlineVideo, aside, nav, "
            "footer, .RelatedContent"
        ):
            tag.decompose()
        body = (
            soup.select_one(
                "div.ArticleBody-articleBody"
            )
            or soup.select_one(
                "div[data-module='ArticleBody']"
            )
            or soup.select_one("article")
        )
        if not body:
            return None
        return _extract_paragraphs(body)


class DailyFXParser(ArticleParser):
    """dailyfx.com パーサー（ig.comへリダイレクト対応）"""

    @property
    def domain(self) -> str:
        return "dailyfx.com"

    @property
    def needs_tls_fingerprint(self) -> bool:
        return True

    def extract_content(self, html: str) -> str | None:
        """dailyfx.com / ig.com の記事本文を抽出

        dailyfx.comはig.comにリダイレクトされるため、
        両方のHTML構造に対応する。
        """
        if _BeautifulSoup is None:
            return None
        soup = _BeautifulSoup(html, "lxml")
        for tag in soup.select(
            "script, style, .dfx-ad, aside, nav, footer, "
            ".esma-rw, .cmp-pullout"
        ):
            tag.decompose()
        # ig.com構造: .simple-text p タグ群
        simple_texts = soup.select(
            ".simple-text p"
        )
        if simple_texts:
            paragraphs = [
                p.get_text(strip=True)
                for p in simple_texts
                if len(p.get_text(strip=True)) > 20
            ]
            if paragraphs:
                return "\n".join(paragraphs)
        # dailyfx.com旧構造
        body = (
            soup.select_one("div.dfx-article__content")
            or soup.select_one(
                ".dfx-articleBody__content"
            )
            or soup.select_one("article")
        )
        if not body:
            return None
        return _extract_paragraphs(body)


class BBCParser(ArticleParser):
    """bbc.com パーサー（段落ベース抽出）"""

    @property
    def domain(self) -> str:
        return "bbc.com"

    def extract_content(self, html: str) -> str | None:
        """bbc.com の記事本文を抽出"""
        if _BeautifulSoup is None:
            return None
        soup = _BeautifulSoup(html, "lxml")
        for tag in soup.select(
            "script, style, nav, footer, aside"
        ):
            tag.decompose()
        # BBC は data-component='text-block' 内の段落を使用
        text_blocks = soup.select(
            "[data-component='text-block'] p"
        )
        if text_blocks:
            paragraphs = [
                p.get_text(strip=True)
                for p in text_blocks
                if p.get_text(strip=True)
            ]
            return "\n".join(paragraphs) if paragraphs else None
        # フォールバック: article内の全段落
        article = soup.select_one("article")
        if article:
            return _extract_paragraphs(article)
        return None


class MarketWatchParser(ArticleParser):
    """marketwatch.com パーサー

    注意: DataDome保護のためJS実行が必要。
    多くの記事でHTTP 401が返る可能性あり。
    """

    @property
    def domain(self) -> str:
        return "marketwatch.com"

    @property
    def needs_tls_fingerprint(self) -> bool:
        return True

    def extract_content(self, html: str) -> str | None:
        """marketwatch.com の記事本文を抽出

        Wayback Machine経由のHTMLにも対応。
        CSS-in-JS構造のため section > p を優先使用。
        """
        if _BeautifulSoup is None:
            return None
        soup = _BeautifulSoup(html, "lxml")
        for tag in soup.select(
            "script, style, .advertisement, aside, "
            "nav, footer, header, "
            "#wm-ipp-base, #wm-ipp-print"
        ):
            tag.decompose()
        # section内の段落群（CSS-in-JS構造対応）
        paragraphs: list[str] = []
        for section in soup.find_all("section"):
            for p in section.find_all("p"):
                text = p.get_text(strip=True)
                if len(text) > 30:
                    paragraphs.append(text)
        if paragraphs:
            return "\n".join(paragraphs)
        # 従来のセレクタ（直接取得成功時）
        body = (
            soup.select_one("div.article__body")
            or soup.select_one(
                "div[itemprop='articleBody']"
            )
            or soup.select_one("article")
        )
        if not body:
            return None
        return _extract_paragraphs(body)


class ParserRegistry:
    """ドメイン→パーサーのマッピングレジストリ"""

    def __init__(self) -> None:
        self._parsers: dict[str, ArticleParser] = {}

    def register(self, parser: ArticleParser) -> None:
        """パーサーを登録

        Args:
            parser: 登録するパーサーインスタンス
        """
        self._parsers[parser.domain] = parser

    def get(self, domain: str) -> ArticleParser | None:
        """ドメインに対応するパーサーを取得

        Args:
            domain: ドメイン名

        Returns:
            ArticleParser | None: パーサー（未登録時None）
        """
        return self._parsers.get(domain)

    @property
    def domains(self) -> list[str]:
        """登録済みドメイン一覧"""
        return list(self._parsers.keys())


def _build_default_registry() -> ParserRegistry:
    """デフォルトパーサーレジストリを構築

    Returns:
        ParserRegistry: 全パーサー登録済みレジストリ
    """
    registry = ParserRegistry()
    for parser_cls in (
        FXStreetParser,
        ForexLiveParser,
        InvestingComParser,
        CNBCParser,
        DailyFXParser,
        BBCParser,
        MarketWatchParser,
    ):
        registry.register(parser_cls())
    return registry


# モジュールレベルのデフォルトレジストリ
_default_registry = _build_default_registry()


@dataclass
class ArticleFetcher:
    """記事本文取得エンジン

    ドメイン別パーサーとHTTPクライアントを組み合わせて
    記事本文を取得する。レート制御・エラーハンドリング付き。

    Args:
        registry: パーサーレジストリ
        timeout: HTTPタイムアウト（秒）
        rate_limit: ドメインあたりのリクエスト間隔（秒）
    """

    registry: ParserRegistry = field(
        default_factory=lambda: _default_registry
    )
    timeout: float = _DEFAULT_TIMEOUT
    rate_limit: float = _DEFAULT_RATE_LIMIT
    # ドメインごとの最終リクエスト時刻
    _last_request: dict[str, float] = field(
        default_factory=dict, repr=False
    )

    def fetch(
        self, url: str, source_domain: str | None = None
    ) -> ScrapeResult:
        """記事本文を取得

        Args:
            url: 記事URL
            source_domain: ソースドメイン（省略時はURLから推定）

        Returns:
            ScrapeResult: スクレイピング結果
        """
        domain = source_domain or _extract_domain(url)
        if not domain:
            return ScrapeResult(
                status="error",
                error_msg="ドメイン抽出失敗",
            )

        parser = self.registry.get(domain)
        if parser is None:
            return ScrapeResult(
                status="error",
                error_msg=f"未対応ドメイン: {domain}",
            )

        # レート制御
        self._wait_rate_limit(domain)

        html: str | None = None
        # Waybackフォールバック対象ドメインは直接取得をスキップ
        use_wayback = (
            domain in _WAYBACK_FALLBACK_DOMAINS
        )

        if not use_wayback:
            try:
                html = self._fetch_html(
                    url, parser.needs_tls_fingerprint
                )
            except TimeoutError:
                return ScrapeResult(
                    status="timeout",
                    error_msg=f"タイムアウト: {url}",
                )
            except Exception as e:
                # 直接取得失敗 → Waybackフォールバック試行
                if domain in _WAYBACK_FALLBACK_DOMAINS:
                    use_wayback = True
                else:
                    return ScrapeResult(
                        status="error",
                        error_msg=f"HTTP取得失敗: {e}",
                    )

        # Wayback Machine フォールバック
        if use_wayback or not html:
            try:
                html = self._fetch_via_wayback(url)
            except Exception as e:
                return ScrapeResult(
                    status="error",
                    error_msg=(
                        f"Wayback取得失敗: {e}"
                    ),
                )

        if not html:
            return ScrapeResult(
                status="error",
                error_msg="空レスポンス",
            )

        try:
            content = parser.extract_content(html)
        except Exception as e:
            logger.debug(
                f"[Scraper] パース失敗 {domain}: {e}"
            )
            return ScrapeResult(
                status="error",
                error_msg=f"パース失敗: {e}",
            )

        if not content:
            return ScrapeResult(
                status="error",
                error_msg="コンテンツ抽出失敗",
            )

        cleaned = _clean_text(content, _MAX_CONTENT_CHARS)
        return ScrapeResult(content=cleaned, status="ok")

    def _wait_rate_limit(self, domain: str) -> None:
        """ドメイン別レート制御

        Args:
            domain: 対象ドメイン
        """
        last = self._last_request.get(domain, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < self.rate_limit:
            wait = self.rate_limit - elapsed
            time.sleep(wait)
        self._last_request[domain] = time.monotonic()

    def _fetch_html(
        self, url: str, use_tls_fingerprint: bool
    ) -> str:
        """HTMLを取得

        Args:
            url: 取得先URL
            use_tls_fingerprint: curl-cffiを使用するか

        Returns:
            str: HTMLテキスト

        Raises:
            RuntimeError: 必要なHTTPライブラリが未インストール
            TimeoutError: タイムアウト
        """
        if use_tls_fingerprint:
            return self._fetch_with_curl(url)
        return self._fetch_with_httpx(url)

    def _fetch_with_httpx(self, url: str) -> str:
        """httpxでHTML取得

        Args:
            url: 取得先URL

        Returns:
            str: HTMLテキスト
        """
        if _httpx is None:
            raise RuntimeError(
                "httpx が必要です: pip install httpx"
            )
        response = _httpx.get(
            url,
            timeout=self.timeout,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text

    def _fetch_via_wayback(self, url: str) -> str:
        """Wayback Machine経由でHTMLを取得

        DataDome等でブロックされるサイトのフォールバック。
        archive.org APIでスナップショットURLを取得し、
        アーカイブ版のHTMLを返す。

        Args:
            url: 元記事URL

        Returns:
            str: アーカイブ版HTMLテキスト

        Raises:
            RuntimeError: httpx未インストール
            LookupError: スナップショットが存在しない
        """
        if _httpx is None:
            raise RuntimeError(
                "httpx が必要です: pip install httpx"
            )
        # Wayback Availability API
        api_url = _WAYBACK_API.format(url=url)
        resp = _httpx.get(
            api_url,
            timeout=self.timeout,
            follow_redirects=True,
        )
        data = resp.json()
        snapshots = data.get(
            "archived_snapshots", {}
        )
        closest = snapshots.get("closest", {})
        if not closest or not closest.get("available"):
            raise LookupError(
                f"Waybackスナップショットなし: {url}"
            )

        archive_url = closest["url"]
        logger.debug(
            f"[Scraper] Wayback使用: {archive_url}"
        )
        # アーカイブ版HTMLを取得
        resp2 = _httpx.get(
            archive_url,
            timeout=self.timeout,
            follow_redirects=True,
        )
        resp2.raise_for_status()
        return resp2.text

    def _fetch_with_curl(self, url: str) -> str:
        """curl-cffiでHTML取得（TLSフィンガープリント付き）

        Args:
            url: 取得先URL

        Returns:
            str: HTMLテキスト
        """
        if _curl_requests is None:
            raise RuntimeError(
                "curl-cffi が必要です: "
                "pip install curl-cffi"
            )
        response = _curl_requests.get(
            url,
            timeout=self.timeout,
            impersonate="chrome120",
        )
        response.raise_for_status()
        return str(response.text)


def _extract_domain(url: str) -> str | None:
    """URLからドメインを抽出

    www. プレフィックスを除去する。

    Args:
        url: 記事URL

    Returns:
        str | None: ドメイン名（抽出失敗時None）
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return None
        # www. を除去
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname
    except Exception:
        return None


def _extract_paragraphs(
    element: object,  # bs4.Tag（型ガード）
) -> str | None:
    """HTML要素から段落テキストを抽出

    Args:
        element: BeautifulSoup Tag要素

    Returns:
        str | None: 段落テキスト（空の場合None）
    """
    paragraphs = element.find_all("p")  # type: ignore[attr-defined]
    texts = [
        p.get_text(strip=True)
        for p in paragraphs
        if p.get_text(strip=True)
    ]
    return "\n".join(texts) if texts else None


def _clean_text(
    raw: str, max_chars: int = _MAX_CONTENT_CHARS
) -> str:
    """テキスト正規化・切り詰め

    - 連続空白行を単一改行に正規化
    - 行頭・行末の空白を除去
    - 最大文字数で切り詰め（単語境界）

    Args:
        raw: 生テキスト
        max_chars: 最大文字数

    Returns:
        str: 正規化済みテキスト
    """
    if not raw:
        return ""
    # 連続空白行を正規化
    text = re.sub(r"\n\s*\n+", "\n", raw)
    # 各行の前後空白を除去
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)

    if len(text) <= max_chars:
        return text

    # 単語境界で切り詰め
    truncated = text[:max_chars]
    # 最後のスペースまたは改行位置で切る
    last_break = max(
        truncated.rfind(" "),
        truncated.rfind("\n"),
    )
    if last_break > max_chars * 0.8:
        truncated = truncated[:last_break]
    return truncated.rstrip() + "..."
