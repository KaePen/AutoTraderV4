"""ニュース記事本文一括スクレイピングスクリプト

フィルタ済みCSV（news_rss_YYYY.csv）の各記事URLにアクセスし、
ドメイン別パーサーで本文を抽出してCSVに保存する。

中断耐性:
  - 取得した本文は1件ごとにキャッシュファイル(.cache.jsonl)に追記
  - --resume で既取得分をスキップし、キャッシュから復元して続行
  - 定期的にCSVへマージし、完了時にキャッシュファイルを削除

使用方法:
  python scripts/scrape_news_content.py --years 2020-2025 --resume
  python scripts/scrape_news_content.py --year 2024 --resume
  python scripts/scrape_news_content.py --year 2024 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# プロジェクトルートをパスに追加
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from loguru import logger  # noqa: E402

from autotrader.adapters.fundamental.article_scraper import (  # noqa: E402
    ArticleFetcher,
)
from autotrader.adapters.fundamental.news_csv_writer import (  # noqa: E402
    read_news_csv,
    write_news_csv,
)

# CSVマージ間隔（件数）
_CSV_MERGE_INTERVAL = 500


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパース

    Returns:
        argparse.Namespace: パース済み引数
    """
    parser = argparse.ArgumentParser(
        description="ニュース記事本文一括スクレイピング",
    )
    group = parser.add_mutually_exclusive_group(
        required=True
    )
    group.add_argument(
        "--year",
        type=int,
        nargs="+",
        help="対象年（複数指定可）",
    )
    group.add_argument(
        "--years",
        type=str,
        help="年範囲（例: 2020-2025）",
    )
    parser.add_argument(
        "--input-dir",
        default="data/fundamental",
        help="入力ディレクトリ",
    )
    parser.add_argument(
        "--input-prefix",
        default="news_rss",
        help="入力ファイル名プレフィックス",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=2.0,
        help="ドメインあたりのリクエスト間隔（秒）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="1リクエストあたりのタイムアウト（秒）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="content既取得のURLをスキップ",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存出力ファイルを上書き",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="処理件数のみ確認",
    )
    return parser.parse_args()


def parse_year_range(years_str: str) -> list[int]:
    """年範囲文字列をパース

    Args:
        years_str: "2020-2025" 形式

    Returns:
        list[int]: 年のリスト
    """
    parts = years_str.split("-")
    if len(parts) != 2:
        raise ValueError(
            f"年範囲フォーマットが不正: {years_str}"
        )
    start, end = int(parts[0]), int(parts[1])
    return list(range(start, end + 1))


def _cache_path(csv_path: Path) -> Path:
    """キャッシュファイルパスを返す

    Args:
        csv_path: 元CSVのパス

    Returns:
        Path: キャッシュファイルパス (.cache.jsonl)
    """
    return csv_path.with_suffix(".cache.jsonl")


