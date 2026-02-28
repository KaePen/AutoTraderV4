# Implementation Plan: Phase 2b EVENT Fundamental Integration Enable

## Overview

Phase 2b code is fully implemented but disabled behind three feature flags. This plan
enables it with a critical fix for LLM processing lag (look-ahead bias prevention),
adds CLI flags, fixes the event LLM CSV path discovery, and validates through
multi-year backtests. The goal is to go from flags=OFF to flags=ON with proven
backtest results showing no degradation.

## Requirements

1. **LLM Processing Lag Fix**: In `_synthesize_event_llm_context()`, zero out
   `surprise_score` and `direction_bias` for events where `elapsed < lag_seconds`
   to prevent look-ahead bias.
2. **CLI Flags**: Add `--fundamental-phase2b` (or granular per-feature flags)
   to `run_backtest.py` that set the three feature flags.
3. **Event LLM CSV Path Fix**: `run_backtest.py` looks for CSVs in
   `data/fundamental/llm_events_SYMBOL_YYYY.csv` but actual files are in
   `data/fundamental/llm_events/llm_events_SYMBOL_YYYY.csv`.
4. **2023 Missing Data**: Handle gracefully (2023 has no event LLM data).
5. **Backtest Validation**: Compare baseline (flags OFF) vs Phase 2b (flags ON)
   across 2020-2025 (6 years).
6. **No Changes to 2026 Data**: Pure OOS, never touch.

## Architecture Changes

### File: `autotrader/adapters/fundamental/backtest_provider.py`
- Add `post_event_lag_seconds` parameter to `BacktestFundamentalProvider.__init__()`
- Modify `_synthesize_event_llm_context()` to zero out post-event fields
  for events where elapsed < lag_seconds
- Add `post_event_lag_seconds` to `BacktestFundamentalProvider` config

### File: `autotrader/decision/unified/config.py`
- Add `fundamental_post_event_lag_seconds: int = 30` to `UnifiedBotConfig`

### File: `autotrader/backtest/runner.py`
- Pass `post_event_lag_seconds` from bot config to BacktestFundamentalProvider

### File: `scripts/run_backtest.py`
- Add CLI flags: `--fundamental-phase2b`, `--fundamental-lag`
- Fix event LLM CSV path discovery (search `llm_events/` subdirectory)
- Wire Phase 2b flags into `UnifiedBotConfig` construction

### Test Files
- New: `tests/unit/adapters/fundamental/test_event_lag.py`
- Extend: `tests/unit/adapters/fundamental/test_backtest_provider.py`

---

## Implementation Steps

### Phase 1: LLM Processing Lag Fix (Core Safety)

#### Step 1.1: Add lag parameter to BacktestFundamentalProvider

**File**: `D:\Projects\AutoTraderV4\autotrader\adapters\fundamental\backtest_provider.py`

**Action**: Add `post_event_lag_seconds` parameter to `__init__()`.

```python
# In __init__(), add parameter:
def __init__(
    self,
    event_guard_minutes: int = 30,
    decay_coefficient: float = 2.0,
    post_event_lag_seconds: int = 30,  # NEW
) -> None:
    ...
    self._post_event_lag_seconds = post_event_lag_seconds
```

- **Why**: Live trading has 10-60s lag between event release and LLM output.
  Without this, backtest uses surprise_score/direction_bias at event_time,
  which is look-ahead bias.
- **Dependencies**: None
- **Risk**: Low -- additive parameter with backward-compatible default

#### Step 1.2: Modify _synthesize_event_llm_context() to respect lag

**File**: `D:\Projects\AutoTraderV4\autotrader\adapters\fundamental\backtest_provider.py`
**Lines**: 597-716

**Action**: In the loop over `active` records (line 628-639), for each record
where `elapsed_seconds < post_event_lag_seconds`, treat as "pre-event only":
use `trade_caution_level`, `expected_volatility`, `is_holiday` but zero out
`surprise_score` and `direction_bias`.

