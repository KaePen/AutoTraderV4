"""GDELT BigQuery クライアント

GDELT GKG（Global Knowledge Graph）v2 テーブルを
Google BigQuery で直接クエリし、FX関連ニュースを取得する。

REST API と異なりレートリミットなし・タイムアウトなし。
BigQuery 無料枠: 月1TB（1年分 ≈ 1-3GB 消費）。

前提:
  pip install google-cloud-bigquery
  環境変数 GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json を設定

使用方法:
  client = GDELTBigQueryClient(project_id="your-project")
  items = client.fetch_news_year(["USD", "JPY"], 2020)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from autotrader.adapters.fundamental.news_schemas import (
    CURRENCY_KEYWORDS,
    NewsItem,
    NewsSource,
)

try:
    from google.cloud import bigquery as _bigquery_module
except ImportError:
    _bigquery_module = None  # type: ignore[assignment]

# GDELT GKG v2 BigQuery ワイルドカードテーブルベース名
# 実際のテーブルは gdelt-bq.gdeltv2.gkg20150101000000 のような
# 15分単位シャードテーブルとして格納されている
_GKG_TABLE_BASE = "gdelt-bq.gdeltv2.gkg"

# FX関連テーマ（GDELT GKG V2Themes）
_FX_THEMES_PATTERN = (
    "ECON_CENTRALBANKS|ECON_INTERESTRATES"
    "|ECON_MONETARYPOLICY|ECON_MONEYSUPPLY"
    "|ECON_INFLATION|ECON_UNEMPLOYMENT"
    "|ECON_TRADE|ECON_CURRENCYCRISIS"
    "|ECON_FISCALSTRESS|ECON_GLOBALECONOMY"
)

# 通貨別中央銀行組織名（V2Organizations 検索用）
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


class GDELTBigQueryClient:
    """GDELT GKG BigQuery クライアント

    GKGテーブルからFX関連ニュースを取得し NewsItem リストを返す。
    既存の GDELTDocClient と同じインターフェースを持つ。

    Args:
        project_id: Google Cloud プロジェクトID
        max_results: 1年あたりの最大取得件数
    """

    def __init__(
        self,
        project_id: str,
        max_results: int = 100_000,
    ) -> None:
        """初期化

        Args:
            project_id: Google Cloud プロジェクトID
            max_results: 1年あたりの最大取得件数
        """
        if _bigquery_module is None:
            raise ImportError(
                "google-cloud-bigquery が必要: "
                "pip install google-cloud-bigquery"
            )
        self._client = _bigquery_module.Client(
            project=project_id
        )
        self._max_results = max_results

    def fetch_news_year(
        self,
        currencies: list[str],
        year: int,
    ) -> list[NewsItem]:
        """1年分のFX関連ニュースをBigQueryから取得

        Args:
            currencies: 対象通貨コードリスト（例: ["USD", "JPY"]）
            year: 対象年（例: 2020）

        Returns:
            list[NewsItem]: 取得したニュースアイテムリスト
        """
        query = self._build_query(currencies, year)
        logger.info(
            f"[BigQuery] {year}年クエリ実行中..."
        )

        try:
            results = self._client.query(query).result()
        except Exception as e:
            logger.error(
                f"[BigQuery] クエリ失敗 ({year}年): {e}"
            )
            return []

        items: list[NewsItem] = []
        seen_ids: set[str] = set()

        for row in results:
            item = self._row_to_news_item(
                row, currencies
            )
            if item and item.news_id not in seen_ids:
                seen_ids.add(item.news_id)
                items.append(item)

        logger.info(
            f"[BigQuery] {year}年完了: {len(items)}件取得"
        )
        return items

    def _build_query(
        self,
        currencies: list[str],
        year: int,
    ) -> str:
        """BigQuery SQLクエリを構築

        ワイルドカードテーブル `gkg{year}*` を使用することで
        指定年のシャードテーブルのみをスキャンし高速化する。
        GKG テーブルは15分単位のシャードテーブル群として格納。

        Args:
            currencies: 対象通貨コードリスト
            year: 対象年

        Returns:
            str: BigQuery SQL文字列
        """
        # 通貨別中央銀行名の条件（ORで結合）
        org_parts: list[str] = []
        for cur in currencies:
            orgs = _CURRENCY_ORGS.get(cur.upper(), [])
            for org in orgs:
                org_parts.append(
                    f"REGEXP_CONTAINS(V2Organizations, "
                    f"r'{org}')"
                )

        theme_cond = (
            f"REGEXP_CONTAINS(V2Themes, "
            f"r'{_FX_THEMES_PATTERN}')"
        )
        if org_parts:
            org_cond = " OR ".join(org_parts)
            where_cond = (
                f"({theme_cond} OR {org_cond})"
            )
        else:
            where_cond = theme_cond

        # ワイルドカードで該当年のシャードテーブルのみスキャン
        # 例: gdelt-bq.gdeltv2.gkg2015*
        wildcard_table = f"{_GKG_TABLE_BASE}{year}*"

        return f"""
        SELECT
          DATE,
          SourceCommonName,
          DocumentIdentifier,
          V2Tone,
          V2Themes,
          V2Organizations
        FROM `{wildcard_table}`
        WHERE
          {where_cond}
          AND DocumentIdentifier IS NOT NULL
          AND DocumentIdentifier != ''
        LIMIT {self._max_results}
        """

    def _row_to_news_item(
        self,
        row: Any,
        target_currencies: list[str],
    ) -> NewsItem | None:
        """BigQueryの行をNewsItemに変換

        Args:
            row: BigQuery行オブジェクト
            target_currencies: 対象通貨リスト

        Returns:
            NewsItem | None: 変換済みアイテム（無効データはNone）
        """
        url = getattr(row, "DocumentIdentifier", "") or ""
        if not url:
            return None

        # 日時パース（INTEGER: YYYYMMDDHHMMSS）
        published_at = _parse_gkg_date(
            str(getattr(row, "DATE", "") or "")
        )
        if published_at is None:
            return None

        source_name = (
            getattr(row, "SourceCommonName", "") or ""
        )
        themes_raw = getattr(row, "V2Themes", "") or ""
        orgs_raw = (
            getattr(row, "V2Organizations", "") or ""
        )
        tone_raw = getattr(row, "V2Tone", "") or ""

        # タイトルを組織名・テーマから構成
        title = _build_title(
            source_name, orgs_raw, themes_raw
        )

        # 通貨検出
        currencies = _extract_currencies_from_gkg(
            orgs_raw, themes_raw, target_currencies
        )
        if not currencies:
            currencies = list(target_currencies)

        # スニペットにトーン値を格納（Tone,Pos,Neg,...）
        tone_parts = tone_raw.split(",")
        snippet = (
            f"Tone:{tone_parts[0]}"
            if tone_parts and tone_parts[0]
            else None
        )

        return NewsItem(
            news_id=NewsItem.make_id(
                NewsSource.GDELT, url
            ),
            published_at=published_at,
            title=title,
            source_name=source_name,
            source_url=url,
            currencies=currencies,
            source_type=NewsSource.GDELT,
            snippet=snippet,
        )


def _parse_gkg_date(date_str: str) -> datetime | None:
    """GKG DATE（YYYYMMDDHHMMSS整数文字列）をdatetimeに変換

    Args:
        date_str: YYYYMMDDHHMMSS 形式の文字列

    Returns:
        datetime | None: 変換済みdatetime（UTC）
    """
    clean = date_str.strip()
    if len(clean) < 14:
        return None
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
        return None


def _build_title(
    source: str,
    orgs_raw: str,
    themes_raw: str,
) -> str:
    """GKGフィールドからニュースタイトルを生成

    V2Organizations と V2Themes から人間が読めるタイトルを構成。

    Args:
        source: ニュースソース名
        orgs_raw: V2Organizations生データ（name,offset;...形式）
        themes_raw: V2Themes生データ（THEME;THEME;...形式）

    Returns:
        str: 生成タイトル文字列
    """
    # 上位2組織名を抽出（形式: name,charoffset;...）
    org_names = [
        part.split(",")[0].strip()
        for part in orgs_raw.split(";")
        if part.strip() and "," in part
    ][:2]

    # 上位2経済テーマを抽出（ECON_* のみ）
    econ_themes = [
        t.split(",")[0].replace("ECON_", "")
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


def _extract_currencies_from_gkg(
    orgs_raw: str,
    themes_raw: str,
    target_currencies: list[str],
) -> list[str]:
    """GKGの組織名・テーマから関連通貨を抽出

    Args:
        orgs_raw: V2Organizations生データ
        themes_raw: V2Themes生データ
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