def _load_cache(
    cache_file: Path,
) -> dict[str, str]:
    """キャッシュファイルからcontent辞書を読み込み

    Args:
        cache_file: キャッシュファイルパス

    Returns:
        dict[str, str]: news_id → content の辞書
    """
    cache: dict[str, str] = {}
    if not cache_file.exists():
        return cache
    with open(cache_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                nid = entry.get("news_id", "")
                content = entry.get("content", "")
                if nid and content:
                    cache[nid] = content
            except json.JSONDecodeError:
                continue
    logger.info(
        f"[Cache] {len(cache)}件復元: {cache_file.name}"
    )
    return cache


def _append_cache(
    cache_file: Path,
    news_id: str,
    content: str,
) -> None:
    """キャッシュファイルに1件追記

    Args:
        cache_file: キャッシュファイルパス
        news_id: ニュースID
        content: 取得した本文
    """
    with open(
        cache_file, "a", encoding="utf-8"
    ) as f:
        entry = json.dumps(
            {"news_id": news_id, "content": content},
            ensure_ascii=False,
        )
        f.write(entry + "\n")


def main() -> int:
    """メイン処理

    Returns:
        int: 終了コード
    """
    args = parse_args()

    # 年リスト構築
    if args.year:
        years = args.year
    else:
        try:
            years = parse_year_range(args.years)
        except ValueError as e:
            logger.error(str(e))
            return 1

    input_dir = Path(args.input_dir)
    prefix = args.input_prefix

    # dry-run
    if args.dry_run:
        for year in years:
            csv_path = input_dir / f"{prefix}_{year}.csv"
            if not csv_path.exists():
                logger.info(f"  {csv_path}: 未存在")
                continue
            items = read_news_csv(csv_path)
            cache_file = _cache_path(csv_path)
            cache = _load_cache(cache_file)
            # CSV上のcontentとキャッシュの合算
            with_content = sum(
                1
                for i in items
                if i.content or i.news_id in cache
            )
            logger.info(
                f"  {csv_path}: {len(items)}件 "
                f"(本文取得済み: {with_content}件, "
                f"未取得: {len(items) - with_content}件)"
                + (
                    f" [キャッシュ: {len(cache)}件]"
                    if cache
                    else ""
                )
            )
        return 0

    # フェッチャー初期化
    fetcher = ArticleFetcher(
        timeout=args.timeout,
        rate_limit=args.rate_limit,
    )

    for year in years:
        csv_path = input_dir / f"{prefix}_{year}.csv"
        if not csv_path.exists():
            logger.warning(f"ファイルなし: {csv_path}")
            continue

        items = read_news_csv(csv_path)
        logger.info(
            f"[{year}] {len(items)}件読込: {csv_path}"
        )

        # キャッシュ復元
        cache_file = _cache_path(csv_path)
        cache = _load_cache(cache_file)
        if cache:
            restored = 0
            for item in items:
                if (
                    not item.content
                    and item.news_id in cache
                ):
                    item.content = cache[item.news_id]
                    restored += 1
            if restored:
                logger.info(
                    f"[{year}] キャッシュから"
                    f"{restored}件復元"
                )

        success = 0
        skipped = 0
        failed = 0
        fetched_since_merge = 0
        start_time = time.monotonic()

        for idx, item in enumerate(items, 1):
            # レジュームモード: 既取得はスキップ
            if args.resume and item.content:
                skipped += 1
                continue

            result = fetcher.fetch(
                item.source_url, item.source_name
            )
            if result.status == "ok" and result.content:
                item.content = result.content
                success += 1
                # 1件ごとにキャッシュ追記
                _append_cache(
                    cache_file,
                    item.news_id,
                    result.content,
                )
                fetched_since_merge += 1
            else:
                failed += 1
                if result.error_msg:
                    logger.debug(
                        f"[{year}] 失敗 "
                        f"{item.source_url}: "
                        f"{result.error_msg}"
                    )

            # 定期CSVマージ
            if (
                fetched_since_merge > 0
                and fetched_since_merge
                % _CSV_MERGE_INTERVAL
                == 0
            ):
                write_news_csv(items, csv_path)
                elapsed = (
                    time.monotonic() - start_time
                )
                logger.info(
                    f"[{year}] {idx}/{len(items)} "
                    f"({elapsed:.0f}秒) "
                    f"成功={success} 失敗={failed} "
                    f"スキップ={skipped}"
                )

        # 最終マージ: CSV書き込み + キャッシュ削除
        write_news_csv(items, csv_path)
        if cache_file.exists():
            cache_file.unlink()
            logger.info(
                f"[{year}] キャッシュファイル削除: "
                f"{cache_file.name}"
            )
        elapsed = time.monotonic() - start_time
        logger.info(
            f"[{year}] 完了 ({elapsed:.0f}秒): "
            f"成功={success} 失敗={failed} "
            f"スキップ={skipped} / 全{len(items)}件"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
