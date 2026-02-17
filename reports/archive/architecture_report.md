# AutoTraderV4 アーキテクチャ評価レポート

日付: 2026-02-07

---

## 1. エグゼクティブサマリー

AutoTraderV4は、FXトレーディング自動化システムである。レガシーシグナル生成器から輻輳型（Convergent）アーキテクチャへの移行途上にあり、3つのアーキテクチャが並存している。設定の分散（TP/SL比率が6箇所に存在）が最大のアーキテクチャ上の問題であり、意図した設定値が実際に反映されない危険性がある。レガシーコードの残存、設定クラスの重複、責務の曖昧な境界が保守性・拡張性を低下させている。

**重大度別の問題数:**
- CRITICAL: 2件（設定分散、3アーキテクチャ並存）
- HIGH: 4件（設定クラス重複、レガシー残存、enum不統一、SL/TP計算多重化）
- MEDIUM: 3件（テスタビリティ不足、ポジション管理未統合、名前空間の混乱）

---

## 2. モジュール依存関係図

```
[core/]
  entities.py  ─── Candle, Signal, Position, Trade, AccountInfo, SymbolInfo
  enums.py     ─── Timeframe, SignalType, ConfidenceLevel, MarketRegime,
  |                 TradingStrategyMode, TradingMode, OrderStatus
  interfaces/  ─── DecisionEngineInterface, Guard, DataProvider,
                    TradeExecutor, IndicatorCalculator, PositionSizerProtocol

[config/]
  settings.py         ─── Settings(BaseSettings), StrategyConfig (レガシー)
  trading_params.py   ─── TradingParams (spread, sl, tp等の単一ソース)
  scoring_config.py   ─── ScoringConfig, TimeframeScoring
  timeframe_preset.py

[calculator/]
  technical/     ─── trend, momentum, volatility, price_structure, price_action
  features/      ─── mtf_features, trend_features, divergence, volatility, regime_detector
  market_structure/ ── swing_analyzer, structure_analyzer, liquidity_analyzer
  precompute.py

[constraint/]
  hard_guard.py  ─── HardGuard (証拠金, 日次損失, ポジション上限, 取引時間, データ品質, ニュース)
  soft_guard.py  ─── SoftGuard (スプレッド, セッション, ボラ, 直近成績, MTF矛盾, トレンド強度)
  filters/       ─── trend_filter, adx_filter

[decision/]
  signal_generator.py        ─── SignalGenerator, OptimizedSignalGenerator,
  |                              LLMEnhancedSignalGenerator (レガシー)
  decision_engine.py         ─── DecisionEngine (レガシー)
  improved_signal_generator.py
  high_win_rate_generator.py
  m1_scalper.py
  short_term_generator.py
  conservative_scalper.py
  monthly_target_strategy.py
  trend_follow_generator.py
  multi_strategy_manager.py
  confidence_calculator.py
  exit_manager.py
  partial_close.py
  |
  unified/
    trade_bot.py          ─── UnifiedTradeBot (メインエントリーポイント)
    config.py             ─── UnifiedBotConfig, EvaluatorConfig, ConsolidatorConfig等
    trading_config.py     ─── TradingConfig, ModeConfig (SCALP/SHORT_MID/SWING)
    mode_selector.py      ─── TradingModeSelector, TradingPlan, MODE_PLANS
    mode_monitor.py       ─── ModeMonitor, ModeConfig (SCALPING_CONFIG等)
    mode_aware_consensus.py ── ModeAwareScoreConsensus
    multi_mode_controller.py ── MultiModeController
    timeframe_evaluator.py ── TimeframeEvaluator (SL/TP独自計算あり)
    timeframe_router.py   ─── TimeframeRouter
    signal_consolidator.py ── SignalConsolidator (SL/TP統合計算あり)
    strength_calculator.py ── IndicatorStrengthCalculator
    position_manager.py   ─── PositionManager
    position_sizer.py     ─── PositionSizer
    position_aggregator.py ── PositionAggregator
    entry_resolver.py     ─── EntryTimeframeResolver
    strategy_pool.py      ─── StrategyPool
    strategy_selector.py  ─── StrategySelector
    strategies/
      base.py     ─── BaseStrategy (SL/TP計算あり)
      scalp.py    ─── ScalpStrategy (TIMEFRAMES.tp_sl_ratio_range)
      short_mid.py ── ShortMidStrategy (TIMEFRAMES.tp_sl_ratio_range)
      swing.py    ─── SwingStrategy (TIMEFRAMES.tp_sl_ratio_range)
      types.py    ─── StrategyTimeframes, ProposedTrade, EdgeScoreComponents
      in_strategy_consensus.py ── InStrategyConsensus

[backtest/]
  engine.py      ─── BacktestEngine (レガシー), UnifiedBacktestEngine,
  |                   ParallelMultiTFBacktestEngine
  runner.py      ─── BacktestRunner (run, run_unified, run_walk_forward等)
  data_loader.py, indicators.py, metrics.py, state.py, simulator.py
  strategy_factory.py, formatters.py, config.py
  filters/       ─── session_filter, volatility_filter, event_filter, filter_manager
  adapters/      ─── cli.py, webui.py

[adapters/]
  database/  ─── connection, models, repositories
  ollama/    ─── client, schemas, prompts

[web/]
```

