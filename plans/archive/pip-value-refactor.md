# Implementation Plan: pip_value/pip_unit Hardcode Elimination

## Overview

pip_unit (`0.01 if "JPY" in symbol else 0.0001`) is duplicated in 21 files across 25+ locations. pip_value has a critical 10x discrepancy between sizer (1000) and simulator (100*1.0=100) for USDJPY. This plan consolidates both values into `SymbolPreset` as the single source of truth, fixes the sizer/simulator discrepancy, and addresses `normalize_atr_by_price()` cross-pair inconsistency.

## Requirements

- R1: pip_unit derived from `get_preset()` or a single helper, eliminating all 25+ inline calculations
- R2: sizer and simulator use the same pip_value for PnL/lot calculation
- R3: quote_ccy_rate added to SymbolPreset (eliminates hardcoded 150.0)
- R4: `normalize_atr_by_price()` produces comparable volatility scores across JPY/USD pairs
- R5: Backward compatibility for JPY pair backtest results after Phase 1-2 (pip_unit only), with intentional changes tracked in Phase 3 (pip_value fix)

## Current State Analysis

### pip_value Flow (the 10x bug)

```
SymbolPreset.pip_value = 100  (USDJPY)

Simulator path:
  _pip_value = preset.pip_value * quote_ccy_rate
             = 100 * 1.0 = 100  (JPY pairs)
  PnL = profit_pips * 100 * volume

Sizer path (trade_bot.py L515-518):
  _sizer_pv = 1000.0 if pip_unit >= 0.001 else 1500.0
  lot = equity * risk_pct / (sl_pips * 1000)

  Result: sizer thinks 1 pip = 1000 JPY/lot (correct)
          simulator thinks 1 pip = 100 JPY/lot (10x too small)
```

The preset pip_value=100 is wrong. Correct: 1 lot = 100,000 units, 1 pip (0.01) = 1000 JPY.
But changing preset pip_value to 1000 would break the simulator PnL by 10x.
The root cause: preset pip_value and sizer pip_value are meant for different units.

**Actual semantics:**
- Preset pip_value=100: "per 0.1 lot" (legacy backtest default_volume=0.1)
- Sizer pip_value=1000: "per 1.0 lot" (standard lot)

This is NOT a bug in current backtest results because `default_volume=0.1` and `pip_value=100` gives `100 * 0.1 = 10 JPY/pip` per 0.01 lot... No, that's wrong too.

Let me re-derive:
- USDJPY: 1 standard lot = 100,000 units
- 1 pip = 0.01 JPY price move
- PnL per pip per lot = 100,000 * 0.01 = 1000 JPY
- Simulator: `profit_pips * pip_value * volume` = `pips * 100 * volume`
- If volume=1.0 lot: PnL = pips * 100 (should be pips * 1000) --> **10x undercount**
- If volume=0.1 lot: PnL = pips * 100 * 0.1 = pips * 10 (should be pips * 100) --> **10x undercount**

The simulator ALWAYS undercounts JPY pair PnL by 10x regardless of volume.

But wait - sizer calculates lot based on pip_value=1000:
`lot = equity * risk% / (sl_pips * 1000)`
Then simulator uses that lot with pip_value=100:
`PnL = pips * 100 * lot`

So the sizer gives 10x MORE lots (because denominator is 10x larger),
and the simulator gives 10x LESS PnL per lot. **These cancel out.**

**Conclusion: The 10x errors cancel each other. The system is internally consistent but both values are wrong. Fixing one without the other breaks everything.**

### pip_unit Locations (25+ occurrences)

All compute `0.01 if "JPY" in symbol else 0.0001`:
1. `backtest/engine.py` (3 places: L463, L1208, L1267)
2. `backtest/runner.py` (2 places: L711, L1202)
3. `backtest/simulator.py` (2 places: L62 default, L114 from_preset)
4. `backtest/parallel.py` (L69)
5. `decision/unified/config.py` (L502 default)
6. `decision/unified/trade_bot.py` (L516 threshold check)
7. `decision/unified/adaptive/trade_record.py` (L57)
8. `decision/unified/risk/position_manager.py` (L255 default)
9. `decision/unified/scoring/timeframe_evaluator.py` (implicit via config)
10. `constraint/filters/volatility_filter.py` (L30)
11. `constraint/filters/filter_manager.py` (L44)
12. `live/engine.py` (2 places: L106, L456)
13. `live/reload.py` (L161)
14. `live/order_service.py` (L83)
15. `live/position_sync.py` (L60)

