# トレードロジック可視化計画

## コンテキスト

AutoTraderV4 のトレードロジックは複数の層に分かれており、コードを読むだけでは全体像の把握が困難。
draw.io MCP を用いてアーキテクチャ・データフロー・シグナル生成パイプラインを視覚化することで、
システム理解・デバッグ・機能追加の効率を向上させる。

---

## 作成するダイアグラム（3枚）

### 図1: システムアーキテクチャ図
**クラス間の依存関係と責務の全体像**

描画対象コンポーネント:
- `LiveTradingEngine` (live/engine.py) - 最上位制御
- `UnifiedTradeBot` (decision/unified/trade_bot.py) - シグナル生成
- `ModeAwareScoreConsensus` (decision/unified/mode_aware_consensus.py) - スコア統合
- `TimeframeEvaluator` x6 (decision/unified/timeframe_evaluator.py) - TF別評価
- `MarketRegimeDetector` (calculator/features/regime_detector.py) - レジーム検出
- `TradingModeSelector` (decision/unified/mode_selector.py) - モード選択
- `TimeframeRouter` (decision/unified/timeframe_router.py) - TFセット決定
- `PositionSizer` (decision/unified/position_sizer.py) - ロット計算
- `SoftGuard` (constraint/soft_guard.py) - ペナルティ
- `RiskManager` (decision/unified/trade_bot.py内) - リスク管理
- `PositionManager` (decision/unified/position_manager.py) - ポジション状態
- `MT5TradeExecutor` (adapters/mt5/trade_executor.py) - 発注実行
- `MT5DataProvider` (adapters/mt5/data_provider.py) - データ取得
- `MT5ConnectionManager` (adapters/mt5/connection.py) - MT5接続

### 図2: シグナル生成〜発注実行フロー
**1ティック内のデータ処理シーケンス**

```
MT5 → DataProvider → set_market_data()
                    ↓
              generate_signal()
                    ├ RiskManager.can_trade()
                    ├ RegimeDetector.detect()
                    ├ ModeSelector.select()
                    ├ TimeframeRouter.route()
                    ├ TimeframeEvaluator[M1/M5/M15/H1/H4/D1].evaluate()
                    ├ ModeAwareScoreConsensus.consolidate()
                    ├ HTF Trend Alignment Check
                    ├ SoftGuard.check()
                    └ PositionSizer.calculate()
                    ↓
              ConsolidatedSignal → Signal
                    ↓
              _execute_entry()
                    ├ get_open_positions()
                    ├ PositionSizer.calculate()
                    ├ MT5TradeExecutor.open_position_async()
                    └ on_trade_executed()
```

### 図3: コンセンサス判定ロジック
**ModeAwareScoreConsensus の判定フローチャート**

```
各TF の TimeframeSignal
    ↓
役割別重み × 方向強度でスコア計算
    ↓
BUYスコア vs SELLスコア比較
    ↓
スコア < 閾値? → HOLD
    ↓
TF方向一致率 < 最低閾値? → HOLD
    ↓
競合シグナル比率チェック → 拮抗時HOLD
    ↓
ConsensusResult(direction, score, confidence)
```

---

## 実装手順

1. `mcp__drawio__open_drawio_mermaid` を3回呼び出し、各ダイアグラムを開く
2. **図1**: `classDiagram` または `graph TD` でアーキテクチャ図
3. **図2**: `sequenceDiagram` でデータフロー
4. **図3**: `flowchart TD` でコンセンサス判定ロジック

## 検証方法

- draw.io エディタが開き、ノードが正しく配置されること
- 依存関係の矢印が実際のコードの呼び出し関係と一致していること
- 凡例・ラベルが日本語で読みやすいこと
