# 輻輳型アーキテクチャ移行計画

## 概要

UnifiedTradeBotの判定ロジックを「単線型（単一モード選択）」から「輻輳型（並列戦略評価＋メタ選択）」へ移行する。

### 目的
- 同一時点で複数の保有期間戦略（スキャ/短中期/スイング）を並列評価
- edge_scoreに基づき最適戦略を自動選択
- HTF整合を戦略別に強度可変化（スキャ=弱、スイング=強）

### 現状の課題
- 単一時間軸の意思決定に寄り、局面適応が弱い
- HTF整合が過剰抑制になる
- 勝率43-46%（目標60%に未達）

---

## アーキテクチャ比較

### 現在のフロー（単線型）
```
generate_signal() → _generate_signal_new()
├─ MarketRegimeDetector.detect_from_row()
├─ TradingModeSelector.select() → TradingPlan（単一モード）
├─ TimeframeRouter.route() → TimeframeSet
├─ RiskManager.can_trade()
├─ TimeframeEvaluator.evaluate() × N
├─ ModeAwareScoreConsensus.consolidate()
├─ _check_htf_trend_alignment()（必須フィルター）
├─ PositionSizer.calculate()
└─ ConsolidatedSignal
```

### 新フロー（輻輳型）
```
generate_signal() → _generate_signal_convergent()
├─ MarketRegimeDetector（流用）
├─ RiskManager.can_trade()（流用）
├─ StrategyPool.evaluate_all()（新規）
│   ├─ ScalpStrategy (M1,M5,M15 + 弱いH1)
│   ├─ ShortMidStrategy (M15,H1,H4)
│   └─ SwingStrategy (H1,H4,D1)
├─ StrategySelector.choose()（新規）
├─ PositionSizer.calculate()（流用）
└─ ConsolidatedSignal（拡張）
```

---

## ファイル構成

```
src/autotrader/decision/unified/
├── strategies/                          # 新規ディレクトリ
│   ├── __init__.py
│   ├── types.py                         # ProposedTrade, StrategyContext等
│   ├── base.py                          # BaseStrategy抽象クラス
│   ├── in_strategy_consensus.py         # 戦略内統合ロジック
│   ├── scalp.py                         # ScalpStrategy
│   ├── short_mid.py                     # ShortMidStrategy
│   └── swing.py                         # SwingStrategy
├── strategy_pool.py                     # 新規: StrategyPool
├── strategy_selector.py                 # 新規: StrategySelector
├── trade_bot.py                         # 修正: _generate_signal_convergent()追加
└── signal_consolidator.py               # 修正: ConsolidatedSignal拡張
```

---

## コンポーネント方針

### 流用（Keep）
| コンポーネント | ファイル | 理由 |
|--------------|---------|------|
| MarketRegimeDetector | `calculator/features/regime_detector.py` | 独立した純粋判定エンジン |
| TimeframeEvaluator | `decision/unified/timeframe_evaluator.py` | TF別評価の単一責任設計 |
| PositionSizer | `decision/unified/position_sizer.py` | インターフェース設計済み |
| RiskManager | `decision/unified/trade_bot.py`内 | シンプルなゲーティング |

### 置換/吸収（Replace/Absorb）
| コンポーネント | 対応 | 理由 |
|--------------|------|------|
| TradingModeSelector | 廃止 | 各戦略に固定plan内包 |
| TimeframeRouter | 廃止 | 各戦略に参照TFセット固定 |
| ModeAwareScoreConsensus | InStrategyConsensusへ移動 | 戦略内統合に閉じる |
| HTF必須フィルター | 戦略別強度可変 | htf_weightで制御 |

---

## 型定義

### ProposedTrade（各戦略からの出力）
```python
@dataclass(frozen=True)
class ProposedTrade:
    strategy_id: StrategyId          # scalp/short_mid/swing
    direction: SignalType            # BUY/SELL/HOLD
    edge_score: float                # 選択基準（0.0-1.0）
    edge_components: EdgeScoreComponents
    consensus: InStrategyConsensusResult
    primary_tf: str
    sl_pips: float
    tp_pips: float
    reasoning: str
```

### EdgeScoreComponents
```python
@dataclass(frozen=True)
class EdgeScoreComponents:
    base_confidence: float           # 戦略内統合の確度
    score_margin_factor: float       # スコア差分係数
    regime_fit_factor: float         # レジーム適合係数
    cost_factor: float               # コスト係数
    htf_conflict_factor: float       # HTF整合係数

    @property
    def edge_score(self) -> float:
        return (base_confidence * score_margin_factor
                * regime_fit_factor * cost_factor * htf_conflict_factor)
```

### StrategyTimeframes（各戦略の時間足設定）
```python
@dataclass(frozen=True)
class StrategyTimeframes:
    primary_tf: str                  # 主要時間足
    entry_tf: str                    # エントリー時間足
    confirm_tfs: tuple[str, ...]     # 確認用時間足
    htf_refs: tuple[str, ...]        # HTF参照リスト
    htf_weight: float                # HTFフィルター強度 (0.0-1.0)
    tp_sl_ratio_range: tuple[float, float]
```

---

## 戦略定義

### ScalpStrategy
- **TFセット**: M5(primary), M1(entry), M15(confirm), H1(htf_ref)
- **htf_weight**: 0.3（弱い参照）
- **TP/SL比率**: 1.0-1.5
- **レジーム適合**: HIGH_VOL=1.2x, TREND=0.8x