### quote_ccy_rate Locations (hardcoded 150.0)

1. `backtest/simulator.py` L118
2. `backtest/runner.py` L712, L1203

### normalize_atr_by_price Issue

```python
def normalize_atr_by_price(atr, price, scale_factor=0.02):
    return min((atr / price) / scale_factor, 1.0)
```

- USDJPY: ATR=1.0, price=150 -> 1.0/150/0.02 = 0.333
- EURUSD: ATR=0.001, price=1.05 -> 0.001/1.05/0.02 = 0.048

Both represent ~similar volatility in pip terms (~100 pips), but scores differ by 7x.
The issue: percentage-based normalization is correct for same-currency comparisons but not cross-pair.

## Implementation Steps

### Phase 1: Add pip_unit and quote_ccy_rate to SymbolPreset + Helper Function (LOW RISK)

**Goal**: Create the single source of truth without changing any behavior.

#### Step 1.1: Add fields to SymbolPreset
**File**: `autotrader/config/trading_params.py`

- Add `pip_unit: float = 0.01` to `SymbolPreset`
- Add `quote_ccy_rate: float = 1.0` to `SymbolPreset`
- Add helper function `get_pip_unit(symbol: str) -> float` as a standalone convenience

```python
def get_pip_unit(symbol: str) -> float:
    """シンボルからpip単位を取得（preset未登録でも動作）"""
    return 0.01 if "JPY" in symbol.upper() else 0.0001

def get_quote_ccy_rate(symbol: str) -> float:
    """クォート通貨→JPY変換レート概算"""
    if "JPY" in symbol.upper():
        return 1.0
    return 150.0  # USD/EUR/GBP等の概算
```

#### Step 1.2: Update symbol_presets.yaml
**File**: `config/symbol_presets.yaml`

- Add `pip_unit` and `quote_ccy_rate` to defaults and per-symbol where needed
- defaults: `pip_unit: 0.01`, `quote_ccy_rate: 1.0` (JPY pairs)
- USD pairs: `pip_unit: 0.0001`, `quote_ccy_rate: 150.0`

#### Step 1.3: Unit tests for new fields
**File**: `tests/unit/config/test_trading_params.py` (new or extend)

- Test `get_preset("USDJPY").pip_unit == 0.01`
- Test `get_preset("EURUSD").pip_unit == 0.0001`
- Test `get_preset("USDJPY").quote_ccy_rate == 1.0`
- Test `get_preset("EURUSD").quote_ccy_rate == 150.0`
- Test `get_pip_unit("USDJPY") == 0.01`
- Test fallback for unknown symbols

**Risk**: LOW - additive only, no existing behavior changes
**Verification**: All existing tests pass. New tests pass.

---

### Phase 2: Replace pip_unit Hardcodes with get_preset/get_pip_unit (MEDIUM RISK)

**Goal**: Eliminate all 25+ `0.01 if "JPY" in symbol else 0.0001` patterns.

Strategy: Replace in order of risk (read-only first, then state-affecting).

#### Step 2.1: Live module replacements (4 files)
**Files**:
- `autotrader/live/engine.py` (L106, L456)
- `autotrader/live/reload.py` (L161)
- `autotrader/live/order_service.py` (L83)
- `autotrader/live/position_sync.py` (L60)

Action: Import `get_pip_unit` from `trading_params` and replace inline calculations.
Where `get_pip_value` static methods exist (order_service, position_sync), keep them as wrappers calling the centralized function.

#### Step 2.2: Decision module replacements (5 files)
**Files**:
- `autotrader/decision/unified/config.py` (L502) - change default, add note
- `autotrader/decision/unified/trade_bot.py` (L516) - use config.pip_unit
- `autotrader/decision/unified/adaptive/trade_record.py` (L57)
- `autotrader/decision/unified/risk/position_manager.py` (L255 default)
- `autotrader/decision/unified/scoring/timeframe_evaluator.py` (implicit)

