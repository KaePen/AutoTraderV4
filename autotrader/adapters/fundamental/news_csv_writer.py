"""ニュースCSV読み書きユーティリティ

ニュースアイテムをCSV形式で保存・読み込みする。
出力先: data/fundamental/news_YYYY.csv
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from autotrader.adapters.fundamental.news_schemas import (
    NewsItem,
    NewsSource,
)

# CSVカラム定義
_NEWS_CSV_COLUMNS = [
    "news_id",
    "published_at",
    "title",
    "source_name",
    "source_url",
    "currencies",
    "source_type",
    "snippet",
    "content",
]


def write_news_csv(
    news_items: list[NewsItem],
    output_path: Path,
    append: bool = False,
) -> None:
    """ニュースアイテムをCSVに書き込み

    Args:
        news_items: 書き込むニュースアイテムリスト
        output_path: 出力先パス
        append: Trueの場合は追記（デフォルトは上書き）
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    write_header = not (
        append and output_path.exists()
        and output_path.stat().st_size > 0
    )

    with open(
        output_path, mode, encoding="utf-8", newline=""
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=_NEWS_CSV_COLUMNS
        )
        if write_header:
            writer.writeheader()
        for item in news_items:
            writer.writerow({
                "news_id": item.news_id,
                "published_at": item.published_at.isoformat(),
                "title": item.title,
                "source_name": item.source_name,
                "source_url": item.source_url,
                "currencies": json.dumps(
                    item.currencies, ensure_ascii=False
                ),
                "source_type": item.source_type.value,
                "snippet": item.snippet or "",
                "content": item.content or "",
            })

    logger.info(
        f"[NewsCsv] {len(news_items)}件書込: {output_path}"
    )


def read_news_csv(csv_path: Path) -> list[NewsItem]:
    """ニュースCSVを読み込み

    Args:
        csv_path: 読み込むCSVファイルパス

    Returns:
        list[NewsItem]: 読み込んだニュースアイテムリスト
    """
    if not csv_path.exists():
        logger.warning(
            f"[NewsCsv] ファイルが見つかりません: {csv_path}"
        )
        return []

    items: list[NewsItem] = []

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                item = _parse_row(row)
                if item:
                    items.append(item)
            except Exception as e:
                logger.debug(
                    f"[NewsCsv] 行スキップ: {e}"
                )
                continue

    logger.info(
        f"[NewsCsv] {len(items)}件読込: {csv_path.name}"
    )
    return items


def filter_news_csv(
    input_path: Path,
    output_path: Path,
    allowed_sources: frozenset[str],
) -> int:
    """ニュースCSVをソースドメインでフィルタリング

    ストリーミング処理でメモリ効率的にフィルタリングする。
    NewsItemへの変換は行わず、生CSV行をそのまま書き出す。

    Args:
        input_path: 入力CSVパス
        output_path: 出力CSVパス
        allowed_sources: 許可するソースドメインのセット

    Returns:
        int: 書き出した行数
    """
    if not input_path.exists():
        logger.warning(
            f"[NewsCsv] 入力ファイルなし: {input_path}"
        )
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    with (
        open(input_path, encoding="utf-8") as fin,
        open(
            output_path, "w", encoding="utf-8", newline=""
        ) as fout,
    ):
        reader = csv.DictReader(fin)
        # 入力CSVのヘッダーを尊重（旧CSV後方互換）
        fieldnames = reader.fieldnames or _NEWS_CSV_COLUMNS
        # content列がなければ追加
        if "content" not in fieldnames:
            fieldnames = list(fieldnames) + ["content"]
        writer = csv.DictWriter(
            fout, fieldnames=fieldnames
        )
        writer.writeheader()

        for row in reader:
            source = row.get("source_name", "")
            if source in allowed_sources:
                # 旧CSVにcontent列がない場合は空文字補完
                row.setdefault("content", "")
                writer.writerow(row)
                count += 1

    logger.info(
        f"[NewsCsv] フィルタ完了: {count}件 → {output_path}"
    )
    return count


def _parse_row(row: dict) -> NewsItem | None:
    """CSV行をNewsItemに変換

    Args:
        row: CSV行辞書

    Returns:
        NewsItem | None: 変換済みアイテム（不正行はNone）
    """
    news_id = row.get("news_id", "")
    if not news_id:
        return None

    published_str = row.get("published_at", "")
    if not published_str:
        return None

    try:
        published_at = datetime.fromisoformat(published_str)
        if published_at.tzinfo is None:
            published_at = published_at.replace(
                tzinfo=UTC
            )
    except ValueError:
        return None

    title = row.get("title", "")
    if not title:
        return None

    # currencies はJSON配列として保存
    currencies_str = row.get("currencies", "[]")
    try:
        currencies = json.loads(currencies_str)
        if not isinstance(currencies, list):
            currencies = []
    except (json.JSONDecodeError, ValueError):
        currencies = []

    source_type_str = row.get(
        "source_type", NewsSource.GDELT.value
    )
    try:
        source_type = NewsSource(source_type_str)
    except ValueError:
        source_type = NewsSource.GDELT

    snippet = row.get("snippet") or None
    content = row.get("content") or None

    return NewsItem(
        news_id=news_id,
        published_at=published_at,
        title=title,
        source_name=row.get("source_name", ""),
        source_url=row.get("source_url", ""),
        currencies=currencies,
        source_type=source_type,
        snippet=snippet,
        content=content,
    )
