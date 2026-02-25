"""MT5から過去チャートデータをCSVにエクスポートするスクリプト.

既存のバックテスト用CSVと同じタブ区切りMT5フォーマットで出力する。
インジケータ計算用のlookback期間にも対応。

使用例:
    # USDJPY 2026年1月 全タイムフレーム（lookback 6ヶ月）
    python scripts/export_mt5_chart.py

    # 通貨ペア・期間を指定
    python scripts/export_mt5_chart.py --symbol EURUSD \
        --start 2026-01-01 --end 2026-02-01 --lookback-months 3

    # 特定タイムフレームのみ
    python scripts/export_mt5_chart.py --timeframes M5 H1 D1
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

# MT5タイムフレーム定数マッピング
MT5_TIMEFRAMES: dict[str, int] = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10,
    "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "Monthly": mt5.TIMEFRAME_MN1,
}

# 既存データと揃えるデフォルトタイムフレーム一覧
DEFAULT_TIMEFRAMES = [
    "M1", "M2", "M3", "M4", "M5", "M6",
    "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12",
    "D1", "W1", "Monthly",
]


def connect_mt5() -> None:
    """MT5に接続する。失敗時はエラー終了。"""
    if not mt5.initialize():
        print(f"MT5初期化失敗: {mt5.last_error()}", file=sys.stderr)
        sys.exit(1)
    info = mt5.terminal_info()
    if info is not None:
        print(f"MT5接続完了: {info.name} (build {info.build})")


def fetch_rates(
    symbol: str,
    timeframe_name: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame | None:
    """MT5から指定期間のレートを取得する。

    Args:
        symbol: 通貨ペア名（例: USDJPY）
        timeframe_name: タイムフレーム文字列（例: M5, H1）
        start: 取得開始日時（UTC）
        end: 取得終了日時（UTC）

    Returns:
        取得したDataFrame。データなしの場合はNone。
    """
    tf_const = MT5_TIMEFRAMES.get(timeframe_name)
    if tf_const is None:
        print(f"  不明なタイムフレーム: {timeframe_name}", file=sys.stderr)
        return None

    rates = mt5.copy_rates_range(symbol, tf_const, start, end)
    if rates is None or len(rates) == 0:
        err = mt5.last_error()
        print(f"  {timeframe_name}: データなし (error={err})")
        return None

    df = pd.DataFrame(rates)
    # MT5のtime列はUNIXタイムスタンプ（秒）
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def format_mt5_csv(df: pd.DataFrame) -> str:
    """DataFrameを既存CSVと同じタブ区切りMT5形式に変換する。

    出力フォーマット:
        <DATE>\\t<TIME>\\t<OPEN>\\t<HIGH>\\t<LOW>\\t<CLOSE>\\t<TICKVOL>\\t<VOL>\\t<SPREAD>
        2026.01.02\\t00:00:00\\t157.123\\t157.456\\t...

    Args:
        df: fetch_ratesで取得したDataFrame

    Returns:
        タブ区切りCSV文字列
    """
    header = (
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>"
        "\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"
    )
    lines = [header]

    for _, row in df.iterrows():
        ts = row["time"]
        date_str = ts.strftime("%Y.%m.%d")
        time_str = ts.strftime("%H:%M:%S")
        line = (
            f"{date_str}\t{time_str}"
            f"\t{row['open']}\t{row['high']}"
            f"\t{row['low']}\t{row['close']}"
            f"\t{row['tick_volume']}\t{row['real_volume']}"
            f"\t{row['spread']}"
        )
        lines.append(line)

    return "\n".join(lines) + "\n"


def build_filename(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
) -> str:
    """既存の命名規則に従ったファイル名を生成する。

    形式: {SYMBOL}_{TIMEFRAME}_{START}_{END}.csv
    例:   USDJPY_M5_202607010000_202602012355.csv

    Args:
        symbol: 通貨ペア
        timeframe: タイムフレーム文字列
        df: データ（先頭・末尾の日時を使う）

    Returns:
        ファイル名文字列
    """
    first = df["time"].iloc[0].strftime("%Y%m%d%H%M")
    last = df["time"].iloc[-1].strftime("%Y%m%d%H%M")
    return f"{symbol}_{timeframe}_{first}_{last}.csv"


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(
        description="MT5から過去チャートデータをCSVにエクスポート",
    )
    parser.add_argument(
        "--symbol",
        default="USDJPY",
        help="通貨ペア（デフォルト: USDJPY）",
    )
    parser.add_argument(
        "--start",
        default="2026-01-01",
        help="取得開始日 YYYY-MM-DD（デフォルト: 2026-01-01）",
    )
    parser.add_argument(
        "--end",
        default="2026-02-01",
        help="取得終了日 YYYY-MM-DD（デフォルト: 2026-02-01）",
    )
    parser.add_argument(
        "--lookback-months",
        type=int,
        default=6,
        help="インジケータ計算用lookback月数（デフォルト: 6）",
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=None,
        help="取得するタイムフレーム（デフォルト: 全TF）"
        " 例: --timeframes M5 H1 D1",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="出力先ディレクトリ（デフォルト: data/{symbol}/）",
    )
    return parser.parse_args()


def main() -> None:
    """メイン処理."""
    args = parse_args()

    # 日時解析
    target_start = datetime.strptime(
        args.start, "%Y-%m-%d",
    ).replace(tzinfo=timezone.utc)
    target_end = datetime.strptime(
        args.end, "%Y-%m-%d",
    ).replace(tzinfo=timezone.utc)

    # lookback分を遡った実際の取得開始日
    lookback = args.lookback_months
    actual_start_month = target_start.month - lookback
    actual_start_year = target_start.year
    while actual_start_month <= 0:
        actual_start_month += 12
        actual_start_year -= 1
    actual_start = target_start.replace(
        year=actual_start_year,
        month=actual_start_month,
        day=1,
    )

    timeframes = args.timeframes or DEFAULT_TIMEFRAMES
    symbol = args.symbol

    # 出力先
    project_root = Path(__file__).resolve().parent.parent
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = project_root / "data" / symbol
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"通貨ペア:   {symbol}")
    print(f"対象期間:   {target_start:%Y-%m-%d} ~ {target_end:%Y-%m-%d}")
    print(f"lookback:   {lookback}ヶ月 → 実際の取得開始: {actual_start:%Y-%m-%d}")
    print(f"タイムフレーム: {', '.join(timeframes)}")
    print(f"出力先:     {output_dir}")
    print()

    # MT5接続
    connect_mt5()

    # シンボル存在確認
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(f"シンボル '{symbol}' が見つかりません", file=sys.stderr)
        mt5.shutdown()
        sys.exit(1)
    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)

    # 各タイムフレームでデータ取得・保存
    saved = 0
    for tf in timeframes:
        print(f"取得中: {symbol} {tf} ...", end=" ")
        df = fetch_rates(symbol, tf, actual_start, target_end)
        if df is None or df.empty:
            continue

        csv_text = format_mt5_csv(df)
        filename = build_filename(symbol, tf, df)
        filepath = output_dir / filename
        filepath.write_text(csv_text, encoding="utf-8")

        print(f"OK ({len(df):,}本) → {filepath.name}")
        saved += 1

    mt5.shutdown()
    print(f"\n完了: {saved}/{len(timeframes)} ファイル保存")


if __name__ == "__main__":
    main()