The key change is in the weighted average loop (lines 648-666):

```python
# Current code (look-ahead bias):
for rec, infl in active:
    w = infl * _IMPACT_WEIGHT.get(rec.impact, 0.3)
    w_bias += rec.direction_bias * w
    w_surprise += rec.surprise_score * w
    total_w += w

# Fixed code:
for rec, infl in active:
    w = infl * _IMPACT_WEIGHT.get(rec.impact, 0.3)
    elapsed_sec = (
        current_time - rec.event_time
    ).total_seconds()
    if elapsed_sec < self._post_event_lag_seconds:
        # Pre-lag: only use pre-event characteristics
        # surprise_score and direction_bias are NOT yet available
        # (LLM hasn't processed the result yet)
        pass  # contribute 0 to bias/surprise but keep w for other calcs
    else:
        w_bias += rec.direction_bias * w
        w_surprise += rec.surprise_score * w
    total_w += w
```

**Critical detail**: The `total_w` still accumulates even for pre-lag events
because the event IS known to exist (scheduled), and its `trade_caution_level`,
`expected_volatility`, `convergence_hours` are pre-event information. Only
`surprise_score` and `direction_bias` are post-event (require actual data).

However, there is a subtlety: if total_w accumulates but w_bias/w_surprise
don't, then the average will be diluted toward 0. This is actually correct
behavior -- during the lag period, the system should be cautious (high caution
from pre-event fields) but have no directional opinion (bias near 0).

The `event_caution_level`, `volatility_multiplier`, `liquidity_factor`, and
`convergence_progress` calculations (lines 668-703) use `rec.expected_volatility`
and `rec.trade_caution_level` which are PRE-event data and should NOT be
affected by the lag.

- **Why**: This is the critical fix preventing look-ahead bias.
- **Dependencies**: Step 1.1
- **Risk**: Medium -- core algorithm change, needs thorough testing

#### Step 1.3: Add config field to UnifiedBotConfig

**File**: `D:\Projects\AutoTraderV4\autotrader\decision\unified\config.py`
**Line**: After line 230 (fundamental_pm_enabled)

**Action**: Add:
```python
# LLMイベント分析のラグ（秒）: ライブでのLLM処理遅延を模擬
fundamental_post_event_lag_seconds: int = 30
```

- **Why**: Configurable lag allows tuning (15s for fast LLM, 60s for slow)
- **Dependencies**: None
- **Risk**: Low

#### Step 1.4: Wire lag parameter in runner.py

**File**: `D:\Projects\AutoTraderV4\autotrader\backtest\runner.py`
**Line**: ~1027

**Action**: Pass lag to BacktestFundamentalProvider:
```python
fundamental_provider = BacktestFundamentalProvider(
    event_guard_minutes=fundamental_guard_minutes,
    decay_coefficient=(
        _bot_cfg.fundamental_decay_coefficient
    ),
    post_event_lag_seconds=(       # NEW
        _bot_cfg.fundamental_post_event_lag_seconds
    ),
)
```

- **Why**: Connect config to provider
- **Dependencies**: Steps 1.1, 1.3
- **Risk**: Low

---

### Phase 2: CLI Flags and Path Fix

#### Step 2.1: Fix event LLM CSV path discovery

**File**: `D:\Projects\AutoTraderV4\scripts\run_backtest.py`
**Lines**: 1278-1294

**Action**: The current code looks for `{fundamental_dir}/llm_events_{sym}_{yr}.csv`
but the actual files are in `{fundamental_dir}/llm_events/llm_events_{sym}_{yr}.csv`.

Fix by searching both locations:
```python
if args.event_llm:
    _fund_dir = Path(args.fundamental_dir)
    _event_llm_csvs = []
    _sym = args.symbol
    for _yr in range(start_year, end_year + 1):
        # Try subdirectory first, then flat
        _csv = _fund_dir / "llm_events" / f"llm_events_{_sym}_{_yr}.csv"
        if not _csv.exists():
            _csv = _fund_dir / f"llm_events_{_sym}_{_yr}.csv"
        if _csv.exists():
            _event_llm_csvs.append(str(_csv))
    ...
```

