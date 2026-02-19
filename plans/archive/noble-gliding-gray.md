# 改善計画: RANGE×DAY 0.5R部分利確 + SWING stag 90分 + 比較レポート

## Context

現在のベスト設定(PF 3.17, DD 1.46%)の上に2つの改善候補を実装し、3パターン比較で効果検証する。
- **A**: ベースライン（現行デフォルト）
- **B**: RANGE×DAY +0.5R → 20%部分利確 → 残りBE
- **C**: SWING stagnation 120分→90分（MFE<0.15R維持）

比較条件: 2023-2025 / 2010-2022 / コストストレス(2010-2025, spread+1pip, slip×2)

---

## Step 1: PositionManagerConfig に新パラメータ追加

**ファイル**: `src/autotrader/decision/unified/position_manager.py`

### 1a. Config フィールド追加 (L233の後)

```python
# RANGE×DAY 0.5R部分利確（デフォルトOFF=比較用）
range_day_half_r_partial_enabled: bool = False
range_day_half_r_partial_ratio: float = 0.20
range_day_half_r_trigger: float = 0.5
```

### 1b. トラッキングset追加 (`__init__` L267の後)

```python
self._half_r_partial_applied: set[str] = set()
```

---

## Step 2: 0.5R部分利確ロジック実装

**ファイル**: `src/autotrader/decision/unified/position_manager.py`

### 2a. `_check_partial_close()` L718の後（1R部分利確の直後、保険ロジックの前）に新ブロック挿入

```python
# === RANGE×DAY 0.5R部分利確 ===
if (
    is_range_day
    and self.config.range_day_half_r_partial_enabled
    and position.current_r >= self.config.range_day_half_r_trigger
    and pos_id not in self._half_r_partial_applied
    and pos_id not in self._partial_closed_1r
):
    self._half_r_partial_applied.add(pos_id)
    self._early_be_applied.add(pos_id)
    be_price = self._get_be_price(position)
    position.current_sl = be_price
    trigger_r = self.config.range_day_half_r_trigger
    if position.direction == SignalType.BUY:
        trig_price = position.entry_price + trigger_r * position.r_value
    else:
        trig_price = position.entry_price - trigger_r * position.r_value
    return ManagementAction.partial_close(
        ratio=self.config.range_day_half_r_partial_ratio,
        new_sl=be_price,
        reason=f"RANGE×DAY {trigger_r}R部分利確: {position.current_r:.2f}R, BE移動",
        exit_reason=ExitReason.TAKE_PROFIT_EARLY,
        trigger_price=trig_price,
    )
```

### 2b. 保険部分利確との干渉防止

L738付近の保険部分利確条件に追加ガード:
```python
and pos_id not in self._half_r_partial_applied
```

→ 0.5R部分利確が先に発火した場合、1.0Rでの保険部分利確はスキップ。

---

## Step 3: slippage_pips の設定経路追加

**目的**: コストストレステストでslippageを上書き可能にする

### 3a. `BacktestConfig` に `slippage_pips` 追加

**ファイル**: `src/autotrader/backtest/runner.py` L70の後

```python
slippage_pips: float = field(
    default_factory=lambda: DEFAULT_TRADING_PARAMS.slippage_pips
)
```

### 3b. `run_unified()` の SimulatorConfig構築に追加

**ファイル**: `src/autotrader/backtest/runner.py` L717-726

```python
sim_config = SimulatorConfig(
    ...
    slippage_pips=self.config.slippage_pips,  # 追加
    ...
)
```

### 3c. `BacktestServiceConfig` と `create_backtest_config()` にも追加

**ファイル**: `src/autotrader/backtest/service.py`

- `BacktestServiceConfig` に `slippage_pips` フィールド追加 (L44付近)
- `create_backtest_config()` に `slippage_pips=config.slippage_pips` 追加 (L79付近)

---

## Step 4: CLIフラグ追加

**ファイル**: `scripts/run_backtest.py`

### 4a. `parse_args()` に追加

```python
# RANGE×DAY 0.5R部分利確
parser.add_argument(
    "--range-day-half-r-partial",
    action="store_true",
    help="RANGE×DAY 0.5R部分利確を有効化",
)
# spread/slippage上書き
parser.add_argument(
    "--spread", type=float, default=None,
    help="スプレッド上書き（pips）",
)
parser.add_argument(
    "--slippage", type=float, default=None,
    help="スリッページ上書き（pips）",
)
```