**依存方向（正常）:**
```
adapters → core
config → core
calculator → core
constraint → core
decision → core, config, calculator, constraint
backtest → decision, calculator, config
web → backtest, config
```

**問題のある依存:**
- `decision/unified/trading_config.py` は独自のTradingMode enumを定義 → `core/enums.py`のTradingStrategyModeと重複
- `decision/unified/mode_monitor.py` は独自のModeConfigを定義 → `trading_config.py`のModeConfigと重複
- `config/settings.py` のStrategyConfig と `decision/unified/strategies/base.py` のStrategyConfig が同名別物

---

## 3. 設定管理の問題と提案

### 3.1 TP/SL比率の設定箇所（6箇所 - CRITICAL）

| # | ファイル | クラス/変数 | 値の例（Scalp） | 使用タイミング |
|---|---------|------------|-----------------|---------------|
| 1 | `mode_selector.py` | `MODE_PLANS.tp_sl_ratio_range` | (1.0, 1.3) | ModeSelector.select() |
| 2 | `strategies/scalp.py` | `TIMEFRAMES.tp_sl_ratio_range` | (1.0, 1.3) | BaseStrategy._calculate_sl_tp() |
| 3 | `timeframe_evaluator.py` | `_calculate_sl_tp()` 内ハードコード | 1.0 (M1) | TimeframeEvaluator.evaluate() |
| 4 | `trading_config.py` | `MODE_CONFIGS.tp_sl_ratio_min/max` | 1.5 / 2.0 | TradingConfig.get_recommended_tp_sl_ratio() |
| 5 | `config.py` | `UnifiedBotConfig.tp_sl_ratio` | 1.0 | レガシー互換 |
| 6 | `config/trading_params.py` | `TradingParams.default_tp_pips/sl_pips` | 40.0 / 20.0 | フォールバック |

**問題:** 設定#1, #2は同じ値で同期済みだが、#4は全く異なる値（1.5-2.0 vs 1.0-1.3）。#3はハードコードされた独自の辞書。#5はレガシー。どの値が実際に使われるかはアーキテクチャモード（convergent/new/legacy）によって異なる。

**現在のフロー（convergent）:**
1. `TimeframeEvaluator._calculate_sl_tp()` で#3のハードコード比率を使用
2. `BaseStrategy._calculate_sl_tp()` で#2の範囲内にクランプ
3. 結果が`ProposedTrade`のsl_pips/tp_pipsになる

**問題点:** #1（TradingPlan）と#4（TradingConfig）は輻輳型では使用されない死んだ設定。設定変更時に「どこを変えるべきか」が不明瞭。

