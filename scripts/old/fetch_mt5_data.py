"""MT5からチャートデータを取得して既存CSV形式で保存

Windows環境で実行すること（MT5パッケージが必要）。

使い方:
    python scripts/fetch_mt5_data.py --start 2026-04-06 --end 2026-04-09
    python scripts/fetch_mt5_data.py --start 2026-04-06 --end 2026-04-09 --symbols USDJPY EURJPY
    python scripts/fetch_mt5_data.py --start 2026-04-06 --end 2026-04-09 --warmup 500
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5パッケージが必要です。Windows環境で実行してください。")
    sys.exit(1)

# JST = UTC+9
JST = timezone(timedelta(hours=9))

# 有効通貨ペア（max_positions > 0）
DEFAULT_SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
    "CADJPY", "CHFJPY", "EURUSD", "GBPUSD",
]

# バックテストで使用する時間足
# (名前, MT5定数, 1本あたりの分数)
TIMEFRAMES = {
    "M1":  (mt5.TIMEFRAME_M1,  1),
    "M5":  (mt5.TIMEFRAME_M5,  5),
    "M15": (mt5.TIMEFRAME_M15, 15),
    "M30": (mt5.TIMEFRAME_M30, 30),
    "H1":  (mt5.TIMEFRAME_H1,  60),
    "H4":  (mt5.TIMEFRAME_H4,  240),
    "H8":  (mt5.TIMEFRAME_H8,  480),
    "D1":  (mt5.TIMEFRAME_D1,  1440),
}

# デフォルトウォームアップ足数（D1で200本≒10ヶ月、M1/M5で500本）
DEFAULT_WARMUP_BARS = 500

# データ出力先
DATA_DIR = Path(r"D:\Projects\AutoTraderV4_data\data_test")


def _warmup_start(
    utc_start: datetime,
    warmup_bars: int,
    bar_minutes: int,
) -> datetime:
    """ウォームアップ分だけ開始時刻を前倒しする

    FXは週5日稼働のため、必要な分数を営業日換算で計算し
    週末分を加算する。最低でも7日（1週間）は戻す。
    """
    needed_minutes = warmup_bars * bar_minutes
    # 営業日数に換算して週末を加算（5営業日ごとに2日の週末）
    needed_days = needed_minutes / (24 * 60)
    calendar_days = needed_days * 7 / 5  # 営業日→暦日
    # 最低7日は戻す（週末を確実に越えるため）
    calendar_days = max(calendar_days, 7)
    return utc_start - timedelta(days=calendar_days)


def fetch_and_save(
    symbol: str,
    tf_name: str,
    tf_mt5: int,
    bar_minutes: int,
    utc_start: datetime,
    utc_end: datetime,
    warmup_bars: int,
    output_dir: Path,
) -> int:
    """1シンボル×1時間足のデータを取得してCSV保存

    Returns:
        取得した足の数
    """
    # ウォームアップ分を含めた開始時刻
    fetch_start = _warmup_start(utc_start, warmup_bars, bar_minutes)
    rates = mt5.copy_rates_range(symbol, tf_mt5, fetch_start, utc_end)
    if rates is None or len(rates) == 0:
        print(f"  {tf_name}: データなし")
        return 0

    # 出力ディレクトリ
    csv_dir = output_dir / symbol / "chart" / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    # ファイル名: {SYMBOL}_{TF}_{start}_{end}.csv（MT5エクスポート形式）
    first_time = datetime.utcfromtimestamp(rates[0]["time"])
    last_time = datetime.utcfromtimestamp(rates[-1]["time"])
    start_str = first_time.strftime("%Y%m%d%H%M")
    end_str = last_time.strftime("%Y%m%d%H%M")

    # MT5の時間足名をCSVファイル名に合わせる
    tf_csv_name = tf_name
    if tf_name == "D1":
        tf_csv_name = "Daily"

    filename = f"{symbol}_{tf_csv_name}_{start_str}_{end_str}.csv"
    filepath = csv_dir / filename

    # MT5エクスポート形式（タブ区切り）で書き出し
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n")
        for r in rates:
            dt = datetime.utcfromtimestamp(r["time"])
            date_str = dt.strftime("%Y.%m.%d")
            time_str = dt.strftime("%H:%M:%S")
            line = (
                f"{date_str}\t{time_str}\t"
                f"{r['open']}\t{r['high']}\t{r['low']}\t{r['close']}\t"
                f"{r['tick_volume']}\t{r['real_volume']}\t{r['spread']}\n"
            )
            f.write(line)

    print(f"  {tf_name}: {len(rates)}本 → {filepath.name}")
    return len(rates)


def main() -> None:
    parser = argparse.ArgumentParser(description="MT5チャートデータ取得")
    parser.add_argument(
        "--start", required=True,
        help="開始日（日本時間、YYYY-MM-DD）",
    )
    parser.add_argument(
        "--end", required=True,
        help="終了日（日本時間、YYYY-MM-DD）",
    )
    parser.add_argument(
        "--symbols", nargs="*", default=None,
        help="通貨ペア（省略時: 全有効ペア）",
    )
    parser.add_argument(
        "--warmup", type=int, default=DEFAULT_WARMUP_BARS,
        help=f"ウォームアップ足数（デフォルト: {DEFAULT_WARMUP_BARS}）",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help=f"出力先（デフォルト: {DATA_DIR}）",
    )
    args = parser.parse_args()

    symbols = args.symbols or DEFAULT_SYMBOLS
    output_dir = Path(args.output_dir) if args.output_dir else DATA_DIR

    # 日本時間 → UTC変換
    start_jst = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=JST)
    # 終了日は翌日0:00 JSTまで（その日の全データを含む）
    end_jst = (
        datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=JST)
        + timedelta(days=1)
    )
    utc_start = start_jst.astimezone(timezone.utc).replace(tzinfo=None)
    utc_end = end_jst.astimezone(timezone.utc).replace(tzinfo=None)

    warmup_bars = args.warmup

    print(f"期間: {args.start} ~ {args.end} (JST)")
    print(f"UTC : {utc_start} ~ {utc_end}")
    print(f"ウォームアップ: {warmup_bars} 本")
    print(f"通貨ペア: {', '.join(symbols)}")
    print(f"時間足: {', '.join(TIMEFRAMES.keys())}")
    print(f"出力先: {output_dir}")
    print()

    # MT5初期化
    if not mt5.initialize():
        print(f"MT5初期化失敗: {mt5.last_error()}")
        sys.exit(1)

    try:
        total_bars = 0
        for symbol in symbols:
            print(f"[{symbol}]")
            for tf_name, (tf_mt5, bar_minutes) in TIMEFRAMES.items():
                count = fetch_and_save(
                    symbol, tf_name, tf_mt5, bar_minutes,
                    utc_start, utc_end, warmup_bars, output_dir,
                )
                total_bars += count
            print()

        print(f"完了: 合計 {total_bars:,} 本取得")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
