"""GDELT GKG v1 日次CSV ダウンローダー

GDELT GKG v1 の日次 CSV ファイルを直接ダウンロードし、
FX 関連ニュースを抽出する。

完全無料・API キー不要・レート制限なし。

データ仕様:
  URL: http://data.gdeltproject.org/gkg/YYYYMMDD.gkg.csv.zip
  形式: タブ区切り（ヘッダーなし）、11カラム
  期間: 2013-04-01 以降

GKG v1 カラム:
  0: DATE         YYYYMMDD
  1: NUMARTS      記事数
  2: COUNTS       イベントカウント
  3: THEMES       テーマ（セミコロン区切り）
  4: LOCATIONS    場所
  5: PERSONS      人名
  6: ORGANIZATIONS 組織名（セミコロン区切り）
  7: TONE         トーン値（カンマ区切り）
  8: CAMEOEVENTIDS
  9: SOURCES      ソースドメイン
  10: SOURCEURLS  記事URL（<UDIV>区切り）
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from autotrader.adapters.fundamental.news_schemas import (
    CURRENCY_KEYWORDS,
    NewsItem,
    NewsSource,
)

try:
    import requests as _requests_module
except ImportError:
    _requests_module = None  # type: ignore[assignment]

# GKG v1 日次 CSV URL
_GKG_BASE_URL = "http://data.gdeltproject.org/gkg"

# リクエスト間隔（秒）
_REQUEST_INTERVAL_SEC = 1.0

# HTTP タイムアウト（秒）
_TIMEOUT_SEC = 30.0

# GKG v1 カラムインデックス
_COL_THEMES = 3
_COL_ORGANIZATIONS = 6
_COL_TONE = 7
_COL_SOURCES = 9
_COL_SOURCEURLS = 10
_MIN_COLS = 11

# GKG v1 FX 関連テーマセット（厳格フィルタ）
# 注意: GKG v2 とテーマ名が異なる
#   v2: ECON_CENTRALBANKS / v1: ECON_CENTRALBANK
#   v2: ECON_INTERESTRATES / v1: ECON_INTEREST_RATES
# v1 に存在しないテーマ（MONETARYPOLICY, MONEYSUPPLY, TRADE 等）は除外
# 意図的に除外:
#   ECON_INTEREST_RATES: 2,580件/日と多すぎ（不動産・法律記事も拾う）
#   ECON_WORLDCURRENCIES_DOLLAR/DOLLARS: 3,100件/日（"dollar" 含む記事全般）
_FX_THEMES: frozenset[str] = frozenset({
    # 中央銀行（FX 最重要・約1,300件/日）
    "ECON_CENTRALBANK",
    # 為替レート直接（約600件/日）
    "ECON_CURRENCY_EXCHANGE_RATE",
    # 主要通貨別テーマ（通貨名固有・広義 DOLLAR 等を除外）
    "ECON_WORLDCURRENCIES_US_DOLLAR",     # USD 固有
    "ECON_WORLDCURRENCIES_YEN",           # JPY
    "ECON_WORLDCURRENCIES_JAPANESE_YEN",  # JPY 別称
    "ECON_WORLDCURRENCIES_EURO",          # EUR
    "ECON_WORLDCURRENCIES_BRITISH_POUND", # GBP
    "ECON_WORLDCURRENCIES_CANADIAN_DOLLAR",    # CAD
    "ECON_WORLDCURRENCIES_AUSTRALIAN_DOLLAR",  # AUD
    "ECON_WORLDCURRENCIES_SWISS_FRANC",   # CHF
})

# 通貨別中央銀行組織名
_CURRENCY_ORGS: dict[str, list[str]] = {
    "USD": ["Federal Reserve", "Fed"],
    "JPY": ["Bank of Japan", "BOJ"],
    "EUR": ["European Central Bank", "ECB"],
    "GBP": ["Bank of England", "BOE"],
    "AUD": ["Reserve Bank of Australia", "RBA"],
    "NZD": ["Reserve Bank of New Zealand", "RBNZ"],
    "CHF": ["Swiss National Bank", "SNB"],
    "CAD": ["Bank of Canada", "BOC"],
}


class GDELTGKGDownloader:
    """GDELT GKG v1 日次 CSV ダウンローダー

    日次 CSV ファイルを直接 HTTP で取得し NewsItem を返す。
    既存の GDELTDocClient と同一インターフェース。

    Args:
        request_interval_sec: リクエスト間隔（秒）
        timeout_sec: HTTP タイムアウト（秒）
    """

    def __init__(
        self,
        request_interval_sec: float = _REQUEST_INTERVAL_SEC,
        timeout_sec: float = _TIMEOUT_SEC,
    ) -> None:
        """初期化

        Args:
            request_interval_sec: リクエスト間隔（秒）
            timeout_sec: HTTP タイムアウト（秒）
        """
        if _requests_module is None:
            raise ImportError(
                "requests が必要: pip install requests"
            )
        self._interval = request_interval_sec
        self._timeout = timeout_sec
        self._last_request_time: float = 0.0

    def fetch_news_year(
        self,
        currencies: list[str],
        year: int,
    ) -> list[NewsItem]:
        """1年分の FX ニュースを日次 CSV から収集

        Args:
            currencies: 対象通貨コードリスト
            year: 対象年

        Returns:
            list[NewsItem]: 取得したニュースアイテムリスト
        """
        all_items: list[NewsItem] = []
        seen_ids: set[str] = set()

        current = date(year, 1, 1)
        year_end = date(year + 1, 1, 1)
        total_days = (year_end - current).days
        day_num = 0

        while current < year_end:
            day_num += 1
            # 30日ごとに進捗ログ
            if day_num % 30 == 1:
                logger.info(
                    f"[GDELT-GKG] {year}年 "
                    f"{day_num}/{total_days}日: "
                    f"{current.strftime('%m/%d')} "
                    f"({len(all_items)}件取得済み)"
                )

            self._wait_rate_limit()
            items = self._fetch_day(currencies, current)

            for item in items:
                if item.news_id not in seen_ids:
                    seen_ids.add(item.news_id)
                    all_items.append(item)

            current += timedelta(days=1)

        logger.info(
            f"[GDELT-GKG] {year}年完了: {len(all_items)}件"
        )
        return all_items

    def _fetch_day(
        self,
        currencies: list[str],
        target_date: date,
    ) -> list[NewsItem]:
        """1日分の GKG CSV をダウンロード・パース

        Args:
            currencies: 対象通貨コードリスト
            target_date: 対象日

        Returns:
            list[NewsItem]: その日の FX 関連ニュースアイテム
        """
        date_str = target_date.strftime("%Y%m%d")
        url = f"{_GKG_BASE_URL}/{date_str}.gkg.csv.zip"

        try:
            resp = _requests_module.get(
                url, timeout=self._timeout
            )
        except Exception as e:
            logger.debug(
                f"[GDELT-GKG] 接続失敗 ({date_str}): {e}"
            )
            return []

        if resp.status_code == 404:
            # 週末・祝日などデータなしは正常
            return []

        if resp.status_code != 200:
            logger.debug(
                f"[GDELT-GKG] HTTP {resp.status_code} "
                f"({date_str})"
            )
            return []

        try:
            return _parse_gkg_zip(
                resp.content, currencies, target_date
            )
        except Exception as e:
            logger.debug(
                f"[GDELT-GKG] パース失敗 ({date_str}): {e}"
            )
            return []

    def _wait_rate_limit(self) -> None:
        """レートリミットのための待機"""
        elapsed = (
            time.monotonic() - self._last_request_time
        )
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last_request_time = time.monotonic()


def _parse_gkg_zip(
    data: bytes,
    currencies: list[str],
    target_date: date,
) -> list[NewsItem]:
    """ZIP を展開して GKG CSV をパース

    Args:
        data: ZIP バイナリ
        currencies: 対象通貨コードリスト
        target_date: 対象日（published_at に使用）

    Returns:
        list[NewsItem]: パース結果
    """
    items: list[NewsItem] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if not name.endswith(".csv"):
                continue
            with z.open(name) as f:
                for raw_line in f:
                    line = raw_line.decode(
                        "utf-8", errors="replace"
                    )
                    item = _parse_gkg_row(
                        line, currencies, target_date
                    )
                    if item:
                        items.append(item)
    return items


def _parse_gkg_row(
    line: str,
    currencies: list[str],
    target_date: date,
) -> NewsItem | None:
    """GKG v1 CSV の 1 行を NewsItem に変換

    Args:
        line: タブ区切りの CSV 行
        currencies: 対象通貨コードリスト
        target_date: 対象日

    Returns:
        NewsItem | None: FX 非関連・URL なしは None
    """
    cols = line.rstrip("\n").split("\t")
    if len(cols) < _MIN_COLS:
        return None

    themes_raw = cols[_COL_THEMES]
    orgs_raw = cols[_COL_ORGANIZATIONS]

    # FX 関連チェック（非関連は早期リターン）
    if not _is_fx_relevant(themes_raw, orgs_raw, currencies):
        return None

    # SOURCEURLS から最初の有効な URL を取得
    urls_raw = cols[_COL_SOURCEURLS]
    url = _extract_first_url(urls_raw)
    if not url:
        return None

    source_name = cols[_COL_SOURCES].split(",")[0].strip()
    tone_raw = cols[_COL_TONE]
    tone_val = tone_raw.split(",")[0].strip() if tone_raw else ""

    published_at = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        tzinfo=timezone.utc,
    )

    title = _build_title(source_name, orgs_raw, themes_raw)

    currencies_found = _extract_currencies(
        orgs_raw, themes_raw, currencies
    )
    if not currencies_found:
        currencies_found = list(currencies)

    return NewsItem(
        news_id=NewsItem.make_id(NewsSource.GDELT, url),
        published_at=published_at,
        title=title,
        source_name=source_name,
        source_url=url,
        currencies=currencies_found,
        source_type=NewsSource.GDELT,
        snippet=f"Tone:{tone_val}" if tone_val else None,
    )


def _is_fx_relevant(
    themes_raw: str,
    orgs_raw: str,
    currencies: list[str],
) -> bool:
    """FX 関連コンテンツかどうかを判定

    Args:
        themes_raw: THEMES カラム生データ
        orgs_raw: ORGANIZATIONS カラム生データ
        currencies: 対象通貨コードリスト

    Returns:
        bool: FX 関連であれば True
    """
    # テーマによる判定
    themes_set = set(themes_raw.split(";"))
    if themes_set & _FX_THEMES:
        return True

    # 中央銀行組織名による判定
    combined_upper = (
        f"{orgs_raw} {themes_raw}".upper()
    )
    for cur in currencies:
        orgs = _CURRENCY_ORGS.get(cur.upper(), [])
        if any(o.upper() in combined_upper for o in orgs):
            return True

    return False


def _extract_first_url(urls_raw: str) -> str:
    """SOURCEURLS から最初の有効な URL を取得

    GDELT GKG v1 の SOURCEURLS は <UDIV> またはカンマで区切られる。

    Args:
        urls_raw: SOURCEURLS カラム生データ

    Returns:
        str: 最初の有効な URL（なければ空文字）
    """
    if not urls_raw:
        return ""

    # <UDIV> 区切り（GKG v1 公式）
    for sep in ("<UDIV>", ","):
        parts = [p.strip() for p in urls_raw.split(sep)]
        for part in parts:
            if part.startswith("http"):
                return part

    return ""


def _build_title(
    source: str,
    orgs_raw: str,
    themes_raw: str,
) -> str:
    """組織名・テーマからニュースタイトルを生成

    Args:
        source: ニュースソース名
        orgs_raw: ORGANIZATIONS 生データ
        themes_raw: THEMES 生データ

    Returns:
        str: 生成タイトル文字列
    """
    org_names = [
        o.strip()
        for o in orgs_raw.split(";")
        if o.strip()
    ][:2]

    econ_themes = [
        t.replace("ECON_", "")
        for t in themes_raw.split(";")
        if t.startswith("ECON_")
    ][:2]

    prefix = f"[{source}] " if source else ""
    parts: list[str] = []
    if org_names:
        parts.append(", ".join(org_names))
    if econ_themes:
        parts.append(", ".join(econ_themes))

    body = " - ".join(parts) if parts else "FX News"
    return f"{prefix}{body}"


def _extract_currencies(
    orgs_raw: str,
    themes_raw: str,
    target_currencies: list[str],
) -> list[str]:
    """組織名・テーマから関連通貨を抽出

    Args:
        orgs_raw: ORGANIZATIONS 生データ
        themes_raw: THEMES 生データ
        target_currencies: 対象通貨コードリスト

    Returns:
        list[str]: 検出された通貨コードリスト
    """
    combined = f"{orgs_raw} {themes_raw}".upper()
    found: list[str] = []

    for cur in target_currencies:
        cur_upper = cur.upper()
        keywords = CURRENCY_KEYWORDS.get(cur_upper, [])
        if cur_upper in combined or any(
            kw.upper() in combined for kw in keywords
        ):
            if cur_upper not in found:
                found.append(cur_upper)

    return found
