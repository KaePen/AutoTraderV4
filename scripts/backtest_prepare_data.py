"""データ準備スクリプト

MT5からティックデータ取得 + OHLCVインジケータ事前計算を行い、
Nautilus バックテスト用のデータを準備する。

Usage:
    # ティックデータ取得（Windows MT5環境で実行）
    uv run python scripts/prepare_data.py fetch --symbol USDJPY --start 2026-01-01 --end 2026-03-31

    # インジケータ事前計算（OHLCVキャッシュ → Parquet保存）
    uv run python scripts/prepare_data.py indicators --symbol USDJPY
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from autotrader.backtest.data_loader import DataLoader
from autotrader.calculator.precompute import PrecomputeEngine

logger = logging.getLogger(__name__)

SUPPORTED_SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
    "CADJPY", "CHFJPY", "EURUSD", "GBPUSD",
]

_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]


def _get_data_dir() -> Path:
    from autotrader.config.paths import get_data_dir
    return Path(get_data_dir())


# ---------------------------------------------------------------------------
# fetch: MT5からティックデータ取得
# ---------------------------------------------------------------------------


def cmd_fetch(args: argparse.Namespace) -> None:
    """MT5からティックデータを取得してParquetに保存."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print(
            "ERROR: MetaTrader5パッケージが見つかりません。\n"
            "Windows環境で pip install MetaTrader5 を実行してください。"
        )
        sys.exit(1)

    symbol = args.symbol
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)

    print("MT5初期化中...")
    if not mt5.initialize():
        print(f"ERROR: MT5初期化失敗: {mt5.last_error()}")
        sys.exit(1)

    try:
        print(f"ティックデータ取得: {symbol} {start.date()} → {end.date()}")

        current = start
        all_chunks: list[Path] = []

        while current < end:
            month_end = min(
                current.replace(day=1) + timedelta(days=32),
                end,
            )
            month_end = month_end.replace(day=1)
            if month_end <= current:
                month_end = end

            print(f"  取得中: {current.date()} → {month_end.date()} ...", end=" ")

            ticks = mt5.copy_ticks_range(
                symbol, current, month_end, mt5.COPY_TICKS_ALL,
            )

            if ticks is None or len(ticks) == 0:
                print("データなし")
                current = month_end
                continue

            df = pd.DataFrame(ticks)
            df["timestamp"] = pd.to_datetime(df["time_msc"], unit="ms", utc=True)
            df = df[["timestamp", "bid", "ask", "volume", "flags"]]
            df = df.set_index("timestamp")

            data_dir = _get_data_dir()
            tick_dir = data_dir / symbol / "ticks"
            tick_dir.mkdir(parents=True, exist_ok=True)

            filename = f"ticks_{current.strftime('%Y%m')}.parquet"
            out_path = tick_dir / filename
            df.to_parquet(out_path, engine="pyarrow", compression="snappy")

            print(f"{len(df):,} ticks → {out_path.name}")
            all_chunks.append(out_path)

            current = month_end

        print(f"\n完了: {len(all_chunks)} ファイル保存")

    finally:
        mt5.shutdown()


# ---------------------------------------------------------------------------
# indicators: OHLCVインジケータ事前計算
# ---------------------------------------------------------------------------


def cmd_indicators(args: argparse.Namespace) -> None:
    """全タイムフレームのインジケータを事前計算してParquetに保存."""
    symbol = args.symbol
    data_dir = _get_data_dir()
    symbol_dir = data_dir / symbol

    chart_dir = symbol_dir / "chart"
    base_dir = chart_dir if chart_dir.exists() else symbol_dir

    # 出力先
    out_dir = data_dir / symbol / "indicators"
    out_dir.mkdir(parents=True, exist_ok=True)

    precompute = PrecomputeEngine()
    total_start = time.time()

    print(f"=== インジケータ事前計算: {symbol} ===")
    print(f"データ元: {base_dir}")
    print(f"出力先:   {out_dir}")
    print()

    for tf in _TIMEFRAMES:
        tf_start = time.time()
        print(f"  {tf}: ", end="", flush=True)

        df: pd.DataFrame | None = None

        # 優先1: chart/cache/ Parquet
        cache_pq = base_dir / "cache" / f"{symbol}_{tf}.parquet"
        if cache_pq.exists():
            try:
                df = pd.read_parquet(cache_pq)
                print(f"{len(df):,} bars (cache) → ", end="", flush=True)
            except Exception as e:
                print(f"Parquet読込失敗: {e}")

        # 優先2: CSV
        if df is None:
            pattern = f"{symbol}_{tf}_*.csv"
            csv_files = sorted(base_dir.glob(pattern))
            if not csv_files and base_dir != symbol_dir:
                csv_files = sorted(symbol_dir.glob(pattern))
            if csv_files:
                dfs = [DataLoader.load_mt5_csv(f) for f in csv_files]
                df = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["time"])
                df = df.sort_values("time").reset_index(drop=True)
                print(f"{len(df):,} bars (csv) → ", end="", flush=True)

        if df is None:
            print("データなし (skip)")
            continue

        # インジケータ計算
        df = precompute.compute_technical_indicators(df)

        # Parquet保存
        out_path = out_dir / f"{symbol}_{tf}.parquet"
        df.to_parquet(out_path, engine="pyarrow", compression="snappy")

        elapsed = time.time() - tf_start
        print(f"保存完了 ({elapsed:.1f}s)")

    total_elapsed = time.time() - total_start
    print(f"\n全TF完了: {total_elapsed:.1f}s")
    print(f"出力: {out_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="データ準備（ティック取得 + インジケータ計算）",
    )
    subparsers = parser.add_subparsers(dest="command")

    # fetch
    fetch_p = subparsers.add_parser("fetch", help="MT5からティックデータ取得")
    fetch_p.add_argument("--symbol", default="USDJPY", choices=SUPPORTED_SYMBOLS)
    fetch_p.add_argument("--start", required=True, help="開始日 (YYYY-MM-DD)")
    fetch_p.add_argument("--end", required=True, help="終了日 (YYYY-MM-DD)")

    # indicators
    ind_p = subparsers.add_parser("indicators", help="インジケータ事前計算")
    ind_p.add_argument("--symbol", default="USDJPY", choices=SUPPORTED_SYMBOLS)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    {"fetch": cmd_fetch, "indicators": cmd_indicators}[args.command](args)


if __name__ == "__main__":
    main()
