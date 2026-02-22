# AutoTraderV4 Backtest - Complete Tunable Parameters Guide

## Overview
This document lists ALL tunable parameters in the production backtest engine that can affect win rate. Parameters are categorized by their impact area and provided with default values.

## 1. CLI ARGUMENTS (scripts/run_backtest.py)

### Period Configuration
- `--years` (default: "2020-2024"): Year range (format: "2020-2024")
- `--start-date` (default: None): Start date (YYYY-MM-DD, overrides --years)
- `--end-date` (default: None): End date (YYYY-MM-DD, overrides --years)

### Symbol & Timeframe
- `--symbol` (default: "USDJPY"): Currency pair
- `--timeframe` / `-tf` (default: "M15"): Base timeframe (M1, M5, M15, H1, H4, D1)

### Capital & Position Sizing
- `--initial-balance` (default: 1,000,000.0): Starting capital (JPY)
- `--volume` (default: 1.0): Fixed volume per trade
- `--max-positions` (default: 1): Maximum concurrent positions
- `--fixed-lot` (flag): Use fixed lot sizing (disables dynamic sizing)
- `--max-lot-per-trade` (default: 2.0): Max lots per single trade
- `--max-total-exposure` (default: 5.0): Max total open lots

### Risk Management
- `--risk-pct` (default: 0.02): Base risk percentage per trade (2%)
- `--max-risk-pct-abs` (default: 0.03): Absolute max risk percentage (3%)
- `--equity-floor` (default: 0.30): Trading stops below this % of initial equity (30%)
- `--equity-caution` (default: 0.50): Reduce lot size below this % (50%)
- `--tp-sl-ratio` (default: 1.0): TP/SL ratio multiplier
- `--slippage-buffer` (default: 2.0): SL slippage buffer (pips)

### Entry & Signal Filtering
- `--consensus-threshold` (default: 5.5): Score required to enter (0-10 scale)
- `--range-day-bbw` (default: 0.20): Bollinger Band Width threshold for RANGE×DAY
- `--range-day-score-premium` (default: 0.55): RANGE×DAY score boost
- `--no-range-day-score-premium` (flag): Disable RANGE×DAY score premium
- `--weak-hours-premium` (default: 0.5): Score premium for weak hours (JST 18-21)
- `--no-weak-hours` (flag): Disable weak hours filter

### Position Management - Basic
- `--stag-exit-minutes` (default: 120.0): Stagnation exit time (minutes)
- `--stag-min-mfe` (default: 0.15): Min MFE for stagnation exit (R value)
- `--keep-tp-after-partial` (flag): Keep TP after 1R partial close (default: disabled)

### Position Management - RANGE×DAY BE Control
- `--no-range-day-be-fix` (flag): Disable RANGE×DAY BE correction
- `--range-day-be-r` (default: 0.3): BE threshold R value for RANGE×DAY
- `--no-fast-be` (flag): Disable speed-based BE
- `--fast-be-minutes` (default: 90.0): Speed-based BE time threshold (minutes)

### Position Management - RANGE×DAY Stagnation Stages
- `--range-stag` (flag): Enable RANGE×DAY stagnation staging
- `--range-stag-s1-min` (default: 45.0): Stage 1 time threshold (minutes)
- `--range-stag-s1-mfe` (default: 0.05): Stage 1 MFE threshold (R value)
- `--range-stag-s2-min` (default: 60.0): Stage 2 time threshold (minutes)
- `--range-stag-s2-mfe` (default: 0.10): Stage 2 MFE threshold (R value)

### Position Management - Early Partial Close (0.5R)
- `--early-partial-close` (flag): Enable 0.5R small profit taking
- `--early-partial-ratio` (default: 0.25): Ratio to close at 0.5R (25%)

### Position Management - RANGE×DAY Insurance
- `--no-range-insurance` (flag): Disable RANGE×DAY insurance logic
- `--insurance-max-min` (default: 30.0): Time limit for 0.3R reach (minutes)
- `--insurance-sl-r` (default: -0.1): SL offset (R value, negative = tighter)
- `--insurance-partial-ratio` (default: 0.20): Partial close ratio (20%)
- `--insurance-trigger-r` (default: 1.0): Trigger R value (1R)
- `--no-insurance-mfe-block` (flag): Disable MFE >= 0.8R block
- `--insurance-min-hold` (default: 15.0): Min holding time (minutes)