#### Step 2.3: Constraint module replacements (2 files)
**Files**:
- `autotrader/constraint/filters/volatility_filter.py` (L30)
- `autotrader/constraint/filters/filter_manager.py` (L44)

#### Step 2.4: Backtest module replacements (4 files)
**Files**:
- `autotrader/backtest/engine.py` (L463, L1208, L1267)
- `autotrader/backtest/runner.py` (L711, L1202)
- `autotrader/backtest/simulator.py` (L114)
- `autotrader/backtest/parallel.py` (L69)

Action: Use `get_preset(symbol).pip_unit` or `get_pip_unit(symbol)`.

#### Step 2.5: Script replacements
**Files**:
- `scripts/run_backtest.py`
- `scripts/backtest_queue_runner.py`
- `scripts/run_multi_pair_backtest.py`
- `scripts/run_portfolio_backtest.py`

#### Step 2.6: Replace quote_ccy_rate hardcodes
**Files**:
- `autotrader/backtest/simulator.py` L118 (from_preset)
- `autotrader/backtest/runner.py` L712, L1203

Action: Use `get_preset(symbol).quote_ccy_rate` or `get_quote_ccy_rate(symbol)`.

**Risk**: MEDIUM - behavior should be identical since values are the same, but any typo could affect all pairs
**Verification**:
1. Run USDJPY backtest (2020-2025) - results must be **identical** to baseline
2. Run EURUSD backtest (2020-2025) - results must be **identical** to baseline
3. Run full test suite

---

### Phase 3: Fix pip_value Consistency (HIGH RISK - Requires Decision)

**Goal**: Make sizer and simulator use the same pip_value.

#### The Key Decision

The current system has two "wrong" values that cancel out:
- Sizer: pip_value = 1000 (correct per-lot value)
- Simulator: pip_value = 100 * quote_ccy_rate = 100 (10x too small per-lot)

**Option A: Fix simulator pip_value to 1000, keep sizer at 1000**
- Change preset pip_value from 100 to 1000 for JPY pairs
- Change preset pip_value from 10 to 10 for USD pairs (already consistent with quote_ccy_rate=150)
- simulator `_pip_value = config.pip_value * config.quote_ccy_rate`
  - JPY: 1000 * 1.0 = 1000 (correct)
  - USD: 10 * 150 = 1500 (correct)
- **Impact**: All backtest PnL numbers become 10x larger for JPY pairs
- **Mitigation**: Adjust `default_volume` or accept new baseline

**Option B: Fix sizer pip_value to match simulator (100)**
- Change sizer from 1000 to 100 for JPY pairs
- **Impact**: Sizer calculates 10x more lots, actual risk becomes 10x
- **This is dangerous** - would cause massive DD increase

**Option C: Introduce pip_value_per_lot as the canonical value, derive both**
- Add `pip_value_per_lot: float` to SymbolPreset (USDJPY=1000, EURUSD=10)
- Sizer uses `pip_value_per_lot` directly
- Simulator uses `pip_value_per_lot * quote_ccy_rate` (no change for JPY, fixes USD)
- Remove legacy `pip_value` field from preset
- **Impact**: Cleaner model, but requires updating all consumers

**Recommended: Option C** - cleanest long-term, with Option A as a simpler alternative.

#### Step 3.1: Add pip_value_per_lot to SymbolPreset
**File**: `autotrader/config/trading_params.py`

```python
@dataclass(frozen=True)
class SymbolPreset:
    pip_value_per_lot: float = 1000.0  # 1pip/1lotのJPY価値
    pip_value: float = 100.0  # DEPRECATED: 後方互換用
```

**File**: `config/symbol_presets.yaml`

```yaml
defaults:
  pip_value_per_lot: 1000.0  # JPY pairs: 100000 * 0.01 = 1000
symbols:
  EURUSD:
    pip_value_per_lot: 10.0  # 100000 * 0.0001 = $10
```

#### Step 3.2: Update sizer to use pip_value_per_lot
**File**: `autotrader/decision/unified/trade_bot.py`

Replace L515-528:
```python
# Before (hardcoded):
_sizer_pv = 1000.0 if self.config.pip_unit >= 0.001 else 1500.0

# After (from preset):
# pip_value_per_lot はプリセットから取得済み（config経由）
```

**File**: `autotrader/decision/unified/risk/position_sizer.py`