### 3.2 設定クラスの重複（HIGH）

| 概念 | 定義箇所1 | 定義箇所2 | 問題 |
|------|----------|----------|------|
| トレードモード | `core/enums.py:TradingStrategyMode` (SCALPING/DAY_TRADE/SWING) | `trading_config.py:TradingMode` (SCALP/SHORT_MID/SWING) | 名前も値も異なる |
| モード設定 | `trading_config.py:ModeConfig` | `mode_monitor.py:ModeConfig` | 同名別物、属性が異なる |
| 戦略設定 | `config/settings.py:StrategyConfig` | `strategies/base.py:StrategyConfig` | 同名別物、用途が完全に異なる |

### 3.3 改善提案

**提案A: 設定の単一ソース化（推奨）**
1. TP/SL比率は `strategies/*.py` の `TIMEFRAMES.tp_sl_ratio_range` のみを正とする
2. `timeframe_evaluator.py` のハードコード辞書を削除し、`plan` パラメータ（または戦略から渡される値）を使用
3. `trading_config.py` の `TradingConfig` は使われていないなら削除
4. `mode_selector.py` の `MODE_PLANS` も同様に戦略のTIMEFRAMESから導出

**提案B: 設定レジストリパターン**
- 1つの `StrategyRegistry` クラスで全設定を一元管理
- 各戦略はレジストリから設定を取得
- 設定変更は1箇所のみで行う

---

## 4. データフロー分析

### 4.1 輻輳型（Convergent）アーキテクチャのフロー

```
UnifiedTradeBot.generate_signal()
  |
  v
[1] RegimeDetector.detect()  → regime_result
  |
  v
[2] RiskManager.can_trade()  → can_trade, reason
  |
  v
[3] StrategyContext構築
  |  (regime, price, spread, hour, has_position, current_strategy)
  |
  v
[4] StrategyPool.evaluate_all(context, tf_data, candle)
  |    |
  |    +-> ScalpStrategy.evaluate()
  |    |     +-> InStrategyConsensus.consolidate()
  |    |     +-> BaseStrategy._calculate_edge_components()
  |    |     +-> BaseStrategy._calculate_sl_tp()
  |    |
  |    +-> ShortMidStrategy.evaluate()
  |    +-> SwingStrategy.evaluate()
  |    |
  |    v
  |  PoolEvaluationResult (全ProposedTrade)
  |
  v
[5] StrategySelector.choose(pool_result, context)
  |    → SelectionResult (best proposal + reasoning)
  |
  v
[6] PositionSizer.calculate(sizing_context)
  |    → lot size
  |
  v
[7] ConsolidatedSignal (最終出力)
```

### 4.2 レガシーフロー（未使用だが残存）

```
UnifiedTradeBot._generate_signal_legacy()
  |
  v
[1] 各TimeframeEvaluator.evaluate()  → TimeframeSignal
  |
  v
[2] SignalConsolidator.consolidate()
  |    → _apply_majority_rule / _apply_weighted_rule
  |    → _calculate_consolidated_sl_tp()
  |
  v
[3] ConsolidatedSignal
```

### 4.3 new（中間）フロー（未使用だが残存）

```
UnifiedTradeBot._generate_signal_new()
  |
  v
[1] ModeSelector.select() → TradingPlan
[2] TimeframeRouter.route()
[3] TimeframeEvaluator.evaluate() x N
[4] ModeAwareScoreConsensus.consolidate()
[5] ConsolidatedSignal
```

### 4.4 データフロー上の問題

1. **SL/TP計算の多段パイプライン**: TimeframeEvaluator → BaseStrategy → SignalConsolidator と3段階で計算・上書きが発生。各段階で異なるロジックが適用される。

2. **StrategyContextの暗黙的依存**: `regime_result`はRegimeDetectorから取得するが、RegimeDetectorはH1データのみ参照。他の時間足のレジームは考慮されない。