### Position Management - Half-R Partial (0.5R)
- `--no-range-day-half-r-partial` (flag): Disable 0.5R partial for RANGE×DAY

### Data & Execution
- `--data-dir` (default: "data"): Base data directory
- `--spread` (default: None): Override spread (pips)
- `--slippage` (default: None): Override slippage (pips)
- `--use-short-tf` (flag, default: True): Use M1 as base timeframe
- `--no-short-tf` (flag): Use M15 as base instead of M1
- `--sequential` (flag): Force sequential execution (disable year parallelization)
- `--enable-scalping` (flag): Allow M1/M5 entries

### Extended Modes
- `--walk-forward` (flag): Run walk-forward validation
- `--optimize` (flag): Run parameter optimization
- `--fast` (flag): High-speed parallel backtest
- `--quick` (flag): Lightweight backtest (sampling mode)
- `--diagnose` (flag): Diagnostic mode
- `--debug-signal TIME` (format: "2023-03-15 10:30"): Debug signal at specific time
- `-v` / `--verbose` (flag): Detailed logging

---

## 2. UnifiedBotConfig Parameters (src/autotrader/decision/unified/config.py)

### Signal Consolidation
- `consolidator.min_alignment` (default: 3): Minimum aligned timeframes
- `consolidator.confidence_threshold` (default: 0.5): Min confidence for entry
- `consensus_threshold` (default: 4.5): Consensus score threshold (MAIN LEVER)

### Evaluation & Strength
- `min_adx` (default: 20.0): Minimum ADX for trend confirmation
- `require_htf_trend` (default: True): Require higher TF trend alignment
- `tp_sl_ratio` (default: 1.0): TP/SL ratio (legacy, now in PositionSizerConfig)

### Timeframe Configuration
- `timeframes` (default: ["M1","M5","M15","M30","H1","H4","H8","D1"]): Evaluated TFs
- `evaluator_configs[TF].min_score` (default: 5.0): Min score per timeframe
- `evaluator_configs[TF].atr_sl_multiplier` (default: 1.5): SL = ATR × 1.5
- `evaluator_configs[TF].atr_tp_multiplier` (default: 2.0): TP = ATR × 2.0

### RANGE×DAY Filter
- `range_day_bbw_threshold` (default: 0.20): BB Width filter threshold
- `range_day_score_premium` (default: 0.55): Score boost for RANGE×DAY mode

### Weak Hours Filter (JST 18-21 = UTC 9-12)
- `weak_hours_enabled` (default: True): Enable weak hours filtering
- `weak_hours_score_premium` (default: 0.5): Score penalty for weak hours

### Position Sizing & Risk
- `use_dynamic_lot` (default: True): Enable dynamic lot sizing
- `max_lot_per_trade` (default: 2.0): Max lots per trade
- `max_total_exposure_lot` (default: 5.0): Max total open lots
- `base_risk_pct` (default: 0.02): Base risk per trade (2%)
- `max_risk_pct_absolute` (default: 0.03): Absolute max risk (3%)
- `equity_floor_pct` (default: 0.30): Stop trading below 30% equity
- `equity_caution_pct` (default: 0.50): Reduce lots below 50% equity

### Slippage & Execution
- `slippage_buffer_pips` (default: 2.0): SL slippage buffer

### Demo/Test Modes
- `demo_mode` (default: False): Lower thresholds for testing
- `demo_consensus_threshold` (default: 1.5): Very loose threshold for demo
- `demo_max_positions` (default: 1): Max positions in demo
- `demo_cooldown_minutes` (default: 0): Cooldown between trades
- `demo_max_daily_trades` (default: 5): Max daily trades in demo

### Quality-Based Dynamic Positions
- `bonus_max_positions` (default: 0): Extra positions for high-quality signals
- `bonus_score_threshold` (default: 7.0): Score threshold for bonus position

---

