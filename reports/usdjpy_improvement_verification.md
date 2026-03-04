# USDJPY改善検証レポート

## 実行条件
- シンボル: USDJPY
- 期間: 2020-2025 (6年)
- 時間足: M1, M5, M15, M30, H1, H4, H8, D1
- 初期残高: JPY 1,000,000
- ボリューム: 1.0 lot

## 検証一覧

| # | テスト | 設定 | Profit | vs T0 | WR | PF | DD | Sharpe | 判定 |
|---|--------|------|--------|-------|-----|-----|-----|--------|------|
| T0 | Baseline | 現行デフォルト | +3,016K | — | 60.2% | 2.03 | 1.86% | 5.30 | baseline |
| T1 | Prog.Stag OFF | `--no-progressive-stagnation` | +3,071K | +55K | 60.6% | 2.04 | 1.86% | 5.39 | marginal |
| T2 | Stag緩和 | `--stag-min-mfe 0.10` + stag 180min | +3,149K | +133K | 60.9% | 2.05 | 1.88% | 5.48 | positive |
| T3 | penalty_cap 0.20 | `--penalty-cap 0.20` | +1,869K | **-1,148K** | 60.5% | 2.04 | 1.86% | 4.24 | **REJECT** |
| T4 | Weak Hours OFF | `--no-weak-hours` | +2,989K | -27K | 60.2% | 2.02 | 1.86% | 5.26 | negligible |
| T5 | Off-hours TREND block | `--off-hours-trend-block` | +2,775K | -241K | 60.3% | 2.01 | 1.83% | 5.13 | REJECT |
| T6 | TREND SL min 30 | `--trend-sl-min 30` | +2,780K | -236K | 60.9% | 2.09 | 2.15% | 5.55 | mixed |
| T7 | TREND SL min 40 | `--trend-sl-min 40` | +2,751K | -266K | 61.6% | 2.18 | 2.06% | 5.74 | mixed |
| **T8** | **Stag TREND=90 RANGE=120** | `--stag-trend-minutes 90 --stag-range-minutes 120` | **+3,218K** | **+202K** | **61.5%** | **2.08** | **1.75%** | **5.62** | **BEST単体** |
| T9 | Align penalty 0.55/1.5 | `--high-align-penalty-threshold 0.55 --high-align-penalty-score 1.5` | +2,974K | -42K | 60.3% | 2.03 | 1.85% | 5.24 | negligible |
| T10a | T8+T7 | stag 90/120 + SL min 40 | +2,988K | -28K | 62.9% | 2.24 | 1.82% | **6.05** | **BEST品質** |
| **T10b** | **T8+T9** | stag 90/120 + align penalty | **+3,197K** | **+181K** | 61.6% | 2.10 | 1.86% | 5.66 | **BEST組合せ** |

## フィードバック検証結果

### Priority 1: Off-hours (TOKYO) TREND制限
- **フィードバックの主張**: TOKYO時間帯のTREND取引PF 1.38で弱い → ブロックすべき
- **検証結果**: T5 (off-hours TREND block) で -241K の悪化。UTCベースの時間帯制限は有効なトレードも殺す
- **結論**: **REJECT** — off-hoursでも利益のあるTRENDトレードが多い

### Priority 2: STAGNATION実装不一致
- **フィードバックの主張**: config 120分なのに60/90分で退出する実装不整合
- **検証結果**:
  - T1 (progressive stagnation OFF): +55K 改善（marginal）
  - T2 (stagnation 180min): +133K 改善
  - **T8 (regime: TREND=90, RANGE=120)**: **+202K 改善 — 最良結果**
- **結論**: **ADOPT T8** — TREND 60→90分、RANGE 90→120分に緩和。CHOPPYは120分維持

### Priority 3: TREND SL最小値引き上げ
- **フィードバックの主張**: ATR由来20pips SLが小さすぎる → 30-40pipsに引き上げ
- **検証結果**:
  - T6 (30pips): -236K, PF 2.09, Sharpe 5.55（利益減だがPF/Sharpe改善）
  - T7 (40pips): -266K, PF 2.18, Sharpe 5.74（品質はさらに向上）
  - T10a (T8+T7): -28K, PF **2.24**, Sharpe **6.05**（T8と組み合わせで利益ほぼ維持+品質大幅向上）
- **結論**: **単体REJECT、T8との組合せで品質重視なら検討可** — SL拡大は取引数減で利益減少するが、品質指標（PF/Sharpe）は改善

### Priority 4: 高HTF alignment (>0.55) ペナルティ
- **フィードバックの主張**: 高alignment時にペナルティを課して過信エントリーを抑制
- **検証結果**: T9 (threshold=0.55, penalty=1.5): -42K（ほぼ中立）
- **結論**: **REJECT** — 効果なし。`align_signed`変数は存在せず、`ma_alignment`は-1〜+1正規化で高値域の問題は限定的

## 推奨設定

### Option A: 利益最大化（推奨）
```bash
--stag-trend-minutes 90 --stag-range-minutes 120
```
- Profit: +3,218K (+202K vs baseline)
- WR: 61.5%, PF: 2.08, DD: 1.75%, Sharpe: 5.62

### Option B: 品質最大化（Sharpe重視）
```bash
--stag-trend-minutes 90 --stag-range-minutes 120 --trend-sl-min 40
```
- Profit: +2,988K (-28K vs baseline)
- WR: 62.9%, PF: 2.24, DD: 1.82%, Sharpe: 6.05

### Option C: バランス型
```bash
--stag-trend-minutes 90 --stag-range-minutes 120 --high-align-penalty-threshold 0.55 --high-align-penalty-score 1.5
```
- Profit: +3,197K (+181K vs baseline)
- WR: 61.6%, PF: 2.10, DD: 1.86%, Sharpe: 5.66

## 操作方法

```bash
# Option A (利益最大化)
uv run python scripts/run_backtest.py --symbol USDJPY --years 2020-2025 \
  --stag-trend-minutes 90 --stag-range-minutes 120

# Option B (品質最大化)
uv run python scripts/run_backtest.py --symbol USDJPY --years 2020-2025 \
  --stag-trend-minutes 90 --stag-range-minutes 120 --trend-sl-min 40

# Option C (バランス型)
uv run python scripts/run_backtest.py --symbol USDJPY --years 2020-2025 \
  --stag-trend-minutes 90 --stag-range-minutes 120 \
  --high-align-penalty-threshold 0.55 --high-align-penalty-score 1.5
```

## 年別詳細比較（T0 vs T8 vs T10a）

| 年 | T0 Profit | T8 Profit | T10a Profit | T0 WR | T8 WR | T10a WR |
|----|-----------|-----------|-------------|-------|-------|---------|
| 2020 | +278K | +285K | +256K | 57.2% | 58.1% | 58.4% |
| 2021 | +131K | +152K | +207K | 57.0% | 57.5% | 59.9% |
| 2022 | +630K | +655K | +617K | 61.2% | 62.0% | 63.7% |
| 2023 | +578K | +625K | +661K | 62.7% | 63.5% | 65.5% |
| 2024 | +690K | +724K | +588K | 63.1% | 63.7% | 64.1% |
| 2025 | +709K | +777K | +659K | 63.3% | 64.3% | 65.6% |
