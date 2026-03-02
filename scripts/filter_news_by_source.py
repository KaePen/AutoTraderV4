"""ニュースCSVをRSSソースでフィルタリングするスクリプト

GDELTから取得した全ニュースCSVをFX専門RSSソースのみに
絞り込み、軽量なデータセットを生成する。

使用方法:
  python scripts/filter_news_by_source.py --years 2015-2025
  python scripts/filter_news_by_source.py --year 2024 --overwrite

出力先: data/fundamental/news_rss_YYYY.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import logging

logger = logging.getLogger(__name__)

from autotrader.adapters.fundamental.news_csv_writer import (
    filter_news_csv,
)
from autotrader.adapters.fundamental.news_schemas import (
    FX_RSS_SOURCES,
)

# デフォルトディレクトリ
_DEFAULT_DIR = "data/fundamental"


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパース

    Returns:
        argparse.Namespace: パース済み引数
    """
    parser = argparse.ArgumentParser(
        description=(
            "ニュースCSVをRSSソースでフィルタリング"
        ),
        formatter_class=(
            argparse.RawDescriptionHelpFormatter
        ),
        epilog="""
例:
  # 2024年をフィルタリング
  python scripts/filter_news_by_source.py --year 2024

  # 2015〜2025年を一括フィルタリング
  python scripts/filter_news_by_source.py --years 2015-2025

  # 既存ファイルを上書き
  python scripts/filter_news_by_source.py --year 2024 --overwrite
        """,
    )
    group = parser.add_mutually_exclusive_group(
        required=True
    )
    group.add_argument(
        "--year",
        type=int,
        nargs="+",
        metavar="YEAR",
        help="年指定（1つまたは複数スペース区切り）",
    )
    group.add_argument(
        "--years",
        type=str,
        help="年範囲指定（例: 2015-2025）",
    )
    parser.add_argument(
        "--input-dir",
        default=_DEFAULT_DIR,
        help="入力CSVディレクトリ",
    )
    parser.add_argument(
        "--output-dir",
        default=_DEFAULT_DIR,
        help="出力CSVディレクトリ",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存ファイルを上書き",
    )
    return parser.parse_args()


def parse_year_range(years_str: str) -> list[int]:
    """年範囲文字列をパース

    Args:
        years_str: "2015-2025" 形式の文字列

    Returns:
        list[int]: 年のリスト

    Raises:
        ValueError: フォーマット不正時
    """
    if "-" not in years_str:
        raise ValueError(
            f"年範囲フォーマットが不正: {years_str}. "
            "例: 2015-2025"
        )
    parts = years_str.split("-")
    if len(parts) != 2:
        raise ValueError(
            f"年範囲フォーマットが不正: {years_str}"
        )
    start, end = int(parts[0]), int(parts[1])
    if start > end:
        raise ValueError(
            f"開始年 > 終了年: {start} > {end}"
        )
    return list(range(start, end + 1))


def main() -> int:
    """メイン処理

    Returns:
        int: 終了コード（0=成功、1=エラー）
    """
    args = parse_args()

    # 年リストを構築
    if args.year:
        years = args.year
    else:
        try:
            years = parse_year_range(args.years)
        except ValueError as e:
            logger.error(str(e))
            return 1

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    logger.info(
        f"フィルタリング開始\n"
        f"  対象年: {years[0]}〜{years[-1]}"
        f" ({len(years)}年)\n"
        f"  入力: {input_dir}\n"
        f"  出力: {output_dir}\n"
        f"  ソース数: {len(FX_RSS_SOURCES)}"
    )

    total = 0
    skipped = 0

    for year in years:
        input_path = input_dir / f"news_{year}.csv"
        output_path = output_dir / f"news_rss_{year}.csv"

        if output_path.exists() and not args.overwrite:
            logger.info(
                f"  [{year}] スキップ（既存）: "
                f"{output_path}"
            )
            skipped += 1
            continue

        count = filter_news_csv(
            input_path, output_path, FX_RSS_SOURCES
        )
        total += count
        logger.info(f"  [{year}] {count}件抽出")

    logger.info(
        f"完了: 合計{total}件抽出, "
        f"{skipped}件スキップ"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
