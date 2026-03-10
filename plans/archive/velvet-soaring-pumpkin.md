# EURJPY 通貨ペア最適化計画

## Context

EURJPY の 2023-2025 年パフォーマンスを USDJPY 同期間と同水準に引き上げる。
EURJPY は現在 max_positions=2, base_risk_pct=0.02 と保守的な設定。
USDJPY は max_positions=8, base_risk_pct=0.08 と積極的で、利益規模に大差がある可能性。

**制約**: 24スレッド、1実行あたりCPU 5消費 → 最大4本並列実行

## 最適化ステップ

### Phase 1: ベースライン確立（2テスト、並列）

| ID | 内容 | コマンド概要 |
|----|------|-------------|
| T0 | USDJPY 2023-2025 デフォルト | `--symbol USDJPY --years 2023-2025` |
| T1 | EURJPY 2023-2025 デフォルト | `--symbol EURJPY --years 2023-2025` |

→ 両者の差分を定量化し、目標を設定

### Phase 2: エントリー品質（4テスト、並列）

consensus_threshold と bca_min_edge の最適値を探索。

| ID | パラメータ | 値 |
|----|-----------|-----|
| T2 | consensus_threshold | 8.0 |
| T3 | consensus_threshold | 8.5 |
| T4 | bca_min_edge | 0.55 (デフォルトに戻す) |
| T5 | bca_min_edge | 0.60 |

※ T1(9.0/0.65)がベースライン。Phase 1結果を見て調整の可能性あり。

### Phase 3: ポジションサイジング（4テスト、並列）

USDJPY との最大差はポジション数とリスク率。段階的に引き上げ。

| ID | max_positions | base_risk_pct | max_lot_per_trade | max_total_exposure_lot |
|----|--------------|---------------|-------------------|----------------------|
| T6 | 4 | 0.04 | 3.0 | 8.0 |
| T7 | 6 | 0.06 | 4.0 | 12.0 |
| T8 | 8 | 0.08 | 5.0 | 16.0 |
| T9 | 4 | 0.04（+ Phase2最良設定） | 3.0 | 8.0 |

### Phase 4: PM チューニング（4テスト、並列）

USDJPY で効果があった設定を EURJPY に適用。

| ID | 内容 |
|----|------|
| T10 | stag_trend=90, stag_range=120 |
| T11 | M1 Structure SL (min=10, max=40, buffer=3.0) |
| T12 | T10 + T11 結合 |
| T13 | Phase3最良 + Phase4最良 結合 |

### Phase 5: 最終検証（1-2テスト）

最良の組み合わせで最終確認。

## 実行方法

```bash
# 例: Phase 1
uv run python scripts/run_backtest.py --symbol USDJPY --years 2023-2025 --max-year-workers 5
uv run python scripts/run_backtest.py --symbol EURJPY --years 2023-2025 --max-year-workers 5

# 例: Phase 2 (パラメータ変更)
uv run python scripts/run_backtest.py --symbol EURJPY --years 2023-2025 \
  --bot-consensus-threshold 8.0 --max-year-workers 5

# ポジションサイジング変更
uv run python scripts/run_backtest.py --symbol EURJPY --years 2023-2025 \
  --max-positions 4 --risk-pct 0.04 --max-lot-per-trade 3.0 \
  --max-total-exposure 8.0 --max-year-workers 5
```

## 主要ファイル

- `config/symbol_presets.yaml` - 通貨ペア別設定
- `scripts/run_backtest.py` - バックテスト実行
- `autotrader/decision/unified/config.py` - UnifiedBotConfig
- `autotrader/decision/unified/risk/position_manager.py` - PositionManagerConfig

## 検証基準

- Profit: USDJPY 2023-2025 と同水準以上
- WR: 60%以上
- PF: 1.5以上
- DD: 3%以下
- Sharpe: 5.0以上
- 月間勝率: 90%以上

## レポート出力

最終結果を `reports/eurjpy_optimization_2023_2025.md` に出力
