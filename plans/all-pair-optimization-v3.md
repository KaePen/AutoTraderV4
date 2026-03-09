# 全ペア最適化計画 v3（pip_value修正後）

## 目標
- 全12ペアで WR ≥70%, 月間+ ≥80%
- DD <10% (各ペア)
- リスク分散: 全ペアで安定したパフォーマンス

## Phase 1: ベースライン取得（現在実行中）

全12ペア × 2020-2025 × 8TF でバックテスト実行。
pip_value修正後の正しいスケールでの結果を取得。

### 期待される変化
- JPYペア: 利益額10x↑, DD%大幅上昇（正しいスケール）
- USDペア: 変更なし

## Phase 2: パラメータ調整

### JPYペア（DD%上昇のため調整必要）

旧DD → 新DD（推定）の対応:
- USDJPY: 1.59% → ~8.5% (risk=0.08, pos=8)
- EURJPY: 2.80% → ~12-15% (risk=0.04, pos=4)
- GBPJPY: 2.62% → ~10-13% (risk=0.03, pos=3)
- AUDJPY: 2.49% → ~10-12% (risk=0.03, pos=3)
- CADJPY: 2.81% → ~12-15% (risk=0.025, pos=2)
- CHFJPY: 2.65% → ~11-13% (risk=0.025, pos=3)

#### 調整方針
DD <10% を目標に risk_pct と max_positions を下げる。
基本式: risk_pct を半分 → DD も概ね半分。

### USDペア（変更なしだが最適化未済のペアあり）
- EURUSD: DD 10.04% → 調整必要（risk=0.02でDD10%は高すぎ）
- GBPUSD, AUDUSD, NZDUSD, USDCHF, USDCAD: 初ベースライン

## Phase 3: 検証ラウンド

調整後のパラメータで全ペア再実行。
目標未達のペアは追加調整。

## Phase 4: 結果反映

- symbol_presets.yaml 更新
- MEMORY.md ベースライン更新
- PR作成
