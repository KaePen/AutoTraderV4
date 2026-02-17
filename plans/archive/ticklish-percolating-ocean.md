# 計画: ChatGPT分析に基づく輻輳型アーキテクチャ改良

## Context

ChatGPTによる外部分析 (`reports/chatgpt-output.txt`) で勝てない本質原因が5つ指摘された:
1. DAYが出場しすぎる（RANGEでDAYが動く）
2. TP/SLの決定が二重化し調整が効かない
3. HTF整合がモード別でなく短期戦略に過剰
4. SoftGuardが設計にあるのに実質使われていない
5. Entryの根拠が薄くてもスコアで通ってしまう

**重要な発見**: ChatGPTの提案の約75%は、既に**輻輳型アーキテクチャ**(`_generate_signal_convergent`)に実装済み。
StrategyPool、StrategySelector、InStrategyConsensus、戦略別HTF factor等が全て存在する。
しかし現在バックテストでは`use_convergent_architecture=False`で無効化されている。

輻輳型が以前「悪化」した原因は、Bug A/C/D修正前のテストだったため。
修正後に再評価し、ChatGPTの提案を追加統合する方針。

## アーキテクチャ選択: 輻輳型の改良（選択肢B）

**理由**:
- 新アーキテクチャ(`_generate_signal_new`)は戦略間比較が不可能（単一モード→単一TFセット）
- 輻輳型は StrategyPool→全戦略並列評価→Selector(edge_score)の構造を持ち、ChatGPTの提案と合致
- 差分実装で済む（新規コード量が最小）

## ChatGPT提案10項目の採否

| # | 提案 | 判定 | 理由 |
|---|------|------|------|
| 1 | 戦略4つ (NoTrade追加) | **Phase 1** | NoTradeStrategy新規追加のみ |
| 2 | TP/SL単一化 | **不要** | 輻輳型は既にProposedTradeのsl/tpが最終値 |
| 3 | SoftGuard→edge_score乗算 | **Phase 1** | `_calculate_edge_components`に1要素追加 |
| 4 | HTF整合を戦略別強度で管理 | **Phase 2** | htf_weight値の調整 |
| 5 | DAY特別枠（発動条件厳格化） | **Phase 1** | `_passes_pre_filters`オーバーライド |
| 6 | RANGEは戦わない | **Phase 1** | regime_weights調整 + NoTrade |
| 7 | Selectorヒステリシス | **Phase 3** | `choose()`のpass部分を実装 |
| 8 | InStrategyConsensus簡素化 | **不要** | 既に固定重み型で実装済み |
| 9 | Exit管理追加 | **Phase 2-3** | 既存ExitManager/PartialCloseManagerの統合 |
| 10 | 型強制 | **部分採用** | soft_guard_factorフィールド追加のみ |

---

## Phase 0: 輻輳型ベースライン計測（前提）

### 目的
Bug A/C/D修正後の環境で輻輳型の性能を再測定し、改良のベースラインを確立する。

### 変更 (1ファイル)
- `src/autotrader/backtest/service.py`: `use_convergent_architecture=False` → `True`

### 検証
```bash
python scripts/run_backtest.py --year 2023 --flow-analysis
```
- 輻輳型のPF/勝率/取引数を記録
- 以前より改善されていればPhase 1に進む
- 改善されていなければ原因調査（edge_score分布、戦略別選択率）

---

## Phase 1: SoftGuard統合 + DAY/RANGE制限 + NoTrade（目標: 勝率52%）

### 1-A: SoftGuardをedge_scoreに統合

**`strategies/types.py`** — EdgeScoreComponentsにフィールド追加:
```python
soft_guard_factor: float  # 1.0 - penalty (0.1〜1.0)
```
edge_scoreプロパティの計算式に `* self.soft_guard_factor` 追加

**`strategies/base.py`** — `_calculate_edge_components`を変更:
- StrategyContextからspread_pips, hour_utcを取得
- SoftGuard.check()を呼び出してpenalty算出
- `soft_guard_factor = max(0.1, 1.0 - penalty)` を計算
- EdgeScoreComponentsに渡す
- StrategyConfigにsoft_guard参照を追加

**`trade_bot.py`** — `_generate_signal_convergent`でSoftGuardインスタンスを戦略に渡す

### 1-B: RANGE時のDAY制限

**`strategies/short_mid.py`**:
- `regime_weights`: `RANGE: 0.5 → 0.2`, `LOW_VOL: 0.6 → 0.3`
- `_passes_pre_filters`オーバーライド: RANGE時 + trend_strength < 0.5 → False

**`strategies/scalp.py`**:
- `regime_weights`: `RANGE: 0.6 → 0.8`（RANGE時のScalp唯一化）

