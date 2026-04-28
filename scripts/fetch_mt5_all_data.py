"""MT5から全データを一括取得（OHLCV全時間足 + ティックデータ）

Windows環境で実行すること（MetaTrader5パッケージが必要）。

用途:
  - バックテスト用の全時間足OHLCVデータ取得
  - TickEntrySimulator 用のティックデータ取得
  - 経済カレンダーデータ取得

使い方:
    # 全ペア・全時間足 + ティック（2020-2025）
    python scripts/fetch_mt5_all_data.py --start 2020-01-01 --end 2025-12-31

    # 特定ペアのみ
    python scripts/fetch_mt5_all_data.py --start 2024-01-01 --end 2025-12-31 --symbols USDJPY EURJPY

    # OHLCVのみ（ティックなし）
    python scripts/fetch_mt5_all_data.py --start 2020-01-01 --end 2025-12-31 --no-ticks

    # ティックのみ
    python scripts/fetch_mt5_all_data.py --start 2024-01-01 --end 2025-12-31 --ticks-only

    # 出力先指定
    python scripts/fetch_mt5_all_data.py --start 2024-01-01 --end 2025-12-31 --output-dir D:\\Projects\\AutoTraderV4_data\\data
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import MetaTrader5 as mt5
    import pandas as pd
except ImportError:
    print(
        "ERROR: MetaTrader5 と pandas が必要です。\n"
        "Windows環境で pip install MetaTrader5 pandas pyarrow を実行してください。"
    )
    sys.exit(1)

# ============================================================
# 定数
# ============================================================

UTC = timezone.utc
JST = timezone(timedelta(hours=9))

DEFAULT_SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
    "CADJPY", "CHFJPY", "EURUSD", "GBPUSD",
]

# バックテストで使用する全時間足
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

# リトライ設定
MAX_RETRIES = 5
RETRY_WAIT_SEC = 3

# デフォルトウォームアップ足数
DEFAULT_WARMUP_BARS = 500

# デフォルト出力先
DEFAULT_OUTPUT_DIR = Path(r"D:\Projects\AutoTraderV4_data\data")


# ============================================================
# OHLCV取得
# ============================================================

def _warmup_start(
    utc_start: datetime,
    warmup_bars: int,
    bar_minutes: int,
) -> datetime:
    """ウォームアップ分だけ開始時刻を前倒し"""
    needed_minutes = warmup_bars * bar_minutes
    needed_days = needed_minutes / (24 * 60)
    calendar_days = needed_days * 7 / 5
    calendar_days = max(calendar_days, 7)
    return utc_start - timedelta(days=calendar_days)


def fetch_ohlcv(
    symbol: str,
    tf_name: str,
    tf_mt5: int,
    bar_minutes: int,
    utc_start: datetime,
    utc_end: datetime,
    warmup_bars: int,
    output_dir: Path,
) -> int:
    """1シンボル x 1時間足のOHLCVデータを取得してCSV保存

    Returns:
        取得した足の数
    """
    fetch_start = _warmup_start(utc_start, warmup_bars, bar_minutes)

    # MT5はサーバーからデータをダウンロードする時間が必要な場合がある
    rates = None
    for attempt in range(1, MAX_RETRIES + 1):
        rates = mt5.copy_rates_range(symbol, tf_mt5, fetch_start, utc_end)
        if rates is not None and len(rates) > 0:
            break
        if attempt < MAX_RETRIES:
            print(f"    {tf_name}: データ待機中... ({attempt}/{MAX_RETRIES})")
            time.sleep(RETRY_WAIT_SEC)

    if rates is None or len(rates) == 0:
        print(f"    {tf_name}: データなし（{MAX_RETRIES}回リトライ後）")
        return 0

    csv_dir = output_dir / symbol / "chart" / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    first_time = datetime.fromtimestamp(rates[0]["time"], tz=UTC)
    last_time = datetime.fromtimestamp(rates[-1]["time"], tz=UTC)
    start_str = first_time.strftime("%Y%m%d%H%M")
    end_str = last_time.strftime("%Y%m%d%H%M")

    tf_csv_name = "Daily" if tf_name == "D1" else tf_name
    filename = f"{symbol}_{tf_csv_name}_{start_str}_{end_str}.csv"
    filepath = csv_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(
            "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>"
            "\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
        )
        for r in rates:
            dt = datetime.fromtimestamp(r["time"], tz=UTC)
            line = (
                f"{dt:%Y.%m.%d}\t{dt:%H:%M:%S}\t"
                f"{r['open']}\t{r['high']}\t{r['low']}\t{r['close']}\t"
                f"{r['tick_volume']}\t{r['real_volume']}\t{r['spread']}\n"
            )
            f.write(line)

    print(f"    {tf_name}: {len(rates):,}本 → {filepath.name}")
    return len(rates)


# ============================================================
# ティックデータ取得
# ============================================================

def _next_month(dt: datetime) -> datetime:
    """翌月1日 00:00 UTC を返す"""
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1, day=1)
    return dt.replace(month=dt.month + 1, day=1)


def fetch_ticks(
    symbol: str,
    utc_start: datetime,
    utc_end: datetime,
    output_dir: Path,
    chunk_days: int = 30,
) -> int:
    """ティックデータを月単位で取得してParquet保存

    カレンダー月境界で分割し、データがない月が連続したら
    残りの年をスキップして高速化する。

    Args:
        symbol: 通貨ペア
        utc_start: 開始日時（UTC）
        utc_end: 終了日時（UTC）
        output_dir: 出力ルートディレクトリ
        chunk_days: 未使用（後方互換のため残す）

    Returns:
        取得した総ティック数
    """
    tick_dir = output_dir / symbol / "ticks"
    tick_dir.mkdir(parents=True, exist_ok=True)

    total_ticks = 0
    # 月初に揃える
    current = utc_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    file_count = 0
    consecutive_empty = 0
    max_consecutive_empty = 3  # 3ヶ月連続空なら年末までスキップ

    while current < utc_end:
        chunk_end = min(_next_month(current), utc_end)

        ticks = None
        for attempt in range(1, MAX_RETRIES + 1):
            ticks = mt5.copy_ticks_range(
                symbol, current, chunk_end, mt5.COPY_TICKS_ALL,
            )
            if ticks is not None and len(ticks) > 0:
                break
            if attempt < MAX_RETRIES:
                print(
                    f"    ticks {current:%Y-%m}: "
                    f"データ待機中... ({attempt}/{MAX_RETRIES})"
                )
                time.sleep(RETRY_WAIT_SEC)

        if ticks is None or len(ticks) == 0:
            consecutive_empty += 1
            if consecutive_empty >= max_consecutive_empty:
                # 年末までスキップ
                next_year = current.replace(
                    year=current.year + 1, month=1, day=1,
                )
                print(
                    f"    ticks {current:%Y-%m}: データなし "
                    f"({consecutive_empty}ヶ月連続) → {next_year.year}年へスキップ"
                )
                current = next_year
                consecutive_empty = 0
            else:
                print(f"    ticks {current:%Y-%m}: データなし")
                current = chunk_end
            continue

        consecutive_empty = 0

        df = pd.DataFrame(ticks)
        df["timestamp"] = pd.to_datetime(
            df["time_msc"], unit="ms", utc=True,
        )
        df = df[["timestamp", "bid", "ask", "volume", "flags"]]
        df = df.set_index("timestamp")

        # 月別ファイル保存（YYYY_MM形式）
        filename = f"ticks_{current:%Y_%m}.parquet"
        out_path = tick_dir / filename
        df.to_parquet(out_path, engine="pyarrow", compression="snappy")

        n = len(df)
        total_ticks += n
        file_count += 1
        print(f"    ticks {current:%Y-%m}: {n:,} ticks → {out_path.name}")

        current = _next_month(current)

    if file_count > 0:
        print(f"    ティック合計: {total_ticks:,} ({file_count}ファイル)")

    return total_ticks


# ============================================================
# 経済カレンダー取得
# ============================================================

def fetch_calendar(
    utc_start: datetime,
    utc_end: datetime,
    output_dir: Path,
) -> int:
    """MT5経済カレンダーイベントを年別CSVに保存

    Returns:
        取得した総イベント数
    """
    try:
        # MT5 5.0.45以降で calendar_range が使用可能
        events = mt5.calendar_range(utc_start, utc_end)
    except AttributeError:
        print("  カレンダー: MT5バージョンが古いためスキップ")
        return 0

    if events is None or len(events) == 0:
        print("  カレンダー: イベントなし")
        return 0

    cal_dir = output_dir / "fundamental" / "events"
    cal_dir.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(events)
    total = 0

    # 年別に分割保存
    if "time" in df.columns:
        df["event_time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    elif "time_msc" in df.columns:
        df["event_time"] = pd.to_datetime(
            df["time_msc"], unit="ms", utc=True,
        )

    for year, year_df in df.groupby(df["event_time"].dt.year):
        out_path = cal_dir / f"events_{year}.csv"
        year_df.to_csv(out_path, index=False, encoding="utf-8")
        n = len(year_df)
        total += n
        print(f"    {year}: {n:,}件 → {out_path.name}")

    return total


# ============================================================
# メイン
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MT5から全データを一括取得（OHLCV + ティック + カレンダー）"
    )
    parser.add_argument(
        "--start", required=True,
        help="開始日（YYYY-MM-DD）",
    )
    parser.add_argument(
        "--end", required=True,
        help="終了日（YYYY-MM-DD）",
    )
    parser.add_argument(
        "--symbols", nargs="*", default=None,
        help=f"通貨ペア（省略時: {', '.join(DEFAULT_SYMBOLS)}）",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help=f"出力先（デフォルト: {DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument(
        "--warmup", type=int, default=DEFAULT_WARMUP_BARS,
        help=f"ウォームアップ足数（デフォルト: {DEFAULT_WARMUP_BARS}）",
    )
    parser.add_argument(
        "--no-ticks", action="store_true",
        help="ティックデータをスキップ",
    )
    parser.add_argument(
        "--ticks-only", action="store_true",
        help="ティックデータのみ取得",
    )
    parser.add_argument(
        "--no-calendar", action="store_true",
        help="カレンダーデータをスキップ",
    )
    parser.add_argument(
        "--timeframes", nargs="*", default=None,
        help=f"取得する時間足（省略時: {', '.join(TIMEFRAMES.keys())}）",
    )
    parser.add_argument(
        "--tick-chunk-days", type=int, default=30,
        help="ティックデータ取得のチャンクサイズ（日数、デフォルト: 30）",
    )
    args = parser.parse_args()

    symbols = args.symbols or DEFAULT_SYMBOLS
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    utc_start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    utc_end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    # 終了日は翌日0:00（その日の全データを含む）
    utc_end = utc_end + timedelta(days=1)

    tfs = args.timeframes or list(TIMEFRAMES.keys())
    do_ohlcv = not args.ticks_only
    do_ticks = not args.no_ticks
    do_calendar = not args.no_calendar and not args.ticks_only

    # MT5初期化
    print("=" * 60)
    print("MT5データ一括取得")
    print("=" * 60)
    print(f"期間:     {args.start} → {args.end}")
    print(f"ペア:     {', '.join(symbols)}")
    print(f"時間足:   {', '.join(tfs)}")
    print(f"ティック: {'ON' if do_ticks else 'OFF'}")
    print(f"カレンダ: {'ON' if do_calendar else 'OFF'}")
    print(f"出力先:   {output_dir}")
    print("=" * 60)
    print()

    print("MT5初期化中...")
    if not mt5.initialize():
        print(f"ERROR: MT5初期化失敗: {mt5.last_error()}")
        sys.exit(1)

    total_start = time.time()
    stats: dict[str, dict] = {}

    try:
        for symbol in symbols:
            print(f"\n{'─' * 50}")
            print(f"■ {symbol}")
            print(f"{'─' * 50}")

            # シンボルをMarket Watchに追加してデータ取得を有効化
            if not mt5.symbol_select(symbol, True):
                print(f"  WARNING: {symbol} の選択に失敗: {mt5.last_error()}")
                continue
            # データダウンロード開始を待つ
            time.sleep(1)

            sym_start = time.time()
            sym_stats = {"ohlcv": {}, "ticks": 0}

            # OHLCV取得
            if do_ohlcv:
                for tf_name in tfs:
                    if tf_name not in TIMEFRAMES:
                        print(f"    WARNING: 不明な時間足 '{tf_name}' をスキップ")
                        continue
                    tf_mt5, bar_minutes = TIMEFRAMES[tf_name]
                    count = fetch_ohlcv(
                        symbol=symbol,
                        tf_name=tf_name,
                        tf_mt5=tf_mt5,
                        bar_minutes=bar_minutes,
                        utc_start=utc_start,
                        utc_end=utc_end,
                        warmup_bars=args.warmup,
                        output_dir=output_dir,
                    )
                    sym_stats["ohlcv"][tf_name] = count

            # ティックデータ取得
            if do_ticks:
                print()
                tick_count = fetch_ticks(
                    symbol=symbol,
                    utc_start=utc_start,
                    utc_end=utc_end,
                    output_dir=output_dir,
                    chunk_days=args.tick_chunk_days,
                )
                sym_stats["ticks"] = tick_count

            elapsed = time.time() - sym_start
            print(f"  {symbol} 完了: {elapsed:.1f}秒")
            stats[symbol] = sym_stats

        # カレンダー取得（ペア共通）
        if do_calendar:
            print(f"\n{'─' * 50}")
            print("■ 経済カレンダー")
            print(f"{'─' * 50}")
            fetch_calendar(utc_start, utc_end, output_dir)

    finally:
        mt5.shutdown()

    # サマリー
    total_elapsed = time.time() - total_start
    print(f"\n{'=' * 60}")
    print("取得完了サマリー")
    print(f"{'=' * 60}")
    for sym, s in stats.items():
        ohlcv_total = sum(s["ohlcv"].values())
        print(
            f"  {sym}: OHLCV {ohlcv_total:,}本, "
            f"ティック {s['ticks']:,}"
        )
    print(f"\n総所要時間: {total_elapsed:.1f}秒")
    print(f"出力先: {output_dir}")


if __name__ == "__main__":
    main()