## 3. PositionManagerConfig Parameters (src/autotrader/decision/unified/position_manager.py)

### Exit Management
- `stagnation_exit_minutes` (default: 120.0): Exit if no progress in 120 min
- `stagnation_min_mfe_r` (default: 0.15): Min MFE required for stagnation exit

### Breakeven Management
- `breakeven_at_1r` (default: True): Move SL to breakeven at 1R
- `early_breakeven_r` (default: 0.5): R value for early BE move
- `early_breakeven_enabled` (default: True): Enable early BE at 0.5R

### Trailing Stop
- `trailing_start_r` (default: 2.0): Start trailing at 2R profit
- `trailing_atr_multiplier` (default: 2.0): Trail distance = 2 × ATR

### Partial Close (R-based)
- `partial_close_1r_ratio` (default: 0.3): Close 30% at 1R
- `partial_close_2r_ratio` (default: 0.3): Close 30% at 2R

### TP Management
- `disable_tp_after_partial` (default: True): Remove TP after 1R partial
- `signal_rev_close_ratio` (default: 0.5): Close 50% on signal reversal

### RANGE×DAY Breakeven
- `range_day_be_disabled` (default: True): RANGE×DAY BE correction enabled (inverse logic!)
- `range_day_early_be_r` (default: 0.3): RANGE×DAY early BE at 0.3R
- `range_day_fast_be_enabled` (default: True): Speed-based BE active
- `range_day_fast_be_minutes` (default: 90.0): Speed BE if reached in 90 min

### RANGE×DAY Stagnation Staging
- `range_day_stagnation_enabled` (default: False): Enable stagnation stages
- `range_day_stagnation_stage1_minutes` (default: 45.0): Stage 1 timer
- `range_day_stagnation_stage1_min_mfe_r` (default: 0.05): Stage 1 MFE threshold
- `range_day_stagnation_stage2_minutes` (default: 60.0): Stage 2 timer
- `range_day_stagnation_stage2_min_mfe_r` (default: 0.10): Stage 2 MFE threshold

### RANGE×DAY Early Partial (0.5R)
- `early_partial_close_enabled` (default: False): Close 0.5R profits
- `early_partial_close_ratio` (default: 0.25): Ratio to close (25%)

### RANGE×DAY Insurance (Spike Protection)
- `range_day_insurance_enabled` (default: True): Light insurance logic active
- `range_day_insurance_max_minutes` (default: 30.0): Insurance active for 30 min
- `range_day_insurance_sl_offset_r` (default: -0.1): Tighter SL by 0.1R
- `range_day_insurance_partial_ratio` (default: 0.20): Close 20% as insurance
- `insurance_trigger_r` (default: 1.0): Trigger at 1R
- `insurance_block_high_mfe_r` (default: 0.8): Block if MFE > 0.8R
- `insurance_min_holding_minutes` (default: 15.0): Minimum hold time (15 min)

### RANGE×DAY Half-R Partial (0.5R)
- `range_day_half_r_partial_enabled` (default: True): Enable 0.5R partial
- `range_day_half_r_partial_ratio` (default: 0.20): Close 20% at 0.5R
- `range_day_half_r_trigger` (default: 0.5): Trigger at 0.5R

---

## 4. Symbol Presets (config/symbol_presets.yaml)

### EURUSD Preset
- `pip_value`: 10.0 (per lot)
- `spread_pips`: 1.0
- `slippage_pips`: 0.3
- `default_sl_pips`: 15.0
- `default_tp_pips`: 30.0
- `max_positions`: 2
- `bonus_score_threshold`: 7.0
- `base_risk_pct`: 0.02
- `max_lot_per_trade`: 2.0

### USDJPY Preset
- `pip_value`: 100.0 (per lot)
- `spread_pips`: 1.5
- `slippage_pips`: 0.5
- `default_sl_pips`: 20.0
- `default_tp_pips`: 40.0
- `max_positions`: 2
- `bonus_score_threshold`: 7.0
- `base_risk_pct`: 0.02
- `max_lot_per_trade`: 2.0

---

## 5. IMPACT ON WIN RATE (Ranked)