**`strategies/swing.py`**:
- `regime_weights`: `RANGE: 0.4 → 0.1`（RANGE時はほぼ候補外）

### 1-C: NoTradeStrategy追加

**`strategies/no_trade.py`** (新規):
- BaseStrategy継承
- `evaluate()`は常にHOLDのProposedTradeを返す
- ただしRANGE+高ペナルティ時はedge_score=0.8を返す → 他戦略より高くなり「取引しない」が勝つ

**`strategies/types.py`** — StrategyIdに`NO_TRADE = "no_trade"`追加

**`strategy_pool.py`** — `__init__`にNoTradeStrategyを追加（4戦略に）

### 変更ファイル一覧 (7ファイル)
| ファイル | 変更内容 |
|---------|---------|
| `strategies/types.py` | EdgeScoreComponents拡張、StrategyId拡張 |
| `strategies/base.py` | _calculate_edge_componentsにSoftGuard統合 |
| `strategies/short_mid.py` | RANGE制限、_passes_pre_filtersオーバーライド |
| `strategies/scalp.py` | RANGE weight引き上げ |
| `strategies/swing.py` | RANGE weight引き下げ |
| `strategies/no_trade.py` | 新規: NoTradeStrategy |
| `strategy_pool.py` | NoTradeをプールに追加 |

### 検証
- 取引数: 1197 → 800-1000（低品質が除去される分減少）
- 勝率: 48% → 52%+
- RANGE時DAY取引数の大幅減少を確認

---

## Phase 2: HTF戦略別強度 + DAY厳格化 + Exit統合（目標: 勝率55%）

### 2-A: HTF整合の戦略別調整
- `scalp.py`: `htf_weight: 0.5 → 0.3`（HTFがScalpを過度にブロックしない）
- `swing.py`: `htf_weight: 0.8 → 0.9`（HTF不整合はほぼ即失格）

### 2-B: ShortMid(=Day)の厳格化
- `short_mid.py`の`_passes_pre_filters`強化:
  - TREND以外は全てFalse
  - `trend_strength < 0.5` → False
- `min_edge_score: 0.12 → 0.18`
- `min_confidence: 0.30 → 0.40`

### 2-C: Exit管理のバックテスト統合
- `runner.py`: PartialCloseManager/ExitManagerの統合
- `simulator.py`: 部分決済対応（ロット分割）のインターフェース追加

### 変更ファイル一覧 (4-6ファイル)
| ファイル | 変更内容 |
|---------|---------|
| `strategies/scalp.py` | htf_weight緩和 |
| `strategies/swing.py` | htf_weight厳格化 |
| `strategies/short_mid.py` | _passes_pre_filters厳格化、閾値引き上げ |
| `backtest/runner.py` | Exit管理統合 |
| `backtest/simulator.py` | 部分決済対応 |

---

## Phase 3: ヒステリシス + 微調整（目標: 勝率56%）

### 3-A: Selectorヒステリシス
- `strategy_selector.py`の`choose()` L103のpass部分を実装
- `SelectorConfig`に`min_switch_margin: float = 0.10`追加
- 新戦略と現戦略のedge_score差がmin_switch_margin未満なら切替しない

### 3-B: Phase 2の結果に基づく閾値最適化
- 各戦略のmin_edge_score微調整
- tp_sl_ratio_range微調整

---

## リスク対策

| リスク | 対策 |
|--------|------|
| 輻輳型がバグ修正後も悪化 | Phase 0で判明。新アーキテクチャ側にSoftGuard統合する選択肢Aに切替 |
| DAY厳格化で取引数激減 | 閾値を段階的に締める（まず0.4、効果を見て0.5） |
| NoTradeが過度に止める | edge_score算出ロジックを慎重に設計、Phase 1で値調整 |
| 過学習 | 2023年最適化後、2021-2022年でアウトオブサンプルテスト |

## 重要な既存コード（再利用）

| コンポーネント | ファイル | 状態 |
|-------------|---------|------|
| StrategyPool.evaluate_all() | `strategy_pool.py:94` | 完全実装済み |
| StrategySelector.choose() | `strategy_selector.py:57` | 完全実装済み（pass部分のみ未実装） |
| EdgeScoreComponents | `strategies/types.py:60` | 5要素の乗算 |
| BaseStrategy._calculate_edge_components() | `strategies/base.py:291` | SoftGuard追加の受け皿 |
| BaseStrategy._passes_pre_filters() | `strategies/base.py:184` | オーバーライド用基底 |
| SoftGuard.check() | `constraint/soft_guard.py:196` | total_penalty返却 |
| InStrategyConsensus | `strategies/in_strategy_consensus.py` | 固定重み統合済み |
| ProposedTrade型 | `strategies/types.py` | 必要フィールド定義済み |
