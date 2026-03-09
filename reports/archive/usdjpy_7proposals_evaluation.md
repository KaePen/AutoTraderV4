# USDJPY 改善提案 7項目 + 追加検証の評価結果

日付: 2026-03-06
期間: 2020-2025 (6年)
通貨ペア: USDJPY

## ベースライン

| 指標 | 値 |
|------|-----|
| 利益合計 | +9,315K |
| 取引数 | 5,043 |
| 勝率 | 68.2% |
| PF | 1.96 |
| DD | 3.49% |
| 月間勝率 | 98.6% (71/72) |
| 月間+5%達成 | 65.3% (47/72) |
| 年間平均収益率 | 155.3% |

---

## Part 1: 元提案 7項目のバックテスト結果

### レジームADX上限テスト

| テスト | 設定 | 利益 | vs T0 | WR | PF | DD | 月間+ |
|--------|------|------|-------|-----|-----|-----|-------|
| T0 Baseline | デフォルト | +9,315K | — | 68.2% | 1.96 | 3.49% | 98.6% |
| T1 ADX=40 | adx_upper_limit=40 | +9,315K | **±0** | 68.2% | 1.96 | 3.49% | 98.6% |
| T2 ADX=35 | adx_upper_limit=35 | +9,315K | **±0** | 68.2% | 1.96 | 3.49% | 98.6% |

**判定**: NO EFFECT — regime_result.adx（H1集約）がUSDJPYで35を超えない

### TREND TF上限テスト

| テスト | 設定 | 利益 | vs T0 | WR | PF | DD | 月間+ |
|--------|------|------|-------|-----|-----|-----|-------|
| T3 TF cap=6 | trend_max_aligned_tfs=6 | +8,508K | **-807K** | 69.1% | 2.00 | 2.97% | 97.2% |
| T4 結合 | ADX=40 + TF cap=6 | +8,508K | **-807K** | 69.1% | 2.00 | 2.97% | 97.2% |

**判定**: REJECTED — 利益 -8.7%。DD改善(-0.52pp)あるが利益減が大きすぎる

### 7項目の最終判定

| # | 提案 | 判定 | 理由 |
|---|------|------|------|
| 1 | Tokyo session TREND blocking | REJECTED | 過去テスト -241K |
| 2 | ADX >= 40 blocking (regime) | NO EFFECT | regime ADXが35未満で発火せず |
| 3 | Extreme HTF alignment penalty | REJECTED | 過去テスト -42K |
| 4 | TREND tf_count >= 7 capping | REJECTED | -807K (-8.7%) |
| 5 | SL > 100 pips blocking | ALREADY_HANDLED | sl_max_pips=50でクランプ済み |
| 6 | Exposure tracking bug | ALREADY_FIXED | year_runner.py:185-207に実装済み |
| 7 | MAE vs SL consistency | ALREADY_HANDLED | 仕様通り（計画値vs実績値） |

---

## Part 2: SIGNAL_REV（consensus_exit）改善テスト

外部分析でSIGNAL_REVが WR 12.2%, -1,868K の負け寄与と指摘。
consensus_exit の設定変更で改善可能か検証。

### メカニズム解析

consensus_exit（`position_manager.py:1032-1088`）:
- 逆方向スコア >= 6.0 かつ 自方向スコア <= 3.0 → **全決済**
- `consensus_exit_loss_only=False`（含み益ポジションも決済対象）
- これがSIGNAL_REV出口の主要発生源

### テスト結果

| テスト | 設定 | 利益 | vs T0 | WR | PF | DD | 月間+ |
|--------|------|------|-------|-----|-----|-----|-------|
| T0 Baseline | デフォルト (threshold=6.0) | +9,315K | — | 68.2% | 1.96 | 3.49% | 98.6% |
| SR-T1 loss_only | consensus_exit_loss_only=True | +9,074K | **-241K** | 67.8% | 1.94 | **2.88%** | 98.6% |
| SR-T4 th=8.0 | consensus_exit_threshold=8.0 | +9,002K | **-313K** | 68.5% | 1.92 | 3.25% | 97.2% |
| SR-T2 th=9.0 | consensus_exit_threshold=9.0 | +8,709K | **-607K** | 68.6% | 1.90 | 3.92% | 95.8% |
| SR-T3 OFF | consensus_exit_enabled=False | +7,216K | **-2,100K** | 67.9% | 1.72 | 3.45% | 93.1% |