3. **ConsolidatedSignalの肥大化**: 輻輳型で追加されたフィールド（chosen_strategy_id, edge_score, edge_components, all_proposals）はレガシーとの互換性のために同じクラスに詰め込まれている。

---

## 5. 拡張性・保守性の評価

### 5.1 新しい戦略の追加

**現状（良好）:** 輻輳型では`BaseStrategy`を継承し、`strategy_id`、`timeframes`、`_get_regime_fit_factor`を実装するだけで新戦略追加可能。`StrategyPool`が自動検出する設計。

**問題点:**
- `StrategyPool.__init__`で3戦略がハードコードされている可能性がある（要確認）
- `mode_selector.py`の`MODE_PLANS`と`trading_config.py`の`MODE_CONFIGS`にも対応するエントリが必要

### 5.2 インジケーターの追加

**現状（良好）:** `calculator/technical/`配下に独立したモジュールとして追加可能。`PrecomputeEngine`経由で計算結果がDataFrameに格納される。

**問題点:**
- `TimeframeEvaluator._calculate_score()`に10個以上のスコアリングメソッドがハードコード。新インジケーター追加時に`_score_xxx`メソッドと呼び出し部分の両方を修正必要。
- Strategyパターン（各スコアリングを独立クラスに抽出）の方が拡張性が高い

### 5.3 テスタビリティ

**問題点:**
- `UnifiedTradeBot`が多数のコンポーネントを直接生成（`_init_new_components`）。DIコンテナ不使用のため、テスト時のモック差替えが困難
- `TimeframeEvaluator._calculate_sl_tp()`にハードコードされた辞書があり、パラメータ変更テストが不可能
- `BacktestEngine`に3つの異なるエンジン（BacktestEngine, UnifiedBacktestEngine, ParallelMultiTFBacktestEngine）が存在し、どれをテストすべきか不明

### 5.4 レガシーコードの影響

**残存レガシーファイル（decision/直下、unified/外）:**

| ファイル | 行数推定 | 使用状況 |
|---------|---------|---------|
| signal_generator.py | 大 | LLMEnhanced版はrunner経由で使用可能 |
| improved_signal_generator.py | 中 | 不明（要調査） |
| high_win_rate_generator.py | 中 | 不明 |
| m1_scalper.py | 中 | 不明 |
| short_term_generator.py | 中 | 不明 |
| conservative_scalper.py | 中 | 不明 |
| monthly_target_strategy.py | 中 | 不明 |
| trend_follow_generator.py | 中 | 不明 |
| multi_strategy_manager.py | 中 | 不明 |
| decision_engine.py | 中 | BacktestEngine(レガシー)から使用 |
| confidence_calculator.py | 中 | 不明 |
| exit_manager.py | 中 | 不明 |
| partial_close.py | 中 | 不明 |

**問題:** レガシーファイルが13個存在し、unified/内のファイル数（約20個）に匹敵する。どれが使われているか把握困難。

---

## 6. 改善提案（優先度・工数付き）

### CRITICAL優先度

| # | 提案 | 期待効果 | 工数 |
|---|------|---------|------|
| C1 | **TP/SL比率設定の単一ソース化** | 設定ミスによるパフォーマンス低下を根本排除 | 2-3日 |
| | `strategies/*.py`のTIMEFRAMES.tp_sl_ratio_rangeを正とし、TimeframeEvaluatorのハードコード辞書を削除。TradingConfig.MODE_CONFIGSのtp_sl_ratio_min/maxとmode_selector.pyのMODE_PLANSも戦略から導出するか削除。 | | |
| C2 | **アーキテクチャモードの整理** | コードベース理解の容易化、バグ発生リスク低減 | 3-5日 |
| | `use_convergent_architecture`のみを残し、`use_new_architecture`フラグとレガシーパスを削除。`_generate_signal_legacy()`、`_generate_signal_new()`を削除。 | | |

### HIGH優先度

