"""GDELT DOC API v2 クライアント

GDELT（Global Database of Events, Language and Tone）の
DOC API v2 を使い、FX関連ニュースを取得する。

API制約:
  - 最大250件/リクエスト
  - レートリミット: 1.5秒/リクエスト推奨（429時は自動バックオフ）
  - 期間指定: YYYYMMDDHHMMSS 形式
  - データカバレッジ: 2015年以降が安定（2014年以前は件数僅少）
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from loguru import logger

from autotrader.adapters.fundamental.news_schemas import (
    CURRENCY_KEYWORDS,
    NewsItem,
    NewsSource,
)

try:
    import requests as _requests_module
except ImportError:
    _requests_module = None  # type: ignore[assignment]

# GDELT DOC API v2 エンドポイント
_GDELT_API_URL = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
)

# レートリミット（秒）
_REQUEST_INTERVAL_SEC = 1.5

# 429 バックオフ秒数（attempt × この値）
_RATE_LIMIT_BACKOFF_SEC = 30.0

# 429 発生時の最大リトライ回数
_MAX_RETRIES = 3

# 1リクエストあたりの最大件数
_MAX_RECORDS = 250


class GDELTDocClient:
    """GDELT DOC API v2 クライアント

    FX通貨関連ニュースを期間指定で取得する。

    Args:
        request_interval_sec: リクエスト間隔（秒）
        max_records: 1リクエストの最大取得件数
        timeout_sec: HTTPタイムアウト（秒）
    """

    def __init__(
        self,
        request_interval_sec: float = _REQUEST_INTERVAL_SEC,
        max_records: int = _MAX_RECORDS,
        timeout_sec: float = 10.0,
    ) -> None:
        """初期化

        Args:
            request_interval_sec: リクエスト間隔（秒）
            max_records: 1リクエストの最大取得件数
            timeout_sec: HTTPタイムアウト（秒）
        """
        self._interval = request_interval_sec
        self._max_records = max_records
        self._timeout = timeout_sec
        self._last_request_time: float = 0.0

    def fetch_news_week(
        self,
        currencies: list[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[NewsItem]:
        """指定期間の通貨関連ニュースを取得

        Args:
            currencies: 対象通貨コードリスト（例: ["USD", "JPY"]）
            start_dt: 期間開始（UTC）
            end_dt: 期間終了（UTC）

        Returns:
            list[NewsItem]: 取得したニュースアイテムリスト
        """
        if _requests_module is None:
            logger.error(
                "[GDELT] requests が必要です: "
                "pip install requests"
            )
            return []

        query = self._build_query(currencies)
        start_str = _fmt_gdelt_dt(start_dt)
        end_str = _fmt_gdelt_dt(end_dt)

        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": str(self._max_records),
            "startdatetime": start_str,
            "enddatetime": end_str,
            "format": "json",
            "sort": "DateDesc",
        }

        for attempt in range(_MAX_RETRIES):
            self._wait_rate_limit()

            # HTTP リクエスト
            try:
                resp = _requests_module.get(
                    _GDELT_API_URL,
                    params=params,
                    timeout=self._timeout,
                )
            except Exception as e:
                logger.warning(
                    f"[GDELT] 接続エラー "
                    f"({start_str}〜{end_str}): {e}"
                )
                return []

            # 429: バックオフして再試行
            if resp.status_code == 429:
                wait = _RATE_LIMIT_BACKOFF_SEC * (attempt + 1)
                logger.warning(
                    f"[GDELT] 429 Too Many Requests, "
                    f"{wait:.0f}秒待機後リトライ "
                    f"({attempt + 1}/{_MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            # その他HTTPエラー
            try:
                resp.raise_for_status()
            except Exception as e:
                logger.warning(
                    f"[GDELT] HTTPエラー "
                    f"({start_str}〜{end_str}): {e}"
                )
                return []

            # 空レスポンス: 2015年以前など対象期間にデータなし
            text = resp.text.strip()
            if not text:
                logger.debug(
                    f"[GDELT] データなし "
                    f"({start_str}〜{end_str})"
                )
                return []

            # JSON パース
            try:
                data = resp.json()
            except Exception as e:
                logger.debug(
                    f"[GDELT] JSONパース失敗 "
                    f"({start_str}〜{end_str}): {e}"
                )
                return []

            articles = data.get("articles") or []
            items = []
            for article in articles:
                item = self._parse_article(article, currencies)
                if item:
                    items.append(item)

            logger.debug(
                f"[GDELT] {start_str}〜{end_str}: "
                f"{len(items)}件取得"
            )
            return items

        # 全リトライ失敗
        logger.warning(
            f"[GDELT] {_MAX_RETRIES}回リトライ後スキップ "
            f"({start_str}〜{end_str})"
        )
        return []

    def fetch_news_year(
        self,
        currencies: list[str],
        year: int,
    ) -> list[NewsItem]:
        """1年分を週単位でループして取得

        約52リクエストを順次実行し、年間のニュースを収集する。

        Args:
            currencies: 対象通貨コードリスト
            year: 対象年

        Returns:
            list[NewsItem]: 取得したニュースアイテムリスト
        """
        all_items: list[NewsItem] = []
        seen_ids: set[str] = set()

        # 週ごとにループ
        current = datetime(year, 1, 1, tzinfo=timezone.utc)
        year_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        week_delta = timedelta(weeks=1)

        week_num = 0
        while current < year_end:
            week_end = min(current + week_delta, year_end)
            week_num += 1
            logger.info(
                f"[GDELT] {year}年 第{week_num}週: "
                f"{current.strftime('%m/%d')}〜"
                f"{week_end.strftime('%m/%d')}"
            )

            items = self.fetch_news_week(
                currencies, current, week_end
            )

            # 重複排除
            for item in items:
                if item.news_id not in seen_ids:
                    seen_ids.add(item.news_id)
                    all_items.append(item)

            current = week_end

        logger.info(
            f"[GDELT] {year}年完了: {len(all_items)}件"
        )
        return all_items

    def _build_query(self, currencies: list[str]) -> str:
        """通貨リストからGDELTクエリを構築

        Args:
            currencies: 対象通貨コードリスト

        Returns:
            str: GDELTクエリ文字列
        """
        keywords: list[str] = []
        for currency in currencies:
            kws = CURRENCY_KEYWORDS.get(currency.upper(), [])
            keywords.extend(kws[:2])  # 各通貨から上位2キーワード

        if not keywords:
            keywords = ["forex", "currency", "central bank"]

        # クエリはOR条件で結合
        query_parts = [f'"{kw}"' for kw in keywords[:6]]
        return " OR ".join(query_parts)

    def _parse_article(
        self,
        article: dict,
        target_currencies: list[str],
    ) -> NewsItem | None:
        """GDELT記事データをNewsItemに変換

        Args:
            article: GDELT APIの記事dict
            target_currencies: 対象通貨リスト（フィルタ用）

        Returns:
            NewsItem | None: 変換済みアイテム
        """
        url = article.get("url", "")
        if not url:
            return None

        title = article.get("title", "").strip()
        if not title:
            return None

        # 公開日時パース
        seendate = article.get("seendate", "")
        published_at = _parse_gdelt_date(seendate)
        if published_at is None:
            return None

        source_name = article.get("domain", "")
        # GDELT DOC API v2 はテキストスニペットを返さないため None
        snippet = None

        # タイトルから通貨を抽出
        currencies = _extract_currencies(
            title, target_currencies
        )
        # 対象通貨が含まれない場合は target_currencies を付与
        if not currencies:
            currencies = list(target_currencies)

        news_id = NewsItem.make_id(NewsSource.GDELT, url)

        return NewsItem(
            news_id=news_id,
            published_at=published_at,
            title=title,
            source_name=source_name,
            source_url=url,
            currencies=currencies,
            source_type=NewsSource.GDELT,
            snippet=snippet,
        )

    def _wait_rate_limit(self) -> None:
        """レートリミットのための待機処理"""
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last_request_time = time.monotonic()


def _fmt_gdelt_dt(dt: datetime) -> str:
    """GDELT APIの日時フォーマット（YYYYMMDDHHMMSS）に変換

    Args:
        dt: 変換するdatetime（UTC）

    Returns:
        str: YYYYMMDDHHMMSS形式の文字列
    """
    return dt.strftime("%Y%m%d%H%M%S")


def _parse_gdelt_date(
    date_str: str,
) -> datetime | None:
    """GDELT日時文字列をdatetimeに変換

    GDELTは "YYYYMMDDTHHMMSSZ" または
    "YYYYMMDDHHMMSS" 形式を返す場合がある。

    Args:
        date_str: GDELTの日時文字列

    Returns:
        datetime | None: 変換済みdatetime（UTC）
    """
    if not date_str:
        return None

    clean = (
        date_str.replace("T", "").replace("Z", "").strip()
    )
    if len(clean) >= 14:
        try:
            return datetime(
                int(clean[0:4]),
                int(clean[4:6]),
                int(clean[6:8]),
                int(clean[8:10]),
                int(clean[10:12]),
                int(clean[12:14]),
                tzinfo=timezone.utc,
            )
        except (ValueError, IndexError):
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
    text_upper = text.upper()

    for currency in target_currencies:
        cur_upper = currency.upper()
        # 通貨コードまたはキーワードで判定
        keywords = CURRENCY_KEYWORDS.get(cur_upper, [])
        if cur_upper in text_upper or any(
            kw.upper() in text_upper for kw in keywords
        ):
            if cur_upper not in found:
                found.append(cur_upper)

    return found
