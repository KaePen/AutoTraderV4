"""BacktestRunnerの_calculate_indicatorsを使ってスコア検証"""
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

import logging
logging.disable(logging.WARNING)

import numpy as np
import pandas as pd
from autotrader.backtest.runner import BacktestRunner, BacktestConfig
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.trade_bot import UnifiedTradeBot

export = Path("/mnt/d/Projects/AutoTraderV4_data/tmp/mt5_export")
hist_base = Path("/home/yamas/projects/AutoTraderV4_data/data")

# BacktestRunnerの_calculate_indicatorsを借用
bt_config = BacktestConfig(symbol="USDJPY", timeframe="H1")
runner = BacktestRunner(config=bt_config)

def load_and_calc(symbol: str, tf: str) -> pd.DataFrame | None:
    """既存データ+MT5エクスポートを結合し、ランナーの計算パスで処理"""
    # MT5エクスポート
    src = export / f"{symbol}_{tf}.csv"
    if not src.exists():
        return None
    new_df = pd.read_csv(src)
    new_df["time"] = pd.to_datetime(new_df["time"])

    # 既存長期データ（ウォームアップ）
    hist_cache = hist_base / symbol / "chart" / "cache"
    hist_name = f"{symbol}_{tf}.parquet"
    if tf == "D1":
        hist_name = f"{symbol}_Daily.parquet"
    hist_pq = hist_cache / hist_name
    if hist_pq.exists():
        hist_df = pd.read_parquet(hist_pq)
        if "time" in hist_df.columns:
            hist_df["time"] = pd.to_datetime(hist_df["time"])
        # 直近6ヶ月 + 新データ
        cutoff = new_df["time"].min() - pd.DateOffset(months=6)
        hist_tail = hist_df[hist_df["time"] >= cutoff]
        common = list(set(hist_tail.columns) & set(new_df.columns))
        combined = pd.concat([hist_tail[common], new_df[common]])
        combined = combined.drop_duplicates(subset=["time"], keep="last").sort_values("time")
    else:
        combined = new_df

    # BacktestRunnerの_calculate_indicatorsで計算
    result = runner._calculate_indicators(combined)
    return result

# 全TF読み込み+計算
symbol = "USDJPY"
config = UnifiedBotConfig(consensus_threshold=14.0)
bot = UnifiedTradeBot(config=config)

bot._market_data = {}
bot._time_arrays = {}
bot._current_indices = {}

for tf in ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]:
    df = load_and_calc(symbol, tf)
    if df is not None and len(df) > 50:
        if "time" in df.columns:
            df = df.set_index("time")
        bot._market_data[tf] = df
        bot._time_arrays[tf] = df.index.values.astype("datetime64[ns]")
        bot._current_indices[tf] = 0
        print(f"  {tf}: {len(df)} bars", flush=True)

# テスト: 2026-04-20 16:00 (Real: SELL score=18.4)
test_time = pd.Timestamp("2026-04-20 16:00:00")
print(f"\nTest at {test_time}", flush=True)

# 各TFのデータを確認
for tf in config.timeframes:
    row = bot._get_current_row(tf, test_time)
    if row is not None:
        adx = row.get("adx", "MISS")
        ema12 = row.get("ema_12", "MISS")
        slope = row.get("macd_hist_slope", "MISS")
        print(f"  {tf}: adx={adx} ema12={ema12} slope={slope}", flush=True)
    else:
        print(f"  {tf}: NO DATA", flush=True)

signal = bot.generate_signal(test_time)
print(f"\nSignal: {signal.direction.value}", flush=True)
print(f"Score: {getattr(signal, 'consensus_score', 'N/A')}", flush=True)
print(f"Confidence: {signal.confidence:.2f}", flush=True)
print(f"Rationale: {signal.rationale[:200] if signal.rationale else 'none'}", flush=True)