### ShortMidStrategy
- **TFセット**: M15(primary), H1(entry), H4(confirm)
- **htf_weight**: 0.6（中程度）
- **TP/SL比率**: 1.5-2.5
- **レジーム適合**: TREND/RANGE両方で有効

### SwingStrategy
- **TFセット**: H1(primary), H4(entry), D1(confirm)
- **htf_weight**: 1.0（必須に近い）
- **TP/SL比率**: 2.0-4.0
- **レジーム適合**: STRONG_TREND=1.3x

---

## 実装順序

### Phase 1: 型定義とインフラ
1. `strategies/types.py` - 全型定義
2. `signal_consolidator.py` - ConsolidatedSignal拡張（後方互換）

### Phase 2: 戦略基盤
3. `strategies/in_strategy_consensus.py` - 戦略内統合
4. `strategies/base.py` - BaseStrategy抽象クラス

### Phase 3: 具体戦略
5. `strategies/scalp.py` - ScalpStrategy
6. `strategies/short_mid.py` - ShortMidStrategy
7. `strategies/swing.py` - SwingStrategy

### Phase 4: 戦略管理
8. `strategy_pool.py` - StrategyPool
9. `strategy_selector.py` - StrategySelector

### Phase 5: 統合
10. `trade_bot.py` - `_generate_signal_convergent()` 追加
11. `trade_bot.py` - `generate_signal()` にフラグ切替追加

### Phase 6: 検証
12. 既存バックテストの動作確認

---

## ConsolidatedSignal拡張

```python
@dataclass(frozen=True)
class ConsolidatedSignal:
    # 既存フィールド（互換性維持）
    direction: SignalType
    confidence: float
    primary_tf: str
    aligned_tfs: list[str]
    sl_pips: float
    tp_pips: float
    rationale: str
    scores: dict[str, float] = field(default_factory=dict)

    # 新規追加フィールド
    chosen_strategy_id: StrategyId | None = None
    edge_score: float | None = None
    edge_components: EdgeScoreComponents | None = None
    all_proposals: list[ProposedTrade] = field(default_factory=list)
```

---

## generate_signal() 新骨格

```python
def _generate_signal_convergent(
    self,
    current_time: pd.Timestamp,
    candle: Candle | None = None,
) -> ConsolidatedSignal:
    # 1. 日次リセット
    self.risk_manager.reset_daily(py_time)

    # 2. レジーム検出（流用）
    regime_result = self._detect_regime(current_time)

    # 3. リスク管理チェック（流用）
    can_trade, reason = self.risk_manager.can_trade(py_time)
    if not can_trade:
        return self._hold_signal(reason)

    # 4. StrategyContext構築
    context = StrategyContext(
        regime_result=regime_result,
        current_price=candle.close,
        spread_pips=self._get_spread_pips(),
        hour_utc=current_time.hour,
        has_open_position=self._has_open_position(),
        current_strategy_id=self._get_current_strategy_id(),
    )

    # 5. 保有中は戦略切替しない
    if context.has_open_position:
        return self._continue_current_strategy(context, current_time, candle)

    # 6. 全TFのデータ取得
    tf_data = self._get_all_tf_data(current_time)

    # 7. 戦略プールで全戦略評価
    pool_result = self.strategy_pool.evaluate_all(context, tf_data, candle)

    # 8. 戦略選択
    selection = self.strategy_selector.choose(pool_result, context)
    if selection.chosen is None:
        return self._hold_signal(selection.reasoning)

    # 9. ポジションサイジング（流用）
    sizing_result = self.position_sizer.calculate(sizing_context)

    # 10. シグナル返却
    return ConsolidatedSignal(
        direction=selection.chosen.direction,
        confidence=selection.chosen.edge_score,
        chosen_strategy_id=selection.chosen.strategy_id,
        edge_score=selection.chosen.edge_score,
        ...
    )
```

---

## 受入条件（Definition of Done）

- [ ] バックテストが例外なく完走
- [ ] ログに `chosen_strategy_id` が出力される
- [ ] 同一時点で3戦略が評価され、edge_scoreで採択される
- [ ] HTF整合が戦略別に効く（scalp=弱、swing=強）
- [ ] 保有中は戦略切替しない

---

## 検証方法

```bash
# 1. 単体テスト
uv run pytest tests/unit/decision/unified/strategies/ -v

# 2. 統合テスト
uv run pytest tests/integration/test_convergent_signal.py -v

# 3. バックテスト動作確認
uv run python scripts/run_fast_backtest.py --years 2023

# 4. 戦略別ログ確認
grep "chosen_strategy_id" backtest_output.log
```

---

## 重要ファイル

| ファイル | 操作 | 内容 |
|---------|-----|------|
| `decision/unified/strategies/types.py` | 新規 | 全型定義 |
| `decision/unified/strategies/base.py` | 新規 | BaseStrategy |
| `decision/unified/strategies/in_strategy_consensus.py` | 新規 | 戦略内統合 |
| `decision/unified/strategies/scalp.py` | 新規 | ScalpStrategy |
| `decision/unified/strategies/short_mid.py` | 新規 | ShortMidStrategy |
| `decision/unified/strategies/swing.py` | 新規 | SwingStrategy |
| `decision/unified/strategy_pool.py` | 新規 | StrategyPool |
| `decision/unified/strategy_selector.py` | 新規 | StrategySelector |
| `decision/unified/trade_bot.py` | 修正 | `_generate_signal_convergent()`追加 |
| `decision/unified/signal_consolidator.py` | 修正 | ConsolidatedSignal拡張 |

---

*作成日: 2026-02-04*
*参照: plans/unified_trade_bot_改善案2.md*
