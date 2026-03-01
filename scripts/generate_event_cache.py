#!/usr/bin/env python3
"""ファンダメンタルデータの通貨ペア別csv/cache構造生成

全ファンダメンタルデータを通貨ペア別に整理し、
Parquetキャッシュを生成するスクリプト。

生成される構造:
    data/{SYMBOL}/
      csv/
        events_YYYY.csv              - 通貨ペアの2通貨でフィルタ済み
        llm_events_{SYMBOL}_YYYY.csv - コピー
        llm_news_{SYMBOL}_YYYY.csv   - コピー（あれば）
      cache/
        events_YYYY.parquet          - eventsキャッシュ
        llm_events_{SYMBOL}_YYYY.parquet - llm_eventsキャッシュ
        llm_news_{SYMBOL}_YYYY.parquet   - llm_newsキャッシュ

使用例:
    # 全通貨ペア・全年を処理
    uv run python scripts/generate_event_cache.py

    # 特定通貨ペアのみ
    uv run python scripts/generate_event_cache.py --symbols USDJPY,EURUSD

    # 特定年範囲
    uv run python scripts/generate_event_cache.py --years 2020-2024

    # 既存キャッシュを上書き
    uv run python scripts/generate_event_cache.py --force
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

# プロジェクトルートをパスに追加
try:
    _project_root = Path(__file__).parent.parent
except NameError:
    _project_root = Path("D:/Projects/AutoTraderV4")
sys.path.insert(0, str(_project_root))

# シンボル→通貨ペア（base, quote）マッピング
_SYMBOL_CURRENCIES: dict[str, tuple[str, str]] = {
    "USDJPY": ("USD", "JPY"),
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
    "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"),
    "EURJPY": ("EUR", "JPY"),
    "GBPJPY": ("GBP", "JPY"),
    "AUDJPY": ("AUD", "JPY"),
    "CADJPY": ("CAD", "JPY"),
    "CHFJPY": ("CHF", "JPY"),
    "EURGBP": ("EUR", "GBP"),
    "GBPCHF": ("GBP", "CHF"),
}


def _get_currencies(symbol: str) -> tuple[str, str]:
    """シンボルからbase/quote通貨を取得

    Args:
        symbol: 通貨ペアシンボル

    Returns:
        tuple[str, str]: (base, quote)
    """
    if symbol in _SYMBOL_CURRENCIES:
        return _SYMBOL_CURRENCIES[symbol]
    # フォールバック: 6文字のシンボルから推測
    return symbol[:3].upper(), symbol[3:6].upper()


def _get_all_symbols(data_dir: Path) -> list[str]:
    """config/symbol_presets.yaml から全シンボルを取得

    Args:
        data_dir: データベースディレクトリ

    Returns:
        list[str]: シンボルリスト
    """
    try:
        import yaml

        preset_path = (
            _project_root / "config" / "symbol_presets.yaml"
        )
        if preset_path.exists():
            with open(preset_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            symbols = list(config.get("symbols", {}).keys())
            if symbols:
                return symbols
    except ImportError:
        pass

    # フォールバック: _SYMBOL_CURRENCIES のキー
    return list(_SYMBOL_CURRENCIES.keys())


def _detect_years(
    events_dir: Path,
) -> list[int]:
    """events CSVから利用可能な年リストを検出

    Args:
        events_dir: events CSVディレクトリ

    Returns:
        list[int]: 年リスト（昇順）
    """
    years: list[int] = []
    if not events_dir.exists():
        return years
    for csv_path in sorted(events_dir.glob("events_*.csv")):
        stem = csv_path.stem  # events_2020
        try:
            year = int(stem.split("_")[1])
            years.append(year)
        except (IndexError, ValueError):
            continue
    return sorted(years)


def _filter_events_by_currency(
    csv_path: Path,
    currencies: set[str],
) -> pd.DataFrame:
    """events CSVを通貨でフィルタ

    Args:
        csv_path: 元CSVパス
        currencies: フィルタ対象通貨セット

    Returns:
        pd.DataFrame: フィルタ済みDataFrame
    """
    df = pd.read_csv(csv_path, encoding="utf-8")
    if "currency" not in df.columns:
        return pd.DataFrame()
    # 通貨カラムをフィルタ
    mask = df["currency"].str.upper().isin(currencies)
    return df[mask].copy()


def _csv_to_parquet(
    csv_path: Path,
    parquet_path: Path,
    force: bool = False,
) -> int:
    """CSVをParquetに変換

    Args:
        csv_path: 元CSVパス
        parquet_path: 出力Parquetパス
        force: 既存ファイルを上書きするか

    Returns:
        int: 行数（0ならスキップまたは空）
    """
    if parquet_path.exists() and not force:
        # 既存キャッシュがあり、CSVより新しければスキップ
        if parquet_path.stat().st_mtime >= csv_path.stat().st_mtime:
            return -1  # スキップを示す
    df = pd.read_csv(csv_path, encoding="utf-8")
    if df.empty:
        return 0
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(parquet_path, index=False)
    return len(df)


def _process_events(
    symbol: str,
    years: list[int],
    events_src_dir: Path,
    csv_dir: Path,
    cache_dir: Path,
    force: bool,
) -> dict[str, int]:
    """events CSVを通貨フィルタ→csv/保存→cache/Parquet生成

    Args:
        symbol: 通貨ペアシンボル
        years: 処理対象年
        events_src_dir: 元events CSVディレクトリ
        csv_dir: 出力csv/ディレクトリ
        cache_dir: 出力cache/ディレクトリ
        force: 上書きフラグ

    Returns:
        dict[str, int]: 処理結果統計
    """
    base, quote = _get_currencies(symbol)
    currencies = {base, quote}
    stats = {"csv_created": 0, "cache_created": 0, "skipped": 0}

    for year in years:
        src_csv = events_src_dir / f"events_{year}.csv"
        if not src_csv.exists():
            continue

        dst_csv = csv_dir / f"events_{year}.csv"
        dst_parquet = cache_dir / f"events_{year}.parquet"

        # CSVフィルタ＆保存
        if not dst_csv.exists() or force:
            filtered_df = _filter_events_by_currency(
                src_csv, currencies,
            )
            if not filtered_df.empty:
                csv_dir.mkdir(parents=True, exist_ok=True)
                filtered_df.to_csv(
                    dst_csv, index=False, encoding="utf-8",
                )
                stats["csv_created"] += 1
            else:
                stats["skipped"] += 1
                continue
        else:
            stats["skipped"] += 1

        # Parquetキャッシュ生成
        if dst_csv.exists():
            n = _csv_to_parquet(dst_csv, dst_parquet, force)
            if n > 0:
                stats["cache_created"] += 1
            elif n == -1:
                pass  # スキップ

    return stats


def _process_llm_events(
    symbol: str,
    years: list[int],
    data_dir: Path,
    csv_dir: Path,
    cache_dir: Path,
    force: bool,
) -> dict[str, int]:
    """llm_events CSVをcsv/にコピー→cache/にParquet生成

    Args:
        symbol: 通貨ペアシンボル
        years: 処理対象年
        data_dir: データベースディレクトリ
        csv_dir: 出力csv/ディレクトリ
        cache_dir: 出力cache/ディレクトリ
        force: 上書きフラグ

    Returns:
        dict[str, int]: 処理結果統計
    """
    stats = {"csv_copied": 0, "cache_created": 0, "skipped": 0}
    llm_src_dir = data_dir / symbol / "llm_events"
    if not llm_src_dir.exists():
        return stats

    for year in years:
        fname = f"llm_events_{symbol}_{year}.csv"
        src_csv = llm_src_dir / fname
        if not src_csv.exists():
            continue

        dst_csv = csv_dir / fname
        dst_parquet = (
            cache_dir / f"llm_events_{symbol}_{year}.parquet"
        )

        # csv/にコピー
        if not dst_csv.exists() or force:
            csv_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_csv, dst_csv)
            stats["csv_copied"] += 1
        else:
            stats["skipped"] += 1

        # cache/にParquet生成
        src_for_parquet = dst_csv if dst_csv.exists() else src_csv
        n = _csv_to_parquet(src_for_parquet, dst_parquet, force)
        if n > 0:
            stats["cache_created"] += 1

    return stats


def _process_llm_news(
    symbol: str,
    years: list[int],
    data_dir: Path,
    csv_dir: Path,
    cache_dir: Path,
    force: bool,
) -> dict[str, int]:
    """llm_news CSVをcsv/にコピー→cache/にParquet生成

    Args:
        symbol: 通貨ペアシンボル
        years: 処理対象年
        data_dir: データベースディレクトリ
        csv_dir: 出力csv/ディレクトリ
        cache_dir: 出力cache/ディレクトリ
        force: 上書きフラグ

    Returns:
        dict[str, int]: 処理結果統計
    """
    stats = {"csv_copied": 0, "cache_created": 0, "skipped": 0}
    news_src_dir = data_dir / symbol / "llm_news"
    if not news_src_dir.exists():
        return stats

    for year in years:
        fname = f"llm_news_{symbol}_{year}.csv"
        src_csv = news_src_dir / fname
        if not src_csv.exists():
            continue

        dst_csv = csv_dir / fname
        dst_parquet = (
            cache_dir / f"llm_news_{symbol}_{year}.parquet"
        )

        # csv/にコピー
        if not dst_csv.exists() or force:
            csv_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_csv, dst_csv)
            stats["csv_copied"] += 1
        else:
            stats["skipped"] += 1

        # cache/にParquet生成
        src_for_parquet = dst_csv if dst_csv.exists() else src_csv
        n = _csv_to_parquet(src_for_parquet, dst_parquet, force)
        if n > 0:
            stats["cache_created"] += 1

    return stats


def _format_size(size_bytes: int) -> str:
    """バイトサイズをhuman-readableに変換

    Args:
        size_bytes: バイト数

    Returns:
        str: フォーマット済みサイズ
    """
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


def _count_dir_size(dir_path: Path) -> int:
    """ディレクトリの合計サイズ

    Args:
        dir_path: ディレクトリパス

    Returns:
        int: 合計バイト数
    """
    if not dir_path.exists():
        return 0
    return sum(
        f.stat().st_size
        for f in dir_path.rglob("*")
        if f.is_file()
    )


def main() -> None:
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description=(
            "ファンダメンタルデータの通貨ペア別"
            "csv/cache構造を生成"
        ),
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help=(
            "対象通貨ペア（カンマ区切り）。"
            "未指定時は全ペアを処理。"
        ),
    )
    parser.add_argument(
        "--years",
        type=str,
        default=None,
        help=(
            "年範囲（例: 2020-2024）。"
            "未指定時はデータに合わせて自動検出。"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="既存キャッシュを上書き",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="データベースディレクトリ（デフォルト: data）",
    )
    parser.add_argument(
        "--fundamental-dir",
        type=str,
        default="data/fundamental",
        help=(
            "元events CSVディレクトリ"
            "（デフォルト: data/fundamental）"
        ),
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    fund_dir = Path(args.fundamental_dir)
    events_src_dir = fund_dir / "events"
    if not events_src_dir.exists():
        events_src_dir = fund_dir

    # 対象シンボル
    if args.symbols:
        symbols = [
            s.strip().upper()
            for s in args.symbols.split(",")
        ]
    else:
        symbols = _get_all_symbols(data_dir)

    # 対象年
    if args.years:
        parts = args.years.split("-")
        if len(parts) == 2:
            year_start = int(parts[0])
            year_end = int(parts[1])
            years = list(range(year_start, year_end + 1))
        else:
            years = [int(parts[0])]
    else:
        years = _detect_years(events_src_dir)
        if not years:
            print(
                "[ERROR] events CSVが見つかりません: "
                f"{events_src_dir}"
            )
            sys.exit(1)

    print(
        f"=== ファンダメンタルデータ構造化 ==="
    )
    print(f"対象シンボル: {', '.join(symbols)}")
    print(f"対象年: {years[0]}-{years[-1]}")
    print(f"上書き: {'ON' if args.force else 'OFF'}")
    print()

    # 全体統計
    total_stats = {
        "symbols_processed": 0,
        "events_csv": 0,
        "events_cache": 0,
        "llm_events_csv": 0,
        "llm_events_cache": 0,
        "llm_news_csv": 0,
        "llm_news_cache": 0,
    }

    for symbol in symbols:
        csv_dir = data_dir / symbol / "csv"
        cache_dir = data_dir / symbol / "cache"

        print(f"--- {symbol} ---")

        # 1. events CSV（通貨フィルタ＋コピー＋Parquet）
        ev_stats = _process_events(
            symbol, years, events_src_dir,
            csv_dir, cache_dir, args.force,
        )
        total_stats["events_csv"] += ev_stats["csv_created"]
        total_stats["events_cache"] += ev_stats["cache_created"]
        print(
            f"  events:     CSV {ev_stats['csv_created']}件, "
            f"Parquet {ev_stats['cache_created']}件, "
            f"スキップ {ev_stats['skipped']}件"
        )

        # 2. llm_events（コピー＋Parquet）
        llm_ev_stats = _process_llm_events(
            symbol, years, data_dir,
            csv_dir, cache_dir, args.force,
        )
        total_stats["llm_events_csv"] += (
            llm_ev_stats["csv_copied"]
        )
        total_stats["llm_events_cache"] += (
            llm_ev_stats["cache_created"]
        )
        print(
            f"  llm_events: CSV {llm_ev_stats['csv_copied']}件, "
            f"Parquet {llm_ev_stats['cache_created']}件, "
            f"スキップ {llm_ev_stats['skipped']}件"
        )

        # 3. llm_news（コピー＋Parquet）
        news_stats = _process_llm_news(
            symbol, years, data_dir,
            csv_dir, cache_dir, args.force,
        )
        total_stats["llm_news_csv"] += (
            news_stats["csv_copied"]
        )
        total_stats["llm_news_cache"] += (
            news_stats["cache_created"]
        )
        print(
            f"  llm_news:   CSV {news_stats['csv_copied']}件, "
            f"Parquet {news_stats['cache_created']}件, "
            f"スキップ {news_stats['skipped']}件"
        )

        # ディレクトリサイズ
        csv_size = _count_dir_size(csv_dir)
        cache_size = _count_dir_size(cache_dir)
        print(
            f"  サイズ: csv/ {_format_size(csv_size)}, "
            f"cache/ {_format_size(cache_size)}"
        )

        total_stats["symbols_processed"] += 1

    # サマリー
    print()
    print("=== 処理サマリー ===")
    print(
        f"処理シンボル数: "
        f"{total_stats['symbols_processed']}"
    )
    print(
        f"events CSV生成: {total_stats['events_csv']}, "
        f"Parquet: {total_stats['events_cache']}"
    )
    print(
        f"llm_events CSV: {total_stats['llm_events_csv']}, "
        f"Parquet: {total_stats['llm_events_cache']}"
    )
    print(
        f"llm_news CSV: {total_stats['llm_news_csv']}, "
        f"Parquet: {total_stats['llm_news_cache']}"
    )


if __name__ == "__main__":
    main()
