"""MT5から過去チャートデータをCSVにエクスポートするスクリプト.

既存のバックテスト用CSVと同じタブ区切りMT5フォーマットで出力する。
インジケータ計算用のlookback期間にも対応。

使用例:
    # USDJPY 2026年1月 全タイムフレーム（自動lookback）
    python scripts/export_mt5_chart.py

    # 通貨ペア・期間を指定
    python scripts/export_mt5_chart.py --symbol EURUSD \
        --start 2026-01-01 --end 2026-02-01

    # 特定タイムフレームのみ
    python scripts/export_mt5_chart.py --timeframes M5 H1 D1

    # lookback月数を手動指定（自動計算より小さい場合は自動値が優先）
    python scripts/export_mt5_chart.py --lookback-months 12
"""

from __future__ import annotations

import argparse
import math
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

# 各時間足の1営業日あたりの平均バー数（Forex 24h市場）
_BARS_PER_TRADING_DAY: dict[str, float] = {
    "M1": 1440.0, "M2": 720.0, "M3": 480.0,
    "M4": 360.0, "M5": 288.0, "M6": 240.0,
    "M10": 144.0, "M12": 120.0, "M15": 96.0,
    "M20": 72.0, "M30": 48.0,
    "H1": 24.0, "H2": 12.0, "H3": 8.0,
    "H4": 6.0, "H6": 4.0, "H8": 3.0, "H12": 2.0,
    "D1": 1.0, "W1": 0.2, "Monthly": 1.0 / 22.0,
}

# インジケータの最長周期（SMA200）
_MAX_INDICATOR_PERIOD = 200

# 1ヶ月あたりの平均営業日数
_TRADING_DAYS_PER_MONTH = 22


def _min_lookback_months(
    timeframe: str,
    max_period: int = _MAX_INDICATOR_PERIOD,
    buffer: float = 1.2,
) -> int:
    """インジケータ計算に必要な最小lookback月数を算出する。

    SMA200等の長周期インジケータが対象期間の初日から
    有効値を持てるよう、十分なバー数を確保する。

    Args:
        timeframe: タイムフレーム文字列（例: M5, D1）
        max_period: インジケータの最長周期（デフォルト: 200）
        buffer: 安全マージン倍率（デフォルト: 1.2 = 20%余裕）

    Returns:
        必要な最小lookback月数（1以上）
    """
    bars_per_day = _BARS_PER_TRADING_DAY.get(timeframe, 1.0)
    bars_per_month = bars_per_day * _TRADING_DAYS_PER_MONTH
    months_needed = (max_period * buffer) / bars_per_month
    return max(1, math.ceil(months_needed))


def connect_mt5() -> None:
    """MT5に接続する。失敗時はエラー終了。"""
    if not mt5.initialize():
        print(
            f"MT5初期化失敗: {mt5.last_error()}", file=sys.stderr,
        )
        sys.exit(1)
    info = mt5.terminal_info()
    if info is not None:
        print(f"MT5接続完了: {info.name} (build {info.build})")


def _subtract_months(dt: datetime, months: int) -> datetime:
    """日時からNヶ月遡った月初を返す。"""
    m = dt.month - months
    y = dt.year
    while m <= 0:
        m += 12
        y -= 1
    return dt.replace(year=y, month=m, day=1)


