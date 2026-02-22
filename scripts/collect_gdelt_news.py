"""GDELT ニュース一括収集スクリプト

GDELT DOC API v2 を使ってFX関連ニュースを年次・通貨別に
一括収集し、CSV形式で保存する。

前提:
  pip install requests

使用方法:
  # 2024年のUSD/JPY関連ニュースを収集
  python scripts/collect_gdelt_news.py \\
      --year 2024 --currencies USD,JPY

  # 2010-2025年の全主要通貨を収集
  python scripts/collect_gdelt_news.py \\
      --years 2010-2025 --currencies USD,JPY,EUR,GBP,AUD

  # 出力先を指定
  python scripts/collect_gdelt_news.py \\
      --year 2024 --currencies USD,JPY \\
      --output data/fundamental/

出力先: data/fundamental/news_YYYY.csv（年ごと）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# プロジェクトルートをパスに追加（フラット構造対応）
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from loguru import logger

from autotrader.adapters.fundamental.gdelt_client import (
    GDELTDocClient,
)
from autotrader.adapters.fundamental.news_csv_writer import (
    read_news_csv,
    write_news_csv,
)

# デフォルト出力ディレクトリ
_DEFAULT_OUTPUT_DIR = "data/fundamental"

# デフォルト通貨
_DEFAULT_CURRENCIES = ["USD", "JPY", "EUR", "GBP"]


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパース

    Returns:
        argparse.Namespace: パース済み引数
    """
    parser = argparse.ArgumentParser(
        description="GDELT FXニュース一括収集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  # 2024年のUSD/JPY関連ニュースを収集
  python scripts/collect_gdelt_news.py \\
      --year 2024 --currencies USD,JPY

  # 2010-2025年を収集
  python scripts/collect_gdelt_news.py \\
      --years 2010-2025 --currencies USD,JPY,EUR,GBP,AUD
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--year",
        type=int,
        help="単一年指定（例: 2024）",
    )
    group.add_argument(
        "--years",
        type=str,
        help="年範囲指定（例: 2010-2025）",
    )
    parser.add_argument(
        "--currencies",
        type=str,
        default=",".join(_DEFAULT_CURRENCIES),
        help="収集対象通貨（カンマ区切り、例: USD,JPY,EUR）",
    )
    parser.add_argument(
        "--output",
        default=_DEFAULT_OUTPUT_DIR,
        help="出力ディレクトリ",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存ファイルを上書き（デフォルトは追記なし・スキップ）",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=1.5,
        help="APIリクエスト間隔（秒）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際にAPIを呼び出さずに処理件数を確認",
    )
    return parser.parse_args()


def parse_year_range(years_str: str) -> list[int]:
    """年範囲文字列をパース

    Args:
        years_str: "2010-2025" 形式の文字列

    Returns:
        list[int]: 年のリスト

    Raises:
        ValueError: フォーマット不正時
    """
    if "-" not in years_str:
        raise ValueError(
            f"年範囲フォーマットが不正: {years_str}. "
            "例: 2010-2025"
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
        years = [args.year]
    else:
        try:
            years = parse_year_range(args.years)
        except ValueError as e:
            logger.error(str(e))
            return 1

    currencies = [
        c.strip().upper() for c in args.currencies.split(",")
        if c.strip()
    ]
    if not currencies:
        logger.error("通貨が指定されていません")
        return 1

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        f"対象通貨: {currencies}\n"
        f"対象年: {years[0]}〜{years[-1]} ({len(years)}年)\n"
        f"出力ディレクトリ: {output_dir}\n"
        f"APIリクエスト間隔: {args.request_interval}秒"
    )

    if args.dry_run:
        for year in years:
            out_path = output_dir / f"news_{year}.csv"
            status = (
                "✓ 既存" if out_path.exists()
                else "✗ 未存在"
            )
            weeks = 52
            logger.info(
                f"[DryRun] {year}: {out_path} {status} "
                f"(約{weeks}リクエスト × "
                f"{args.request_interval}秒)"
            )
        return 0

    client = GDELTDocClient(
        request_interval_sec=args.request_interval
    )

    error_count = 0
    success_count = 0

    for year in years:
        out_path = output_dir / f"news_{year}.csv"

        if out_path.exists() and not args.overwrite:
            existing = read_news_csv(out_path)
            logger.info(
                f"[{year}] スキップ（既存: {len(existing)}件）: "
                f"{out_path}"
            )
            success_count += 1
            continue

        logger.info(f"[{year}] GDELT収集開始...")
        try:
            items = client.fetch_news_year(currencies, year)
            if not items:
                logger.warning(
                    f"[{year}] 取得件数0件（APIエラーの可能性）"
                )
                error_count += 1
                continue

            write_news_csv(items, out_path, append=False)
            logger.info(
                f"[{year}] 完了: {len(items)}件 → {out_path}"
            )
            success_count += 1
        except Exception as e:
            logger.error(f"[{year}] 予期しないエラー: {e}")
            error_count += 1

    logger.info(
        f"\n完了: {success_count}件成功, "
        f"{error_count}件エラー"
    )
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
