#!/usr/bin/env python3
"""全データの通貨ペア別csv/cache階層構造生成

チャートデータとファンダメンタルデータを通貨ペア別に整理し、
Parquetキャッシュを生成するスクリプト。

生成される構造:
    data/{SYMBOL}/
      chart/
        csv/
          {SYMBOL}_M1_*.csv     - チャートCSV（移動/コピー）
          {SYMBOL}_M5_*.csv
          ...
        cache/
          {SYMBOL}_M1.parquet   - チャートParquet
          {SYMBOL}_M5.parquet
          ...
      events/
        csv/
          events_YYYY.csv       - 通貨フィルタ済み
        cache/
          events_YYYY.parquet
      llm_events/
        csv/
          llm_events_{SYMBOL}_YYYY.csv
        cache/
          llm_events_{SYMBOL}_YYYY.parquet
      llm_news/
        csv/
          llm_news_{SYMBOL}_YYYY.csv
        cache/
          llm_news_{SYMBOL}_YYYY.parquet

使用例:
    # 全通貨ペア・全データを処理
    uv run python scripts/generate_data_cache.py --force

    # 特定通貨ペアのみ
    uv run python scripts/generate_data_cache.py --symbols USDJPY

    # チャートのみ
    uv run python scripts/generate_data_cache.py --chart-only

    # ファンダメンタルのみ
    uv run python scripts/generate_data_cache.py --fundamental-only

    # チャートCSVを移動せずコピー
    uv run python scripts/generate_data_cache.py --no-move

    # 旧構造を削除
    uv run python scripts/generate_data_cache.py --cleanup
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

import pandas as pd
import polars as pl

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

# チャートCSVファイル名パターン
# 例: USDJPY_M1_201001040000_202512302359.csv
_CHART_CSV_RE = re.compile(r"^([A-Z]{6})_([A-Za-z0-9]+)_\d+_\d+\.csv$")

# タイムフレーム正規化マッピング
_TF_NORMALIZE: dict[str, str] = {
    "Daily": "Daily",
    "Weekly": "Weekly",
    "Monthly": "Monthly",
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

        preset_path = _project_root / "config" / "symbol_presets.yaml"
        if preset_path.exists():
            with open(preset_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            symbols = list(config.get("symbols", {}).keys())
            if symbols:
                return symbols
    except ImportError:
        pass

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
        stem = csv_path.stem
        try:
            year = int(stem.split("_")[1])
            years.append(year)
        except (IndexError, ValueError):
            continue
    return sorted(years)


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
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f}GB"


def _count_dir_size(dir_path: Path) -> int:
    """ディレクトリの合計サイズ

    Args:
        dir_path: ディレクトリパス

    Returns:
        int: 合計バイト数
    """
    if not dir_path.exists():
        return 0
    return sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())


# ============================================================
# A) チャートデータ処理
# ============================================================


def _extract_tf_from_filename(filename: str) -> str | None:
    """チャートCSVファイル名からタイムフレームを抽出

    Args:
        filename: ファイル名（例: USDJPY_M1_201001040000_202512302359.csv）

    Returns:
        str | None: タイムフレーム（例: M1）
    """
    m = _CHART_CSV_RE.match(filename)
    if m:
        return m.group(2)
    return None


def _process_chart(
    symbol: str,
    data_dir: Path,
    force: bool,
    no_move: bool,
) -> dict[str, int]:
    """チャートCSVをchart/csv/に移動/コピーし、cache/にParquet生成

    Args:
        symbol: 通貨ペアシンボル
        data_dir: データベースディレクトリ
        force: 上書きフラグ
        no_move: Trueならコピーモード（元ファイルを残す）

    Returns:
        dict[str, int]: 処理結果統計
    """
    stats = {
        "csv_moved": 0,
        "cache_created": 0,
        "skipped": 0,
        "csv_size": 0,
        "cache_size": 0,
    }

    chart_dir = data_dir / symbol / "chart"
    csv_dir = chart_dir / "csv"
    cache_dir = chart_dir / "cache"

    if not chart_dir.exists():
        return stats

    # chart/ 直下のCSVファイルを検出
    chart_csvs = sorted(
        f
        for f in chart_dir.iterdir()
        if f.is_file()
        and f.suffix == ".csv"
        and _extract_tf_from_filename(f.name) is not None
    )

    if not chart_csvs:
        return stats

    csv_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    for src_csv in chart_csvs:
        tf = _extract_tf_from_filename(src_csv.name)
        if tf is None:
            continue

        dst_csv = csv_dir / src_csv.name
        parquet_name = f"{symbol}_{tf}.parquet"
        dst_parquet = cache_dir / parquet_name

        # csv/ にコピー/移動
        if not dst_csv.exists() or force:
            if no_move:
                shutil.copy2(src_csv, dst_csv)
            else:
                shutil.copy2(src_csv, dst_csv)
                # 元ファイルはそのまま残す
                # （タスク仕様: chart/直下の元ファイルは残す）
            stats["csv_moved"] += 1
        else:
            stats["skipped"] += 1

        # Parquetキャッシュ生成
        if (
            dst_parquet.exists()
            and not force
            and dst_parquet.stat().st_mtime >= dst_csv.stat().st_mtime
        ):
            continue  # 既存キャッシュが新しい

        # MT5形式CSVをPolarsで読み込み
        try:
            _src_for_read = dst_csv if dst_csv.exists() else src_csv
            _t0 = time.time()
            df = pl.read_csv(
                _src_for_read,
                separator="\t",
                has_header=True,
            )

            # カラム名正規化
            rename_map: dict[str, str] = {}
            for col in df.columns:
                cl = col.strip("<>").lower()
                if cl in ("date",):
                    rename_map[col] = "date"
                elif cl in ("time",):
                    rename_map[col] = "time_col"
                elif cl in ("open",):
                    rename_map[col] = "open"
                elif cl in ("high",):
                    rename_map[col] = "high"
                elif cl in ("low",):
                    rename_map[col] = "low"
                elif cl in ("close",):
                    rename_map[col] = "close"
                elif cl in ("tickvol", "tick_volume"):
                    rename_map[col] = "tick_volume"
                elif cl in ("vol", "volume", "real_volume"):
                    rename_map[col] = "volume"
                elif cl in ("spread",):
                    rename_map[col] = "spread"

            df = df.rename(rename_map)

            # date + time_col → time カラム結合
            if "date" in df.columns and "time_col" in df.columns:
                df = df.with_columns(
                    pl.concat_str(
                        [
                            pl.col("date"),
                            pl.lit(" "),
                            pl.col("time_col"),
                        ]
                    )
                    .str.strptime(
                        pl.Datetime,
                        "%Y.%m.%d %H:%M:%S",
                    )
                    .alias("time")
                )
                df = df.drop(["date", "time_col"])
            elif "date" in df.columns:
                df = df.with_columns(
                    pl.col("date")
                    .str.strptime(
                        pl.Datetime,
                        "%Y.%m.%d",
                    )
                    .alias("time")
                )
                df = df.drop(["date"])

            # time列を先頭に
            cols = ["time"] + [c for c in df.columns if c != "time"]
            df = df.select(cols)

            df.write_parquet(dst_parquet)
            _elapsed = time.time() - _t0

            stats["cache_created"] += 1
            stats["csv_size"] += _src_for_read.stat().st_size
            stats["cache_size"] += dst_parquet.stat().st_size

            print(
                f"    {tf}: {len(df):,}行 "
                f"CSV {_format_size(_src_for_read.stat().st_size)}"
                f" -> Parquet {_format_size(dst_parquet.stat().st_size)}"
                f" ({_elapsed:.1f}s)"
            )

        except Exception as e:
            print(f"    [ERROR] {tf}: {e}")
            continue

    return stats


# ============================================================
# B) 経済イベント処理
# ============================================================


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
        int: 行数（0なら空、-1ならスキップ）
    """
    if (
        parquet_path.exists()
        and not force
        and parquet_path.stat().st_mtime >= csv_path.stat().st_mtime
    ):
        return -1
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
    data_dir: Path,
    force: bool,
) -> dict[str, int]:
    """events CSVを通貨フィルタ→events/csv/保存→events/cache/Parquet生成

    Args:
        symbol: 通貨ペアシンボル
        years: 処理対象年
        events_src_dir: 元events CSVディレクトリ
        data_dir: データベースディレクトリ
        force: 上書きフラグ

    Returns:
        dict[str, int]: 処理結果統計
    """
    base, quote = _get_currencies(symbol)
    currencies = {base, quote}
    stats = {"csv_created": 0, "cache_created": 0, "skipped": 0}

    csv_dir = data_dir / symbol / "events" / "csv"
    cache_dir = data_dir / symbol / "events" / "cache"

    for year in years:
        src_csv = events_src_dir / f"events_{year}.csv"
        if not src_csv.exists():
            continue

        dst_csv = csv_dir / f"events_{year}.csv"
        dst_parquet = cache_dir / f"events_{year}.parquet"

        # CSVフィルタ＆保存
        if not dst_csv.exists() or force:
            filtered_df = _filter_events_by_currency(
                src_csv,
                currencies,
            )
            if not filtered_df.empty:
                csv_dir.mkdir(parents=True, exist_ok=True)
                filtered_df.to_csv(
                    dst_csv,
                    index=False,
                    encoding="utf-8",
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

    return stats


# ============================================================
# C) LLMイベント処理
# ============================================================


def _process_llm_events(
    symbol: str,
    years: list[int],
    data_dir: Path,
    force: bool,
) -> dict[str, int]:
    """llm_events CSVをllm_events/csv/にコピー→llm_events/cache/にParquet生成

    Args:
        symbol: 通貨ペアシンボル
        years: 処理対象年
        data_dir: データベースディレクトリ
        force: 上書きフラグ

    Returns:
        dict[str, int]: 処理結果統計
    """
    stats = {"csv_copied": 0, "cache_created": 0, "skipped": 0}
    llm_src_dir = data_dir / symbol / "llm_events"
    if not llm_src_dir.exists():
        return stats

    csv_dir = data_dir / symbol / "llm_events" / "csv"
    cache_dir = data_dir / symbol / "llm_events" / "cache"

    for year in years:
        fname = f"llm_events_{symbol}_{year}.csv"
        src_csv = llm_src_dir / fname
        if not src_csv.exists():
            continue

        dst_csv = csv_dir / fname
        dst_parquet = cache_dir / f"llm_events_{symbol}_{year}.parquet"

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


# ============================================================
# D) LLMニュース処理
# ============================================================


def _process_llm_news(
    symbol: str,
    years: list[int],
    data_dir: Path,
    force: bool,
) -> dict[str, int]:
    """llm_news CSVをllm_news/csv/にコピー→llm_news/cache/にParquet生成

    Args:
        symbol: 通貨ペアシンボル
        years: 処理対象年
        data_dir: データベースディレクトリ
        force: 上書きフラグ

    Returns:
        dict[str, int]: 処理結果統計
    """
    stats = {"csv_copied": 0, "cache_created": 0, "skipped": 0}
    news_src_dir = data_dir / symbol / "llm_news"
    if not news_src_dir.exists():
        return stats

    csv_dir = data_dir / symbol / "llm_news" / "csv"
    cache_dir = data_dir / symbol / "llm_news" / "cache"

    for year in years:
        fname = f"llm_news_{symbol}_{year}.csv"
        src_csv = news_src_dir / fname
        if not src_csv.exists():
            continue

        dst_csv = csv_dir / fname
        dst_parquet = cache_dir / f"llm_news_{symbol}_{year}.parquet"

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


# ============================================================
# クリーンアップ
# ============================================================


def _cleanup_old_structure(
    symbol: str,
    data_dir: Path,
) -> list[str]:
    """PR #387/384 の旧構造を削除

    削除対象:
    - data/{SYMBOL}/csv/   （PR #387 のフラット構造）
    - data/{SYMBOL}/cache/ （PR #387 のフラット構造）

    Args:
        symbol: 通貨ペアシンボル
        data_dir: データベースディレクトリ

    Returns:
        list[str]: 削除したディレクトリのリスト
    """
    removed: list[str] = []
    sym_dir = data_dir / symbol

    old_csv = sym_dir / "csv"
    old_cache = sym_dir / "cache"

    for old_dir in [old_csv, old_cache]:
        if old_dir.exists() and old_dir.is_dir():
            shutil.rmtree(old_dir)
            removed.append(str(old_dir))

    return removed


# ============================================================
# メイン
# ============================================================


def main() -> None:
    """メイン処理"""
    parser = argparse.ArgumentParser(
        description=(
            "全データを通貨ペア別csv/cache階層に整理しParquetキャッシュを生成"
        ),
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help=("対象通貨ペア（カンマ区切り）。未指定時は全ペアを処理。"),
    )
    parser.add_argument(
        "--years",
        type=str,
        default=None,
        help=("年範囲（例: 2020-2024）。未指定時はデータに合わせて自動検出。"),
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
        help=("元events CSVディレクトリ（デフォルト: data/fundamental）"),
    )
    parser.add_argument(
        "--chart-only",
        action="store_true",
        help="チャートデータのみ処理",
    )
    parser.add_argument(
        "--fundamental-only",
        action="store_true",
        help="ファンダメンタルデータのみ処理",
    )
    parser.add_argument(
        "--no-move",
        action="store_true",
        help="チャートCSVを移動せずコピー",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="旧構造（csv/ cache/）を削除",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    fund_dir = Path(args.fundamental_dir)
    events_src_dir = fund_dir / "events"
    if not events_src_dir.exists():
        events_src_dir = fund_dir

    # 対象シンボル
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = _get_all_symbols(data_dir)

    # 対象年（ファンダメンタル用）
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
        if not years and not args.chart_only:
            print(f"[WARN] events CSVが見つかりません: {events_src_dir}")
            years = list(range(2010, 2026))

    # 処理モード判定
    do_chart = not args.fundamental_only
    do_fundamental = not args.chart_only

    print("=== データ構造化キャッシュ生成 ===")
    print(f"対象シンボル: {', '.join(symbols)}")
    if do_fundamental and years:
        print(f"対象年: {years[0]}-{years[-1]}")
    print(f"チャート: {'ON' if do_chart else 'OFF'}")
    print(f"ファンダメンタル: {'ON' if do_fundamental else 'OFF'}")
    print(f"上書き: {'ON' if args.force else 'OFF'}")
    if args.cleanup:
        print("クリーンアップ: ON")
    print()

    total_chart_csv_size = 0
    total_chart_cache_size = 0

    for symbol in symbols:
        print(f"--- {symbol} ---")

        # A) チャートデータ
        if do_chart:
            chart_stats = _process_chart(
                symbol,
                data_dir,
                args.force,
                args.no_move,
            )
            total_chart_csv_size += chart_stats["csv_size"]
            total_chart_cache_size += chart_stats["cache_size"]
            if chart_stats["cache_created"] > 0:
                print(
                    f"  chart: {chart_stats['csv_moved']}件移動, "
                    f"{chart_stats['cache_created']}件Parquet生成"
                )
            elif chart_stats["skipped"] > 0:
                print(f"  chart: {chart_stats['skipped']}件スキップ")

        # B) 経済イベント
        if do_fundamental:
            ev_stats = _process_events(
                symbol,
                years,
                events_src_dir,
                data_dir,
                args.force,
            )
            print(
                f"  events:     CSV {ev_stats['csv_created']}件, "
                f"Parquet {ev_stats['cache_created']}件, "
                f"スキップ {ev_stats['skipped']}件"
            )

            # C) LLMイベント
            llm_ev_stats = _process_llm_events(
                symbol,
                years,
                data_dir,
                args.force,
            )
            print(
                f"  llm_events: CSV {llm_ev_stats['csv_copied']}件"
                f", Parquet {llm_ev_stats['cache_created']}件, "
                f"スキップ {llm_ev_stats['skipped']}件"
            )

            # D) LLMニュース
            news_stats = _process_llm_news(
                symbol,
                years,
                data_dir,
                args.force,
            )
            print(
                f"  llm_news:   CSV {news_stats['csv_copied']}件, "
                f"Parquet {news_stats['cache_created']}件, "
                f"スキップ {news_stats['skipped']}件"
            )

        # クリーンアップ
        if args.cleanup:
            removed = _cleanup_old_structure(symbol, data_dir)
            if removed:
                for r in removed:
                    print(f"  [CLEANUP] 削除: {r}")

        # ディレクトリサイズ表示
        sym_dir = data_dir / symbol
        chart_cache_size = _count_dir_size(sym_dir / "chart" / "cache")
        events_size = _count_dir_size(sym_dir / "events")
        llm_events_size = _count_dir_size(sym_dir / "llm_events")
        llm_news_size = _count_dir_size(sym_dir / "llm_news")
        print(
            f"  サイズ: chart/cache "
            f"{_format_size(chart_cache_size)}, "
            f"events {_format_size(events_size)}, "
            f"llm_events {_format_size(llm_events_size)}, "
            f"llm_news {_format_size(llm_news_size)}"
        )

    # サマリー
    print()
    print("=== 処理サマリー ===")
    print(f"処理シンボル数: {len(symbols)}")
    if total_chart_csv_size > 0:
        ratio = total_chart_cache_size / total_chart_csv_size
        print(
            f"チャート圧縮: "
            f"{_format_size(total_chart_csv_size)} CSV -> "
            f"{_format_size(total_chart_cache_size)} Parquet "
            f"({ratio:.1%})"
        )


if __name__ == "__main__":
    main()
