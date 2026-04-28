"""Windows環境でOHLCV+ティックパイプラインを実行するスクリプト

Usage:
    cd D:\\Projects\\AutoTraderV4
    python scripts/run_pipeline_windows.py
"""

import sys
import time
import logging
from multiprocessing import Pool
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logging.getLogger("autotrader").setLevel(logging.WARNING)

SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
    "CADJPY", "CHFJPY", "EURUSD", "GBPUSD",
]
PARALLEL = 2


def run_ohlcv(sym: str) -> str:
    from autotrader.backtest.data_pipeline import prepare_ohlcv
    t0 = time.time()
    try:
        r = prepare_ohlcv(sym, start_year=2010, end_year=2026, force=True)
        tfs = sum(
            1 for i in r.get("timeframes", {}).values()
            if i.get("status") == "ok"
        )
        return f"{sym}: {tfs}TF完了 ({time.time() - t0:.0f}秒)"
    except Exception as e:
        return f"{sym}: ERROR {e}"


def run_ticks(sym: str) -> str:
    from autotrader.backtest.data_pipeline import prepare_ticks
    t0 = time.time()
    try:
        r = prepare_ticks(sym, force=True)
        ticks = r.get("total_ticks", 0)
        months = r.get("monthly_files", 0)
        return f"{sym}: {ticks:,}ticks {months}months ({time.time() - t0:.0f}秒)"
    except Exception as e:
        return f"{sym}: ERROR {e}"


def run_batches(func, label: str) -> None:
    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")
    t0 = time.time()
    batches = [
        SYMBOLS[i:i + PARALLEL]
        for i in range(0, len(SYMBOLS), PARALLEL)
    ]
    for i, batch in enumerate(batches, 1):
        print(f"\nBatch {i}/{len(batches)}: {', '.join(batch)}", flush=True)
        bt0 = time.time()
        with Pool(len(batch)) as pool:
            for result in pool.imap_unordered(func, batch):
                print(f"  {result}", flush=True)
        print(f"  → {time.time() - bt0:.0f}秒", flush=True)
    print(f"\n{label} 全完了: {time.time() - t0:.0f}秒")


if __name__ == "__main__":
    run_batches(run_ohlcv, "OHLCV インジケータ計算")
    run_batches(run_ticks, "ティック月別キャッシュ")
    print("\n全パイプライン完了")