- **Why**: Without this fix, `--event-llm` finds zero CSVs and silently falls
  back to the old logic. This is a pre-existing bug.
- **Dependencies**: None
- **Risk**: Low

#### Step 2.2: Add Phase 2b CLI flags

**File**: `D:\Projects\AutoTraderV4\scripts\run_backtest.py`

**Action**: Add after the `--event-llm` argument (line 733):

```python
# Phase 2b フラグ
parser.add_argument(
    "--fundamental-phase2b",
    action="store_true",
    help=(
        "Phase 2b ファンダメンタル統合を有効化。"
        "--fundamental --event-llm も自動的に有効化。"
        "assessor/softguard/pm の全機能を有効にする。"
    ),
)
parser.add_argument(
    "--fundamental-lag",
    type=int,
    default=30,
    help=(
        "LLMイベント分析ラグ（秒）。"
        "ライブでのLLM処理遅延をバックテストで模擬。"
        "（デフォルト: 30）"
    ),
)
```

- **Why**: One-flag enable for full Phase 2b, plus tunable lag
- **Dependencies**: None
- **Risk**: Low

#### Step 2.3: Wire Phase 2b flags into bot_config

**File**: `D:\Projects\AutoTraderV4\scripts\run_backtest.py`
**Lines**: ~1177-1208 (UnifiedBotConfig construction)

**Action**: When `--fundamental-phase2b` is set, add to bot_config:
```python
bot_config = UnifiedBotConfig(
    ...
    # Phase 2b flags
    fundamental_assessor_enabled=args.fundamental_phase2b,
    fundamental_softguard_enabled=args.fundamental_phase2b,
    fundamental_pm_enabled=args.fundamental_phase2b,
    fundamental_post_event_lag_seconds=args.fundamental_lag,
)
```

Also auto-enable `--fundamental` and `--event-llm` when `--fundamental-phase2b`:
```python
# After parse_args(), before CSV list construction:
if args.fundamental_phase2b:
    args.fundamental = True
    args.event_llm = True
```

- **Why**: Single flag enables the full pipeline
- **Dependencies**: Steps 2.1, 2.2, 1.3
- **Risk**: Low

---

### Phase 3: Testing

#### Step 3.1: Unit test for lag behavior

**File**: `D:\Projects\AutoTraderV4\tests\unit\adapters\fundamental\test_event_lag.py`

**Action**: Create new test file with:

1. `test_lag_zeroes_out_bias_during_lag_period`: Create provider with
   `post_event_lag_seconds=60`, add event at T=0. Query at T+30s.
   Verify `direction_bias == 0.0` and `surprise_score == 0.0` but
   `event_caution_level > 0` and `volatility_multiplier > 1.0`.

2. `test_lag_allows_bias_after_lag_period`: Same setup, query at T+61s.
   Verify `direction_bias != 0.0` (reflects actual event data).

3. `test_lag_zero_means_no_lag`: Provider with `post_event_lag_seconds=0`.
   Verify bias is available immediately at event_time.

4. `test_lag_does_not_affect_pre_event_fields`: During lag period,
   caution_level, volatility, holiday should still be computed correctly.

5. `test_lag_mixed_events`: Two events -- one within lag, one past lag.
   Verify only the past-lag event contributes to bias.

- **Why**: Critical correctness test for look-ahead bias prevention
- **Dependencies**: Steps 1.1, 1.2
- **Risk**: Low

#### Step 3.2: Extend existing backtest_provider tests

**File**: `D:\Projects\AutoTraderV4\tests\unit\adapters\fundamental\test_backtest_provider.py`

**Action**: Add test for the new `post_event_lag_seconds` parameter
to verify backward compatibility (default=30 matches expected behavior).