| # | 提案 | 期待効果 | 工数 |
|---|------|---------|------|
| H1 | **Enum統一** | 混乱排除、型安全性向上 | 1日 |
| | `trading_config.py:TradingMode`を削除し、`core/enums.py:TradingStrategyMode`に統一。SCALP↔SCALPING、SHORT_MID↔DAY_TRADEのマッピングを明確化。 | | |
| H2 | **同名クラスの解消** | import時の混乱排除 | 1日 |
| | `config/settings.py:StrategyConfig` → `LegacyStrategyConfig`にリネーム。`mode_monitor.py:ModeConfig` → `MonitorModeConfig`にリネーム。 | | |
| H3 | **レガシーdecision/ファイルの棚卸し** | 保守コスト低減 | 2日 |
| | 各ファイルの使用状況を調査し、未使用なら`_legacy/`サブディレクトリに移動またはarchive。 | | |
| H4 | **SL/TP計算の責務明確化** | 計算結果の予測可能性向上 | 2日 |
| | TimeframeEvaluatorはATRベースの「生SL/TP」のみ計算。BaseStrategyが戦略のTP/SL比率でクランプ。SignalConsolidatorの統合計算は輻輳型では不要なので削除対象。 | | |

### MEDIUM優先度

| # | 提案 | 期待効果 | 工数 |
|---|------|---------|------|
| M1 | **UnifiedTradeBotのDI導入** | テスタビリティ向上 | 2-3日 |
| | `_init_new_components()`をコンストラクタインジェクションに変更。 | | |
| M2 | **TimeframeEvaluatorのスコアリング拡張性改善** | 新インジケーター追加容易化 | 3日 |
| | 各スコアリングメソッドをScorerインターフェースの実装として抽出（Strategyパターン）。 | | |
| M3 | **BacktestEngine統一** | テスト・保守コスト低減 | 3日 |
| | 3エンジンを1つに統合し、設定で動作を切り替える。 | | |

### 推奨実施順序

```
Phase 1 (1週間): C1 → H1 → H2
  設定管理を整理し、最も危険な問題を解消

Phase 2 (1週間): C2 → H3 → H4
  レガシーコードを整理し、アーキテクチャを単純化

Phase 3 (2週間): M1 → M2 → M3
  テスタビリティと拡張性を改善
```

---

## 付録A: 設定のデータフロー詳細図

```
[TP/SL比率の流れ - 輻輳型アーキテクチャ]

ScalpStrategy.TIMEFRAMES.tp_sl_ratio_range = (1.0, 1.3)
  |
  |  TimeframeEvaluator._calculate_sl_tp():
  |    tp_sl_ratios = {"M1": 1.0, "M5": 1.05, ...}  ← ハードコード
  |    plan.get_recommended_tp_sl_ratio() で上書き可能
  |    → sl_pips, tp_pips (TimeframeSignalに格納)
  |
  v
BaseStrategy._calculate_sl_tp():
  primary_signal.sl_pips, tp_pips を取得
  self.timeframes.tp_sl_ratio_range でクランプ
  → 最終 sl_pips, tp_pips (ProposedTradeに格納)
  |
  v
ConsolidatedSignal.sl_pips, tp_pips
```

## 付録B: 使用されていない設定（Dead Configuration）

| 設定 | ファイル | 理由 |
|------|---------|------|
| `TradingConfig.MODE_CONFIGS` | trading_config.py | 輻輳型では参照されない |
| `TradingConfig.default_rr_ratio` | trading_config.py | 同上 |
| `UnifiedBotConfig.tp_sl_ratio` | config.py | レガシー互換、輻輳型では不使用 |
| `UnifiedBotConfig.min_adx` | config.py | RegimeDetectorに移行済み |
| `UnifiedBotConfig.require_htf_trend` | config.py | 同上 |
| `TradingPlan.tp_sl_ratio_range` | mode_selector.py | 輻輳型では使われない |
| `ModeMonitor` の SL/TP計算 | mode_monitor.py | MultiModeController用だが輻輳型では未使用 |