### 4b. `run_single_backtest()` のPMConfig構築に追加

```python
range_day_half_r_partial_enabled=args.range_day_half_r_partial,
```

### 4c. BacktestServiceConfigにspread/slippage反映

```python
if args.spread is not None:
    service_config.spread_pips = args.spread
if args.slippage is not None:
    # BacktestConfigのslippage_pipsを上書き
```

---

## Step 5: 比較スクリプト作成

**ファイル**: `scripts/run_comparison.py` (新規)

### 構成

```
3パターン × 3条件 = 9回のバックテスト

パターン:
  A: ベースライン (pm_overrides={})
  B: range_day_half_r_partial_enabled=True
  C: swing_stagnation_exit_minutes=90.0

条件:
  1: 2023-2025 (通常コスト)
  2: 2010-2022 (通常コスト)
  3: 2010-2025 (コストストレス: spread=2.5, slip=1.0)
```

### データ読み込み最適化

- `BacktestRunner` を1回だけ生成、`load_data()` は1回
- 各パターン実行前に `runner.config` の `spread_pips` / `slippage_pips` を差し替え
- `run_unified()` に異なる `pm_config` を渡す

### メトリクス抽出

```python
# yearly_results[i]["breakdown"]["exit_reason"] から
sl_losses = sum(bd["SL_HIT"]["net_profit"])          # SL損失合計
trail_profit = sum(bd["TRAIL_HIT"]["net_profit"])     # TRAIL利益
tp2_profit = sum(bd["TP_2R"]["net_profit"])            # TP2利益
tp_early = sum(bd["TP_EARLY"]["net_profit"])           # TP_EARLY利益（新機能確認用）
```

### 出力フォーマット

```
=== 3パターン比較結果 ===

【2023-2025】
| パターン    | 取引 | 勝率   | PF   | 純利益     | DD    | SL損失     | TRAIL+TP2  | TP_EARLY |
|------------|------|--------|------|-----------|-------|-----------|-----------|----------|
| A:ベースライン | 120  | 65.0%  | 3.10 | +500,000  | 1.5%  | -100,000  | +400,000  | +50,000  |
| B:0.5R部分利確 | 120  | 66.0%  | 3.20 | +520,000  | 1.4%  | -90,000   | +380,000  | +80,000  |
| C:SWING 90分  | 118  | 65.5%  | 3.15 | +510,000  | 1.3%  | -95,000   | +390,000  | +50,000  |

（2010-2022、コストストレスも同形式）
```

### レポート保存

- stdout出力 + `reports/comparison_YYYYMMDD_HHMMSS.txt` に保存

---

## Step 6: テスト

**ファイル**: `tests/unit/decision/unified/test_position_manager.py`

既存 `TestRangeDayInsurance` パターンに倣い、`TestRangeDayHalfRPartial` クラス追加:

1. `test_half_r_fires_at_05r` — 有効時、0.5R到達で20%部分利確+BE
2. `test_half_r_disabled_by_default` — デフォルトOFF時、発火しない
3. `test_half_r_not_for_trend` — TREND×DAYでは発火しない
4. `test_half_r_blocks_insurance_partial` — 0.5R利確後、保険部分利確はスキップ
5. `test_half_r_not_after_1r` — 1R利確済みの場合、0.5Rは発火しない

---

## 修正ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/decision/unified/position_manager.py` | Config追加、0.5Rロジック、保険ガード |
| `src/autotrader/backtest/runner.py` | `BacktestConfig.slippage_pips`追加、SimulatorConfigに伝播 |
| `src/autotrader/backtest/service.py` | `BacktestServiceConfig.slippage_pips`追加 |
| `scripts/run_backtest.py` | CLI: `--range-day-half-r-partial`, `--spread`, `--slippage` |
| `scripts/run_comparison.py` | **新規**: 3パターン×3条件比較スクリプト |
| `tests/unit/decision/unified/test_position_manager.py` | 0.5R部分利確テスト追加 |

---

## 検証手順

1. `pytest tests/unit/decision/unified/test_position_manager.py -v` — 新テスト含む全テスト合格
2. `python scripts/run_comparison.py` — 9回バックテスト実行、比較レポート出力
3. 結果確認: BパターンのTP_EARLYが増加、SL損失が減少していれば成功
4. Cパターン: SWING取引数減少、SL損失減少、PF維持/改善を確認
