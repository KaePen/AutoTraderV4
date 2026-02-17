# P0-1 速度ベースBE + P0-2 RANGE×DAY stagnation厳格化

## Context

前回の改善でDAY_TRADE×RANGEの0.5R早期BEを完全無効化した（`range_day_early_be_r=1.0`）。
しかし**スパイク反転パターン**（速く0.5Rに到達→反転で全戻し）ではBEが有効。
完全無効化は過剰なので、**速い到達時のみBEを復活**させる。

P0-2は既存stagnation exit（120分+MFE<0.2R）をRANGE×DAYだけ厳格化（60分+MFE<0.3R）し、
SL/STAGNATION負けを早期撤退で軽減する。

---

## Step 1: PositionManagerConfig拡張

**`src/autotrader/decision/unified/position_manager.py`** 行209の後に追加:

```python
# P0-1: 速度ベースBE（RANGE×DAY）
range_day_fast_be_enabled: bool = True
range_day_fast_be_minutes: float = 90.0
# P0-2: RANGE×DAY stagnation厳格化
range_day_stagnation_enabled: bool = True
range_day_stagnation_minutes: float = 60.0
range_day_stagnation_min_mfe_r: float = 0.3
```

---

## Step 2: `_check_partial_close`にcurrent_time追加 + fast-beロジック

**`src/autotrader/decision/unified/position_manager.py`**

### 2-A. シグネチャ変更（行537）
```python
def _check_partial_close(self, position, current_price, current_time):
```

### 2-B. evaluate()の呼び出し変更（行330付近）
```python
action = self._check_partial_close(
    position, current_price, current_time,
)
```

### 2-C. 早期BEセクション変更（行626-629）

現在:
```python
effective_be_r = self.config.early_breakeven_r
if is_range_day and self.config.range_day_be_disabled:
    effective_be_r = self.config.range_day_early_be_r
```

変更後:
```python
effective_be_r = self.config.early_breakeven_r  # 0.5
if is_range_day and self.config.range_day_be_disabled:
    if self.config.range_day_fast_be_enabled:
        elapsed_min = (
            (current_time - position.entry_time)
            .total_seconds() / 60
        )
        if elapsed_min <= self.config.range_day_fast_be_minutes:
            # 速い到達→通常の0.5R BEを適用
            effective_be_r = self.config.early_breakeven_r
        else:
            # ゆっくり到達→1Rまで待つ
            effective_be_r = self.config.range_day_early_be_r
    else:
        effective_be_r = self.config.range_day_early_be_r
```

**ロジック**: elapsed<=90分なら0.5R BE発動、>90分なら1Rまで待機。

---

## Step 3: `_check_stagnation_exit`にRANGE×DAY分岐追加

**`src/autotrader/decision/unified/position_manager.py`** 行451-476

既存ロジックの`stagnation_exit_minutes`/`stagnation_min_mfe_r`の前にregime判定を挿入:

```python
is_range_day = (
    getattr(position.plan, "regime", None) == "RANGE"
    and position.plan.mode == TradingStrategyMode.DAY_TRADE
)
if is_range_day and self.config.range_day_stagnation_enabled:
    stag_minutes = self.config.range_day_stagnation_minutes   # 60
    stag_mfe = self.config.range_day_stagnation_min_mfe_r     # 0.3
else:
    stag_minutes = self.config.stagnation_exit_minutes         # 120
    stag_mfe = self.config.stagnation_min_mfe_r                # 0.2
```

以降の条件で`stag_minutes`/`stag_mfe`を使用。

---

## Step 4: CLIフラグ + pm_config構築

**`scripts/run_backtest.py`**

### 4-A. argparse追加（`--early-partial-close`の前）
```
--no-fast-be           速度ベースBE無効化
--fast-be-minutes 90   速い到達の時間閾値
--no-range-stag        RANGE×DAY stagnation厳格化無効化
--range-stag-minutes 60  stagnation時間
--range-stag-mfe 0.3   stagnation MFE閾値
```

### 4-B. pm_config構築に追加
```python
range_day_fast_be_enabled=not args.no_fast_be,
range_day_fast_be_minutes=args.fast_be_minutes,
range_day_stagnation_enabled=not args.no_range_stag,
range_day_stagnation_minutes=args.range_stag_minutes,
range_day_stagnation_min_mfe_r=args.range_stag_mfe,
```

---

## Step 5: テスト

**`tests/unit/decision/unified/test_position_manager.py`**

### 5-A. 既存テスト修正
`TestRangeDayBeFix.setup_method`のconfigに`range_day_fast_be_enabled=False`追加。
（fast-beがデフォルトTrueだと、既存テスト`test_range_day_no_early_be_at_05r`が
15分<=90分でBE発火して壊れるため）

### 5-B. 新テスト4件追加

| テスト | 条件 | 期待結果 |
|--------|------|----------|
| `test_fast_be_fires_when_quick` | RANGE×DAY, 30分後0.5R | UPDATE_SL（BE発火） |
| `test_slow_be_does_not_fire` | RANGE×DAY, 120分後0.5R | HOLD（BE不発火） |
| `test_range_day_strict_stagnation` | RANGE×DAY, 65分MFE<0.3R | FULL_CLOSE(STAGNATION) |
| `test_trend_day_normal_stagnation` | TREND×DAY, 65分MFE<0.3R | HOLD（120分閾値未到達） |

---

## 変更ファイル一覧

| ファイル | 変更内容 |
|----------|----------|
| `src/autotrader/decision/unified/position_manager.py` | Config+5フィールド, `_check_partial_close`にcurrent_time+fast-be, `_check_stagnation_exit`にregime分岐 |
| `scripts/run_backtest.py` | CLIフラグ5つ + pm_config構築更新 |
| `tests/unit/decision/unified/test_position_manager.py` | setup_method修正 + 4テスト追加 |

## 検証

1. `.venv/bin/pytest tests/unit/decision/unified/test_position_manager.py -v` で全テスト合格
2. `python scripts/run_backtest.py 2020-2024` で新ロジック実行
3. `python scripts/run_backtest.py 2020-2024 --no-fast-be --no-range-stag` で従来比較
4. DAY_TRADE×RANGEサマリーでBE_HIT/STAGNATION件数と損益の変化を確認
