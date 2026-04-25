"""BacktestRunner で 2026年4月をライブ同一設定で実行"""
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

import logging
logging.disable(logging.CRITICAL)

import pandas as pd
from autotrader.backtest.runner import BacktestRunner, BacktestConfig
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.risk.position_manager import (
    PositionManagerConfig,
)

# ライブと同一設定（trading_defaults.yaml準拠）
bot_config = UnifiedBotConfig(consensus_threshold=14.0)
bt_config = BacktestConfig(symbol="USDJPY", timeframe="H1")

# ライブのPM設定（trading_defaults.yaml pm_config）
pm_config = PositionManagerConfig(
    partial_close_1r_ratio=0.50,
    partial_close_2r_ratio=0.05,
    breakeven_at_1r=True,
    trailing_start_r=0.5,
    trailing_atr_multiplier=2.0,
    early_breakeven_r=0.6,
    early_breakeven_enabled=True,
    signal_rev_close_ratio=0.0,
    range_day_be_disabled=True,
    range_day_early_be_r=0.3,
    range_day_fast_be_enabled=True,
    range_day_fast_be_minutes=90.0,
    stagnation_exit_minutes=90.0,
    stagnation_min_mfe_r=0.10,
    be_cushion_pips=3.0,
    early_profit_guard_enabled=True,
    stag_pretighten_enabled=True,
    trailing_stage2_r=1.2,
    trailing_stage2_atr_multiplier=1.2,
    stagnation_stage1_minutes=60.0,
    stagnation_stage1_mfe_r=0.05,
    stagnation_stage2_minutes=90.0,
    stagnation_stage2_mfe_r=0.10,
    stag_pretighten_pct=0.80,
    stag_pretighten_mfe_r=0.10,
    stag_pretighten_sl_r=-0.05,
    early_profit_guard_min_mfe_r=0.05,
    early_profit_guard_max_r=0.30,
    early_profit_guard_score_diff=1.0,
    early_profit_guard_min_opp_score=4.0,
    early_profit_guard_min_hold_minutes=5.0,
    edge_decay_exit_enabled=True,
    edge_decay_exit_threshold=0.40,
    edge_decay_exit_min_bars=5,
    edge_decay_exit_max_loss_r=-0.3,
    edge_decay_profit_exit_enabled=True,
    edge_decay_profit_erosion_threshold=0.60,
    edge_decay_profit_decay_min=0.25,
    edge_decay_stagnation_enabled=True,
    edge_decay_stagnation_threshold=0.35,
    edge_decay_stagnation_multiplier=0.65,
)

runner = BacktestRunner(
    config=bt_config, data_dir="/tmp/bt_vs_real_data"
)

logging.disable(logging.NOTSET)
logging.basicConfig(level=logging.INFO, format="%(message)s")

result = runner.run_unified_monthly(
    start_year=2026,
    end_year=2026,
    config=bot_config,
    use_m1=False,
    pm_config=pm_config,
    max_month_workers=1,
    sequential=True,
    period_start=pd.Timestamp("2026-04-20"),
    period_end=pd.Timestamp("2026-04-24"),
)

print(f"\n{'='*50}", flush=True)
print(f"BT:   trades={result.trades} net={result.net_profit:+,.0f}", flush=True)
print(f"Real: trades=7(USDJPY) net=-714", flush=True)
print(f"{'='*50}", flush=True)
