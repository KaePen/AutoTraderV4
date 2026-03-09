# 10ペア マルチ通貨ペアバックテスト パラメータ最適化計画

## Context

マルチ通貨ペアBTの実装（PR #545）とベースTF/年間収益率修正（PR #546）が完了。
次のステップとして、10ペアでのリスク最小化・WR最大化パラメータを探索する。

**目標**: 年間収益率 +80% 以上を維持しつつ、リスク（DD）を極力下げ、WRを最大化する。
**対象**: 10ペア（JPY 6 + USD 4）
**制約**: 全てキューランナー経由で実行

## 対象ペア（10ペア）

| グループ | ペア |
|---------|------|
| JPY (6) | USDJPY, EURJPY, GBPJPY, AUDJPY, CADJPY, CHFJPY |
| USD (4) | EURUSD, GBPUSD, AUDUSD, NZDUSD |

USDCAD/USDCHF は信号生成問題（6年で72-86トレード）のため除外。

## 検証フェーズ

### Phase 1: ベースライン確立（2テスト）

10ペア・global_max=8 で現行推奨設定（R1ベース）と安全設定を比較。

| テスト | global | per_pair | exposure | risk% | CT | 目的 |
|--------|--------|----------|----------|-------|-----|------|
| B1 | 8 | 1 | 12.0 | 0.015 | 9.5 | R1を10ペア/8posに拡張 |
| B2 | 8 | 1 | 12.0 | 0.01 | 9.5 | リスク低減ベースライン |

**判定基準**: 年間収益率、DD、WR、PF、Sharpe、月間勝率を記録。
B1の収益率が+80%超なら、リスク低減の余地あり → Phase 2へ。

### Phase 2: 品質フィルタ（CT）スイープ（4テスト）

CTを上げることでWRを上げつつ、収益率+80%を維持できる閾値を特定。
Phase 1の良い方のrisk%をベースに使用。

| テスト | global | per_pair | exposure | risk% | CT | 目的 |
|--------|--------|----------|----------|-------|-----|------|
| C1 | 8 | 1 | 12.0 | ※ | 10.0 | 軽い品質UP |
| C2 | 8 | 1 | 12.0 | ※ | 10.5 | 中程度品質UP |
| C3 | 8 | 1 | 12.0 | ※ | 11.0 | 強い品質UP |
| C4 | 8 | 1 | 12.0 | ※ | 11.5 | 最大品質フィルタ |

※ Phase 1結果に基づき決定（0.015 or 0.01）

**判定基準**: WR最大かつ年間収益率 ≥ +80% の CT を特定。

### Phase 3: リスク/ポジション微調整（3-4テスト）

Phase 2で最適CTが決まった後、リスク率とポジション制限を微調整。

| テスト | global | per_pair | exposure | risk% | CT | 目的 |
|--------|--------|----------|----------|-------|-----|------|
| D1 | 8 | 1 | 12.0 | 0.012 | ※CT | リスク中間値 |
| D2 | 6 | 1 | 10.0 | ※risk | ※CT | global制限強化 |
| D3 | 10 | 1 | 15.0 | ※risk | ※CT | global制限緩和 |
| D4 | 8 | 2 | 12.0 | 0.01 | ※CT | 2pos/pair低リスク |

※ Phase 2結果に基づき決定

**判定基準**: DD最小 かつ 年間収益率 ≥ +80% かつ WR ≥ 70%

### Phase 4: 個別ペア深掘り（必要に応じて）

Phase 3までの結果でWRが低い or DDが高いペアが特定された場合:

- 該当ペアのCTを個別に調整（per-pair CT override）
- BCA min_edge を調整（プリセット値変更 → コード変更 → worktree PR）
- 特定ペアの除外検討（10ペア → 9ペア等）

**注**: 個別ペアのパラメータ変更はコード変更を伴うため、worktree + PR が必要。
その後の検証はキューランナーで実施。

### Phase 5: 最終確認（1テスト）

全Phase結果を踏まえた最終推奨設定で、6年フルバックテストを実行。

| テスト | 内容 |
|--------|------|
| F1 | 最終推奨パラメータでの確認実行 |

## 実行方法

全テストはキューランナー経由（`backtest_queue.json`）で実行。

### キュージョブ形式

```json
{
  "jobs": [
    {
      "id": "10p-B1",
      "type": "multi_pair",
      "name": "B1: Baseline 10pairs",
      "multi_pair_config": {
        "pairs": ["USDJPY","EURJPY","GBPJPY","AUDJPY","CADJPY","CHFJPY",
                  "EURUSD","GBPUSD","AUDUSD","NZDUSD"],
        "global_max_positions": 8,
        "per_pair_max_positions": 1,
        "global_max_exposure_lot": 12.0,
        "base_risk_pct": 0.015,
        "consensus_threshold": 9.5,
        "tests": ["B1"]
      }
    }
  ]
}
```

### 実行順序

1. Phase 1: B1, B2 を同時投入（並列可能）
2. Phase 2: Phase 1結果を分析 → C1-C4 を投入
3. Phase 3: Phase 2結果を分析 → D1-D4 を投入
4. Phase 4: 必要に応じてコード変更 + 再検証
5. Phase 5: 最終確認 F1

各Phase間で結果を分析し、次Phaseのパラメータを決定する。

## パフォーマンス見積もり

- M1基準: ~525K bars/year/pair × 10 pairs = ~5.25M iterations/year
- 6年 = ~31.5M iterations/test
- Phase 1-3 合計: ~10テスト = ~315M iterations
- 推定: 1テスト30-60分、Phase 1-3 合計 5-10時間

## 成功基準

最終推奨設定が以下を全て満たすこと:

| 指標 | 基準 |
|------|------|
| 年間収益率 | ≥ +80% |
| 最大DD | ≤ 5% |
| WR | ≥ 70% |
| PF | ≥ 2.5 |
| 月間勝率 | ≥ 90% |
| Sharpe | ≥ 4.0 |

## レポート出力

`reports/multi_pair_10p_optimization.md` に全Phase結果を集約。