### HIGH IMPACT (Wins/Losses Determined By)
1. **consensus_threshold** (5.5): Score required for entry
   - Higher = fewer, higher-quality entries
   - Lower = more entries, lower quality
   
2. **range_day_bbw_threshold** (0.20): RANGE filter
   - Determines which range trades enter
   
3. **stagnation_exit_minutes** (120.0): Hold time before exit
   - Affects winners that turn into small losses
   
4. **breakeven_at_1r** (True): Move SL to breakeven at 1R
   - Critical for breaking even on mediocre trades

### MEDIUM IMPACT (Risk/Reward Adjustment)
5. **partial_close_1r_ratio** (0.3): Lock in profit at 1R
6. **range_day_insurance_enabled** (True): Spike protection
7. **range_day_fast_be_enabled** (True): Speed-based BE
8. **early_breakeven_enabled** (True): Early BE at 0.5R
9. **tp_sl_ratio** (1.0): Reward/risk ratio

### MEDIUM-LOW IMPACT (Fine-Tuning)
10. **range_day_score_premium** (0.55): RANGE×DAY boost
11. **weak_hours_premium** (0.5): Weak hours penalty
12. **use_dynamic_lot** (True): Position sizing
13. **base_risk_pct** (0.02): Risk per trade
14. **trailing_start_r** (2.0): When to trail

### LOW IMPACT (Edge Cases)
15. `early_partial_close_enabled`: 0.5R taker profits
16. `range_day_stagnation_enabled`: Stagnation staging
17. `signal_rev_close_ratio`: Signal reversal exits
18. Demo mode parameters

---

## 6. RECOMMENDED COMMAND FOR SINGLE BACKTEST

```bash
python scripts/run_backtest.py \
  --symbol EURUSD \
  --years 2023-2024 \
  --initial-balance 1000000 \
  --volume 1.0 \
  --timeframe M15 \
  --consensus-threshold 5.5 \
  --range-day-bbw 0.20 \
  --range-day-score-premium 0.55 \
  --stag-exit-minutes 120.0 \
  --stag-min-mfe 0.15 \
  --keep-tp-after-partial \
  --range-day-be-r 0.3 \
  --fast-be-minutes 90.0 \
  --base-risk-pct 0.02 \
  --tp-sl-ratio 1.0
```

---

## 7. BACKTEST OUTPUT FORMAT

The backtest prints results to console via BacktestResult dataclass:

```python
class BacktestResult:
    trades: int                           # Total trades
    win_rate: float                       # % (0-100)
    non_loss_rate: float                  # % (0-100)
    profit_factor: float                  # Gross Profit / Gross Loss
    net_profit: float                     # JPY
    max_drawdown: float                   # % (0-100)
    sharpe_ratio: float                   # Risk-adjusted return
    annual_return: float                  # % per year
    monthly_results: list[dict]           # Per-month details
    yearly_results: list[dict]            # Per-year details
```

Each trade is logged with:
- Entry/exit price, time
- Direction, volume, PnL
- Win/loss
- Mode, regime, confidence
- MFE/MAE (Maximum Favorable/Adverse Excursion)
- Time held
- Exit reason

---

## 8. COMMON OPTIMIZATION PATTERNS

To increase win rate toward 70%:

1. **Tighten Entry**: Increase `consensus_threshold` from 5.5 → 6.5
2. **Filter Ranges**: Keep `range_day_bbw` tight (0.20)
3. **Exit Early**: Reduce `stagnation_exit_minutes` (120 → 60)
4. **Lock Profit**: Increase `partial_close_1r_ratio` (0.3 → 0.5)
5. **Protect Gains**: Enable `early_partial_close`, `early_breakeven_enabled`
6. **Avoid Weak Hours**: Ensure `weak_hours_enabled` is True
7. **Conservative Lots**: Lower `base_risk_pct` (0.02 → 0.01)

To increase trade count (more data):

1. Lower `consensus_threshold` (5.5 → 4.5)
2. Disable `weak_hours_enabled` (allow JST 18-21)
3. Increase `max_positions` (1 → 2)
4. Set `demo_mode` = True for testing
5. Use M1 base with `--use-short-tf` (default)