- **Why**: Regression prevention
- **Dependencies**: Step 1.1
- **Risk**: Low

#### Step 3.3: Run existing test suite

**Action**: Run all existing Phase 2b tests to verify nothing breaks:
```bash
uv run pytest tests/unit/decision/unified/test_fundamental_assessor.py -v
uv run pytest tests/unit/decision/unified/test_pm_fundamental.py -v
uv run pytest tests/unit/adapters/fundamental/test_fundamental_memory.py -v
uv run pytest tests/unit/adapters/fundamental/test_backtest_provider.py -v
uv run pytest tests/unit/adapters/fundamental/test_event_lag.py -v
```

- **Why**: Verify no regressions
- **Dependencies**: All Phase 1-2 steps
- **Risk**: Low

---

### Phase 4: Backtest Validation

#### Step 4.1: Baseline Backtest (Flags OFF)

**Action**: Run baseline with event data but Phase 2b OFF:
```bash
uv run python scripts/run_backtest.py \
    --fundamental --event-llm \
    --years 2020-2025 \
    --symbol USDJPY \
    --max-year-workers 5
```

Record: total trades, win rate, net PnL, Sharpe, max drawdown, PF.

- **Why**: Establish baseline for comparison
- **Dependencies**: Step 2.1 (path fix needed)
- **Risk**: None (no code changes, read-only)

#### Step 4.2: Phase 2b Backtest (Flags ON, lag=30)

**Action**:
```bash
uv run python scripts/run_backtest.py \
    --fundamental-phase2b \
    --years 2020-2025 \
    --symbol USDJPY \
    --max-year-workers 5
```

Record same metrics. Compare.

- **Why**: Validate Phase 2b impact
- **Dependencies**: All Phase 1-3 steps
- **Risk**: Medium (may show degradation requiring parameter tuning)

#### Step 4.3: Lag Sensitivity Test

**Action**: Run with different lag values to measure sensitivity:
- `--fundamental-lag 0` (no lag, look-ahead -- for comparison only)
- `--fundamental-lag 15`
- `--fundamental-lag 30` (default)
- `--fundamental-lag 60`

Compare to understand how lag affects results. If lag=0 is significantly
better than lag=30, that indicates the look-ahead bias was substantial
and the fix is necessary.

- **Why**: Validate lag parameter and quantify look-ahead bias magnitude
- **Dependencies**: Step 4.2
- **Risk**: Low

#### Step 4.4: Per-Feature Ablation (Optional)

If Phase 2b shows issues, test individual flags:
1. `fundamental_assessor_enabled=True` only (direction filter + threshold adj)
2. `fundamental_softguard_enabled=True` only (penalty injection)
3. `fundamental_pm_enabled=True` only (trailing SL adjustment)

This requires temporarily modifying the bot_config in the script
or adding granular CLI flags.

- **Why**: Identify which specific feature helps/hurts
- **Dependencies**: Step 4.2
- **Risk**: Low

#### Step 4.5: Full Backtest (2010-2025)

Once parameters are validated on 2020-2025, run full range:
```bash
uv run python scripts/run_backtest.py \
    --fundamental-phase2b \
    --years 2010-2025 \
    --symbol USDJPY \
    --max-year-workers 5
```

Note: 2023 has no event LLM data. The fallback to events-only logic
will handle it, but we should verify there's no crash or anomaly.

- **Why**: Verify robustness across full data range
- **Dependencies**: Step 4.2 confirms no degradation
- **Risk**: Low (2023 graceful degradation should work)

---

## Data Availability Matrix