def fetch_rates(
    symbol: str,
    timeframe_name: str,
    start: datetime,
    end: datetime,
    min_start: datetime | None = None,
) -> pd.DataFrame | None:
    """MT5から指定期間のレートを取得する。

    小さいタイムフレームでバー数上限に引っかかる場合、
    lookback期間を段階的に短縮してリトライする。
    ただし min_start（インジケータ計算に必要な最小開始日）
    より前には短縮しない。

    Args:
        symbol: 通貨ペア名（例: USDJPY）
        timeframe_name: タイムフレーム文字列（例: M5, H1）
        start: 取得開始日時（UTC、lookback含む）
        end: 取得終了日時（UTC）
        min_start: インジケータ計算に必要な最小開始日。
            フォールバック時にこの日付より後には短縮しない。

    Returns:
        取得したDataFrame。データなしの場合はNone。
    """
    tf_const = MT5_TIMEFRAMES.get(timeframe_name)
    if tf_const is None:
        print(
            f"  不明なタイムフレーム: {timeframe_name}",
            file=sys.stderr,
        )
        return None

    if min_start is None:
        min_start = start

    # lookbackを段階的に短縮する候補リスト
    # min_start を下限としてフィルタリング
    fallback_starts = [start]
    for months_back in [4, 3, 2, 1]:
        fb = _subtract_months(end, months_back)
        if fb > start and fb <= min_start:
            # min_start以前なら候補に追加
            fallback_starts.append(fb)
        elif fb > start and fb > min_start:
            # min_startより後は追加しない（lookback不足）
            pass

    # min_startが明示的にstartより後なら最終候補として追加
    if min_start > start and min_start not in fallback_starts:
        fallback_starts.append(min_start)

    for attempt_start in fallback_starts:
        rates = mt5.copy_rates_range(
            symbol, tf_const, attempt_start, end,
        )
        if rates is not None and len(rates) > 0:
            if attempt_start != start:
                print(
                    f"(lookback短縮: "
                    f"{attempt_start:%Y-%m-%d}~) ",
                    end="",
                )
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(
                df["time"], unit="s", utc=True,
            )
            return df

    err = mt5.last_error()
    print(f"  {timeframe_name}: データなし (error={err})")
    return None


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
        help=(
            "インジケータ計算用lookback月数（デフォルト: 6）。"
            "各TFの最小必要量より小さい場合は自動で増加。"
        ),
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

    user_lookback = args.lookback_months
    timeframes = args.timeframes or DEFAULT_TIMEFRAMES
    symbol = args.symbol

    # 出力先
    project_root = Path(__file__).resolve().parent.parent
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = project_root / "data" / symbol
    output_dir.mkdir(parents=True, exist_ok=True)

    # 各TFの必要lookbackを表示
    print(f"通貨ペア:   {symbol}")
    print(
        f"対象期間:   "
        f"{target_start:%Y-%m-%d} ~ {target_end:%Y-%m-%d}",
    )
    print(f"タイムフレーム: {', '.join(timeframes)}")
    print(f"出力先:     {output_dir}")
    print()

    # 時間足ごとのlookback計画を表示
    print("lookback計画（SMA200基準）:")
    for tf in timeframes:
        min_lb = _min_lookback_months(tf)
        effective = max(user_lookback, min_lb)
        start_dt = _subtract_months(target_start, effective)
        marker = ""
        if effective > user_lookback:
            marker = f" (自動増加: {user_lookback}→{effective})"
        print(
            f"  {tf:>7s}: {effective:>2d}ヶ月 "
            f"→ {start_dt:%Y-%m-%d}~{marker}",
        )
    print()

    # MT5接続
    connect_mt5()

    # シンボル存在確認
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(
            f"シンボル '{symbol}' が見つかりません",
            file=sys.stderr,
        )
        mt5.shutdown()
        sys.exit(1)
    if not symbol_info.visible:
        mt5.symbol_select(symbol, True)

    # 各タイムフレームでデータ取得・保存
    saved = 0
    for tf in timeframes:
        # TFごとに最適なlookbackを計算
        min_lb = _min_lookback_months(tf)
        effective_lb = max(user_lookback, min_lb)
        actual_start = _subtract_months(
            target_start, effective_lb,
        )
        # インジケータ計算に必要な最小開始日
        min_start = _subtract_months(target_start, min_lb)

        print(
            f"取得中: {symbol} {tf} "
            f"({actual_start:%Y-%m-%d}~) ...",
            end=" ",
        )
        df = fetch_rates(
            symbol, tf, actual_start, target_end, min_start,
        )
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