Remove `_SIZER_PIP_VALUE_BY_QUOTE` dict and `_sizer_pip_value()` function.
Use `pip_value_per_lot` from config directly.

**File**: `autotrader/live/engine.py`

Update `_build_sizer_config()` to pass `pip_value` from preset.

#### Step 3.3: Update simulator to use pip_value_per_lot
**File**: `autotrader/backtest/simulator.py`

```python
# Before:
self._pip_value = self.config.pip_value * self.config.quote_ccy_rate
# = 100 * 1.0 = 100 (USDJPY, WRONG)

# After:
self._pip_value = self.config.pip_value_per_lot * self.config.quote_ccy_rate
# = 1000 * 1.0 = 1000 (USDJPY, CORRECT)
```

**CRITICAL**: This changes ALL backtest results. Must re-baseline.

#### Step 3.4: Update live modules
**Files**: `live/order_service.py`, `live/position_sync.py`

Update `get_pip_value()` methods to use preset.

**Risk**: HIGH - Changes all PnL calculations
**Verification**:
1. Run USDJPY backtest - PnL should be 10x previous values
2. Verify risk% actually matches configured risk (not 1/10)
3. Run EURUSD backtest - should be unchanged (already consistent)
4. Full test suite update with new expected values

---

### Phase 4: Fix normalize_atr_by_price Cross-Pair Comparability (MEDIUM RISK)

**Goal**: Make volatility scores comparable across pairs with different pip scales.

#### Step 4.1: Add pip-aware normalization
**File**: `autotrader/calculator/scoring.py`

```python
def normalize_atr_by_price(
    atr: float,
    price: float,
    scale_factor: float = 0.02,
    pip_unit: float | None = None,
) -> float:
    """ATRを正規化（0.0 ~ 1.0）

    pip_unit指定時: ATRをpip数に変換してから正規化（ペア間比較可能）
    pip_unit未指定時: 従来の価格比率ベース（後方互換）
    """
    if price <= 0 or scale_factor <= 0:
        return 0.0

    if pip_unit is not None and pip_unit > 0:
        # ATRをpip数に変換して正規化
        # 50pips を scale_factor=1.0 とするスケール
        atr_pips = atr / pip_unit
        return min(atr_pips / 100.0, 1.0)  # 100pips = 1.0

    # 従来互換
    atr_ratio = atr / price
    return min(atr_ratio / scale_factor, 1.0)
```

#### Step 4.2: Update callers to pass pip_unit
**File**: `autotrader/decision/unified/scoring/strength_calculator.py` L318

```python
# Before:
return normalize_atr_by_price(atr, close)

# After:
return normalize_atr_by_price(atr, close, pip_unit=self._pip_unit)
```

This requires `strength_calculator` to receive pip_unit from config.

#### Step 4.3: Update tests
**File**: `tests/unit/calculator/test_scoring.py`

Add tests for pip-aware normalization:
- USDJPY: ATR=1.0, pip_unit=0.01 -> 100 pips -> 1.0
- EURUSD: ATR=0.001, pip_unit=0.0001 -> 10 pips -> 0.1

**Risk**: MEDIUM - Changes scoring behavior for all pairs
**Verification**:
1. Run USDJPY backtest - compare volatility scores
2. Run EURUSD backtest - compare volatility scores
3. Both should now produce similar ranges for similar market conditions
4. Overall performance comparison

---

## Phase Execution Summary

| Phase | Risk | JPY Impact | USD Impact | Independent |
|-------|------|-----------|-----------|-------------|
| 1: Add fields | LOW | None | None | Yes |
| 2: Replace hardcodes | MEDIUM | None (same values) | None | Requires Phase 1 |
| 3: Fix pip_value | HIGH | 10x PnL change | None | Requires Phase 2 |
| 4: Fix ATR normalize | MEDIUM | Score change | Score change | Requires Phase 1 |

**Recommended execution order**: Phase 1 -> Phase 2 -> Phase 4 -> Phase 3

Phase 3 is deliberately last because it changes all backtest baselines and requires a
strategic decision about risk sizing. Phases 1-2 are pure refactoring with zero behavior change.
Phase 4 can be done before Phase 3 since it's independent.

## Risks & Mitigations