| Year | events CSV | event LLM CSV | Status |
|------|-----------|---------------|--------|
| 2010 | Yes | Yes | OK |
| 2011 | Yes | Yes | OK |
| 2012 | Yes | Yes | OK |
| 2013 | Yes | Yes | OK |
| 2014 | Yes | Yes | OK |
| 2015 | Yes | Yes | OK |
| 2016 | Yes | Yes | OK |
| 2017 | Yes | Yes | OK |
| 2018 | Yes | Yes | OK |
| 2019 | Yes | Yes | OK |
| 2020 | Yes | Yes | OK |
| 2021 | Yes | Yes | OK |
| 2022 | Yes | Yes | OK |
| 2023 | Yes | **No** | Fallback to events-only |
| 2024 | Yes | Yes | OK |
| 2025 | Yes | Yes | OK |
| 2026 | OOS | OOS | **NEVER TOUCH** |

---

## Risks and Mitigations

### Risk 1: Phase 2b degrades backtest performance
- **Probability**: Medium
- **Impact**: High
- **Mitigation**: Per-feature ablation (Step 4.4) to identify problem.
  Can disable individual features while keeping others. Likely candidates:
  - Direction filter too aggressive -> tune `conviction_boost_max`, `direction_penalty_scale`
  - SoftGuard penalty too harsh -> tune assessor thresholds
- **Fallback**: Keep flags OFF, iterate on parameters

### Risk 2: Lag period creates dead zones where no trading happens
- **Probability**: Low
- **Impact**: Medium
- **Mitigation**: The lag only zeros out direction_bias and surprise_score.
  The pre-event fields (caution, volatility) are still used. If caution_level
  triggers a block, that's the CORRECT behavior (same as Phase 2a guard).
  The lag parameter is tunable.

### Risk 3: 2023 missing data causes unexpected behavior
- **Probability**: Low
- **Impact**: Low
- **Mitigation**: Provider fallback already handles this (falls through to
  `_fallback_context()`). The `records` check at line 484-485 will be empty
  for 2023, triggering fallback. Verify in Step 4.5.

### Risk 4: Fundamental provider pickle serialization with new field
- **Probability**: Low
- **Impact**: Medium (breaks parallel year execution)
- **Mitigation**: `post_event_lag_seconds` is a simple int. No pickle issues
  expected. The existing pickle check (runner.py line 1184-1196) will catch
  any issues early.

---

## Success Criteria

- [ ] Unit tests for lag behavior pass (5 new tests)
- [ ] All existing 900+ tests still pass
- [ ] `--event-llm` correctly discovers CSVs in `llm_events/` subdirectory
- [ ] `--fundamental-phase2b` enables all three flags + auto-enables data loading
- [ ] Baseline backtest (flags OFF) produces same results as before
- [ ] Phase 2b backtest (flags ON, lag=30) shows no significant degradation
  (max -5% PF, max -2% WR tolerated)
- [ ] lag=0 vs lag=30 comparison shows quantifiable look-ahead bias difference
- [ ] 2023 year runs without crash (graceful fallback)
- [ ] Full 2010-2025 run completes successfully

---

## Estimated Timeline

| Phase | Steps | Complexity | Time |
|-------|-------|-----------|------|
| 1. Lag Fix | 1.1-1.4 | Medium | 30-45 min |
| 2. CLI + Path Fix | 2.1-2.3 | Low | 20-30 min |
| 3. Testing | 3.1-3.3 | Low-Medium | 30-45 min |
| 4. Backtest Validation | 4.1-4.5 | Low (but wall-clock) | 2-4 hours (parallel) |

Total coding: ~1.5 hours
Total validation: ~2-4 hours (parallel backtests)

---

## Code Change Summary

| File | Change Type | Lines |
|------|------------|-------|
| `autotrader/adapters/fundamental/backtest_provider.py` | Modify | ~20 lines added |
| `autotrader/decision/unified/config.py` | Modify | ~2 lines added |
| `autotrader/backtest/runner.py` | Modify | ~3 lines changed |
| `scripts/run_backtest.py` | Modify | ~25 lines added |
| `tests/unit/adapters/fundamental/test_event_lag.py` | New | ~120 lines |
| `tests/unit/adapters/fundamental/test_backtest_provider.py` | Modify | ~10 lines |

Total: ~180 lines of changes (conservative, surgical)