### 分析

- **SR-T3（完全無効）**: -2,100K (-22.5%) の大幅悪化。consensus_exitは必要な機能
- **SR-T2（th=9.0）**: 閾値を上げすぎると逃げ遅れ、DD 3.92%に悪化
- **SR-T1（loss_only）**: DD 2.88%に改善(-0.61pp)だが利益-241K。DD重視なら検討余地あり

**判定**: 現行設定（threshold=6.0, loss_only=False）が最適。
SIGNAL_REVは「負けトレードの早期損切り」として機能しており、
WR 12.2%で見えるのは本来の役割の結果。無効化すると損失が増える。

---

## Part 3: エントリー側ADX検証（個別TFのADXスコアリング）

外部分析の「ADX>=40」はregime ADXではなく個別TFのADXを想定していた可能性。
エントリー側のADXスコアリングへの上限/キャップを検証。

### 発見: `_score_adx` はデッドコード

`timeframe_evaluator.py:611-634` の `_score_adx()` メソッドは
**どこからも呼び出されていない**デッドコードだった。
ADX>40で3.0ボーナスを与えるロジックは実際には動作していない。

### ADXがスコアに影響する実際の経路

`strength_calculator.py:204-214` の `adx_factor`:
```python
adx_factor = 1.0 + (adx - 25) / 50  # ADX=40→1.3, ADX=50→1.5
adx_factor = min(adx_factor, 1.5)     # キャップ1.5
return max(-1.0, min(1.0, base_strength * adx_factor))  # [-1,1]クランプ
```

### テスト結果（adx_factor_cap変更）

| テスト | 設定 | 利益 | vs T0 |
|--------|------|------|-------|
| ADX-T1 | adx_extreme_threshold=40, bonus=0 | +9,315K | **±0** |
| ADX-T2 | adx_extreme_threshold=35, bonus=0 | +9,315K | **±0** |
| ADX-T3 | adx_extreme_threshold=30, bonus=0 | +9,315K | **±0** |
| ADX-T4 | adx_factor_cap=1.0 (ボーナス完全無効) | +9,315K | **±0** |
| ADX-T5 | adx_factor_cap=1.2 (ボーナス縮小) | +9,315K | **±0** |

**全テストでベースラインと完全同一結果。**

### 原因分析

1. `base_strength` が ±1.0（全SMA整列時）の場合、`base_strength * adx_factor` は
   adx_factor に関わらず `min(1.0)` でクランプされ差が出ない
2. `base_strength` が ±0.5 の場合でも、差は最大 0.25 程度
3. この差はコンセンサス閾値 9.0 に対して無視できるレベル（重み考慮でも 0.25-0.75）

**判定**: ADXはUSDJPYのトレード判定に実質的に無影響。
`_score_adx` はデッドコードとして将来整理可能。

---

## 総合結論

### 検証した全パラメータ

| カテゴリ | テスト数 | 改善あり | 判定 |
|----------|---------|---------|------|
| 元提案7項目 | 4テスト | 0 | 全て不採用/対応不要 |
| SIGNAL_REV改善 | 4テスト | 0 | 現行設定が最適 |
| エントリー側ADX | 5テスト | 0 | 影響なし（デッドコード） |
| **合計** | **13テスト** | **0** | **現行設定が最適** |

### 構造的発見

1. **ADXは飾り**: regime ADXもentry ADXもUSDJPYの判定に影響しない。
   ADXベースの改善提案は全て無効。
2. **consensus_exit は必要機能**: 無効化で-2,100K (-22.5%)。
   SIGNAL_REV WR 12.2%は「損切り機能」として正常動作の結果。
3. **TREND TF cap**: 利益減(-807K)が大きくDD改善(-0.52pp)に見合わない。
4. **`_score_adx` デッドコード**: 将来のリファクタリングで削除候補。

### 現行システムの強み

6年間で+9,315K、WR 68.2%、PF 1.96、月間勝率98.6%は
安定した性能を示しており、パラメータ微調整よりも
ロジックの構造的改善（新しいシグナルソースの追加等）が次の改善ステップとなる。
