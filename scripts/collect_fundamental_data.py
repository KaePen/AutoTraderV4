"""経済イベント過去データ収集スクリプト

ForexFactoryまたはMT5から経済カレンダーデータを収集し、
data/fundamental/events_YYYY.csv 形式で保存する。

使用方法:
  python scripts/collect_fundamental_data.py --year 2024 --source ff
  python scripts/collect_fundamental_data.py --year 2024 --source mt5
  python scripts/collect_fundamental_data.py --years 2018-2024 --source ff
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

# プロジェクトルートをパスに追加
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from loguru import logger

# デフォルト出力ディレクトリ
_DEFAULT_OUTPUT = "data/fundamental"

# デフォルト対象通貨
_DEFAULT_CURRENCIES = ["USD", "JPY", "EUR", "GBP", "AUD", "CAD", "CHF", "NZD"]

# CSVヘッダー
_CSV_HEADER = [
    "event_id", "event_time", "currency", "event_name",
    "impact", "actual", "forecast", "previous",
]


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパース

    Returns:
        argparse.Namespace: パース済み引数
    """
    parser = argparse.ArgumentParser(
        description="経済イベント過去データ収集スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  # 2024年のデータをForexFactoryから収集
  python scripts/collect_fundamental_data.py --year 2024 --source ff

  # 2018〜2024年のデータを収集
  python scripts/collect_fundamental_data.py --years 2018-2024 --source ff

  # MT5から2023年のUSD/JPY関連データを収集
  python scripts/collect_fundamental_data.py --year 2023 --source mt5 \\
    --currencies USD,JPY
        """,
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="収集対象年（単一年）",
    )
    parser.add_argument(
        "--years",
        type=str,
        default=None,
        help="収集対象年範囲（例: 2018-2024）",
    )
    parser.add_argument(
        "--source",
        choices=["ff", "mt5"],
        default="ff",
        help="データソース（ff=ForexFactory, mt5=MetaTrader5）",
    )
    parser.add_argument(
        "--currencies",
        type=str,
        default=",".join(_DEFAULT_CURRENCIES),
        help=f"対象通貨（カンマ区切り、デフォルト: {','.join(_DEFAULT_CURRENCIES)}）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=_DEFAULT_OUTPUT,
        help=f"出力ディレクトリ（デフォルト: {_DEFAULT_OUTPUT}）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存ファイルを上書き（デフォルト: スキップ）",
    )
    return parser.parse_args()


def parse_years(args: argparse.Namespace) -> list[int]:
    """引数から対象年リストを生成

    Args:
        args: パース済み引数

    Returns:
        list[int]: 対象年リスト
    """
    if args.year is not None:
        return [args.year]
    if args.years is not None:
        if "-" in args.years:
            parts = args.years.split("-")
            start, end = int(parts[0]), int(parts[1])
            return list(range(start, end + 1))
        return [int(args.years)]
    # デフォルト: 現在年
    return [datetime.now(timezone.utc).year]


def events_to_csv_rows(events: list) -> list[list[str]]:
    """EconomicEventリストをCSV行リストに変換

    Args:
        events: EconomicEventリスト

    Returns:
        list[list[str]]: CSV行リスト
    """
    rows = []
    for ev in events:
        rows.append([
            ev.event_id,
            ev.event_time.isoformat(),
            ev.currency,
            ev.event_name,
            ev.impact.value,
            str(ev.actual) if ev.actual is not None else "",
            str(ev.forecast) if ev.forecast is not None else "",
            str(ev.previous) if ev.previous is not None else "",
        ])
    return rows


def collect_ff(
    year: int,
    currencies: list[str],
    output_dir: Path,
    overwrite: bool,
) -> int:
    """ForexFactoryから年間データを収集

    Args:
        year: 対象年
        currencies: 対象通貨リスト
        output_dir: 出力ディレクトリ
        overwrite: 既存ファイル上書きフラグ

    Returns:
        int: 収集イベント数
    """
    from autotrader.adapters.fundamental.forex_factory import (
        ForexFactoryClient,
    )

    output_path = output_dir / f"events_{year}.csv"
    if output_path.exists() and not overwrite:
        logger.info(
            f"[ForexFactory] スキップ（既存）: {output_path}"
        )
        return 0

    client = ForexFactoryClient(timeout=30.0, rate_limit_hours=0.0)
    events = client.fetch_historical_year(year, currencies=currencies)

    if not events:
        logger.warning(
            f"[ForexFactory] {year}年: イベントが取得できませんでした"
        )
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = events_to_csv_rows(events)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        writer.writerows(rows)

    logger.info(
        f"[ForexFactory] {year}年: {len(events)}件 → {output_path}"
    )
    return len(events)


def collect_mt5(
    year: int,
    currencies: list[str],
    output_dir: Path,
    overwrite: bool,
) -> int:
    """MT5カレンダーから年間データを収集

    Args:
        year: 対象年
        currencies: 対象通貨リスト
        output_dir: 出力ディレクトリ
        overwrite: 既存ファイル上書きフラグ

    Returns:
        int: 収集イベント数
    """
    try:
        from autotrader.adapters.fundamental.mt5_calendar import (
            MT5CalendarClient,
        )
    except ImportError:
        logger.error(
            "[MT5] mt5_calendarモジュールが見つかりません"
        )
        return 0

    output_path = output_dir / f"events_{year}.csv"
    if output_path.exists() and not overwrite:
        logger.info(
            f"[MT5] スキップ（既存）: {output_path}"
        )
        return 0

    client = MT5CalendarClient()
    from datetime import datetime, timezone
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    try:
        events = client.fetch_events(
            from_date=start,
            to_date=end,
            currencies=currencies,
        )
    except Exception as e:
        logger.error(f"[MT5] {year}年データ取得エラー: {e}")
        return 0

    if not events:
        logger.warning(
            f"[MT5] {year}年: イベントが取得できませんでした"
        )
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = events_to_csv_rows(events)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        writer.writerows(rows)

    logger.info(
        f"[MT5] {year}年: {len(events)}件 → {output_path}"
    )
    return len(events)


def main() -> None:
    """メインエントリポイント"""
    args = parse_args()
    years = parse_years(args)
    currencies = [c.strip().upper() for c in args.currencies.split(",")]
    output_dir = Path(args.output)

    logger.info(
        f"データ収集開始: {years} / ソース={args.source} / "
        f"通貨={currencies} / 出力={output_dir}"
    )

    total = 0
    for year in years:
        if args.source == "ff":
            count = collect_ff(year, currencies, output_dir, args.overwrite)
        else:
            count = collect_mt5(year, currencies, output_dir, args.overwrite)
        total += count

    logger.info(f"収集完了: 合計{total}件")


if __name__ == "__main__":
    main()
