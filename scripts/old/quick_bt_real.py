"""Quick BT vs Real comparison for USDJPY"""
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

import logging
logging.disable(logging.WARNING)

from scripts.compare_bt_real import load_real_trades, load_mt5_data, replay_bot
from autotrader.decision.unified.config import UnifiedBotConfig
import pandas as pd

db = "/mnt/d/Projects/AutoTraderV4/data/autotrader.db"
export = Path("/mnt/d/Projects/AutoTraderV4_data/tmp/mt5_export")
config = UnifiedBotConfig(consensus_threshold=14.0)
real = load_real_trades(db, "2026-04-20", "2026-04-26")

for symbol in ["USDJPY", "EURJPY"]:
    sym_real = real[real["symbol"] == symbol]
    if sym_real.empty:
        continue
    data = load_mt5_data(export, symbol)
    if not data:
        continue
    bt_signals = replay_bot(symbol, data, config)
    print(f"{symbol} TFs={sorted(data.keys())} BT={len(bt_signals)} Real={len(sym_real)}", flush=True)
    for s in bt_signals[:5]:
        print(f"  BT: {s['time'][11:16]} {s['direction']} sc={s['score']:.1f}", flush=True)
    for _, t in sym_real.iterrows():
        ot = t["opened_at"]
        matches = [s for s in bt_signals if abs((pd.Timestamp(s["time"]) - ot).total_seconds()) < 7200 and s["direction"] == t["signal_type"]]
        st = f"MATCH(sc={matches[0]['score']:.1f})" if matches else "NO MATCH"
        print(f"  R: {str(ot)[11:16]} {t['signal_type']} sc={t['entry_own_score']:.1f} pnl={t['profit_loss']:+,.0f} -> {st}", flush=True)