### Risk 1: Phase 3 breaks all JPY backtest baselines
- **Impact**: All JPY pair PnL numbers become 10x larger
- **Mitigation**: This is actually CORRECT behavior. Current backtest PnL is 10x underreported.
  The system compensates via 10x oversized lots. After fix, lots will be 10x smaller,
  PnL per lot 10x larger, net result should be identical IF both are fixed simultaneously.
- **Action**: Fix sizer and simulator in the same commit.

### Risk 2: Phase 2 regression from typo/import error
- **Mitigation**: Run full backtest for USDJPY and EURUSD after Phase 2.
  Results must be **bit-for-bit identical** to pre-refactor.

### Risk 3: Phase 4 changes scoring balance
- **Mitigation**: Phase 4 is optional and only matters for multi-pair portfolios.
  For single-pair (current production), normalize_atr_by_price is only compared within
  the same pair, so percentage-based normalization is already correct.
- **Action**: Consider making Phase 4 opt-in via config flag.

### Risk 4: Live trading affected by pip_value change
- **Mitigation**: Live sizer already uses `_sizer_pip_value()` with correct 1000/1500 values.
  Phase 3 should maintain these values via preset. Live position_sync/order_service
  pip_value is only used for display/logging, not trading decisions.

## Testing Strategy

### Phase 1 Tests
- Unit: `test_trading_params.py` - new fields, helper functions
- Integration: All existing tests pass unchanged

### Phase 2 Tests
- Unit: All existing tests pass unchanged
- Integration: USDJPY + EURUSD backtest produce identical results
- Grep: Zero remaining `0.01 if.*JPY.*else 0.0001` patterns in production code

### Phase 3 Tests
- Unit: Update all tests with pip_value expectations
- Integration: USDJPY backtest with `pip_value_per_lot=1000`
- Validation: Verify `lot * pip_value_per_lot * sl_pips = risk_amount` consistently
- Golden test update: New baselines for all pairs

### Phase 4 Tests
- Unit: `test_scoring.py` pip-aware normalization
- Integration: Multi-pair portfolio with comparable volatility scores

## Success Criteria

- [ ] Zero `0.01 if "JPY" in symbol else 0.0001` in production code
- [ ] Zero `quote_ccy_rate = 1.0 if "JPY"` in production code
- [ ] `get_preset(symbol).pip_unit` is the only source of pip_unit
- [ ] Sizer and simulator use the same pip_value source
- [ ] All backtest results reproducible (after re-baselining for Phase 3)
- [ ] Live trading unaffected (or intentionally corrected)
- [ ] `normalize_atr_by_price` produces comparable cross-pair scores (Phase 4)

## Files Changed Summary

### Phase 1 (2 files)
- `autotrader/config/trading_params.py` - add pip_unit, quote_ccy_rate, helpers
- `config/symbol_presets.yaml` - add pip_unit, quote_ccy_rate per symbol

### Phase 2 (17 files)
- `autotrader/live/engine.py`
- `autotrader/live/reload.py`
- `autotrader/live/order_service.py`
- `autotrader/live/position_sync.py`
- `autotrader/decision/unified/config.py`
- `autotrader/decision/unified/trade_bot.py`
- `autotrader/decision/unified/adaptive/trade_record.py`
- `autotrader/decision/unified/risk/position_manager.py`
- `autotrader/constraint/filters/volatility_filter.py`
- `autotrader/constraint/filters/filter_manager.py`
- `autotrader/backtest/engine.py`
- `autotrader/backtest/runner.py`
- `autotrader/backtest/simulator.py`
- `autotrader/backtest/parallel.py`
- `scripts/run_backtest.py`
- `scripts/backtest_queue_runner.py`
- `scripts/run_multi_pair_backtest.py`

### Phase 3 (6 files)
- `autotrader/config/trading_params.py`
- `config/symbol_presets.yaml`
- `autotrader/decision/unified/trade_bot.py`
- `autotrader/decision/unified/risk/position_sizer.py`
- `autotrader/backtest/simulator.py`
- `autotrader/live/engine.py`

### Phase 4 (3 files)
- `autotrader/calculator/scoring.py`
- `autotrader/decision/unified/scoring/strength_calculator.py`
- `tests/unit/calculator/test_scoring.py`
