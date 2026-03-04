# 早期利益ガード（Early Profit Guard）実装計画

## Context

ユーザーの観察: 「エントリー後、数分間は有利方向に動くが、その後逆方向に動いてSLに到達するケースが多い。利益があるうちに利確しておけばよかった」

### 既存メカニズムの限界

| シナリオ | 既存機能 | 不足点 |
|---------|---------|--------|
| MFE 0.15R → 逆方向スコア上昇 | `profit_reversal` | MFE >= 0.3R 必須で小利益は対象外 |
| MFE 0.10R → opp_score 4.5に上昇 | `consensus_exit` | opp >= 6.0 AND own <= 3.0 で厳しすぎ |
| 0.2R → 0.02R に減少中 | なし | 小利益帯 + センチメント悪化の組合せ検出がない |

### 技術的制約
- `UPDATE_TP` アクションタイプが存在しない（`current_tp` フィールドもない）
- → TP修正ではなく **`FULL_CLOSE`（市場価格決済）** で対応
- `evaluate()` に `buy_score`/`sell_score` がリアルタイムで渡される → スコア差で逆方向検出可能

### リスク警告
- `very_early_exit`: 過去最大の劣化要因（-935K, WR -5.8pp）
- `profit_reversal`: EURJPY最大劣化要因（-460K）
- 早期exit系は過激すぎると大幅悪化する → **デフォルトOFF + 慎重なバックテスト必須**

## 実装内容

### 変更ファイル: `autotrader/decision/unified/risk/position_manager.py` のみ

CLI自動生成により `run_backtest.py` の変更は不要。

### Config追加（`PositionManagerConfig` 末尾, 行~293）

```python
# 早期利益ガード: 小利益+センチメント悪化で早期撤退
early_profit_guard_enabled: bool = False
# MFE最低値（一度は有利に動いた証拠）
early_profit_guard_min_mfe_r: float = 0.05
# 現在含み益の最低R値
early_profit_guard_min_r: float = 0.0
# 大利益は対象外（profit_reversalに任せる）
early_profit_guard_max_r: float = 0.30
# 逆方向スコア - 自方向スコアの差（これ以上で発動）
early_profit_guard_score_diff: float = 1.0
# 逆方向スコアの最低値（ノイズ排除）
early_profit_guard_min_opp_score: float = 4.0
# 最低保有時間（分、エントリーノイズ排除）
early_profit_guard_min_hold_minutes: float = 5.0
```

### 新メソッド: `_check_early_profit_guard()`

**発動条件（全て AND）:**
1. `highest_r >= min_mfe_r` (0.05R) — 一度は有利方向に動いた
2. `current_r > min_r` (0.0) — 現在まだ含み益
3. `current_r <= max_r` (0.30R) — 大利益はprofit_reversalに委任
4. `elapsed >= min_hold_minutes` (5分) — エントリーノイズ除外
5. `opp_score >= min_opp_score` (4.0) — 逆方向に実質的な勢い
6. `opp_score - own_score >= score_diff` (1.0) — スコア差で逆転検知

**アクション:** `ManagementAction.full_close()` with `ExitReason.TAKE_PROFIT_EARLY`

### evaluate() への挿入位置

`_check_profit_reversal()` の後、`_check_stagnation_exit()` の前（ステップ3.6）:

```
SL → partial_close → TP → profit_reversal → **早期利益ガード** → stagnation → time → signal_rev → consensus → trailing
```

### ライブエンジンとの互換性

`engine.py` と `position_sync.py` は `buy_score=0.0, sell_score=0.0` で呼び出すため、
`if buy_score == 0.0 and sell_score == 0.0: return None` で安全にスキップ。

## テスト計画

### バックテスト検証
```bash
# 有効化（デフォルト設定）
--symbol USDJPY --pm-early-profit-guard-enabled

# スコア差感度（攻撃的 → 保守的）
--pm-early-profit-guard-score-diff 0.5
--pm-early-profit-guard-score-diff 2.0

# R範囲調整
--pm-early-profit-guard-max-r 0.20   # より狭い利益帯のみ

# 保有時間調整
--pm-early-profit-guard-min-hold-minutes 3.0  # より早期に発動
--pm-early-profit-guard-min-hold-minutes 10.0 # もう少し待つ
```

### 評価基準
- 利益: Baseline比 ±5% 以内なら品質改善として許容
- WR: 改善が期待される（小損失→小利益に変換）
- DD: 改善が期待される
- STAG比率: 減少が期待される（STAGに到達する前に利確）
