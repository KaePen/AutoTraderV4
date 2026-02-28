# V2 Trading Engine: Market Structure State Machine

## Context

既存のトレードロジック（V1）は「マルチTFコンセンサス投票 + インジケーター加点方式」で、
7つの時間足を並列評価し、重み付きスコアで方向を決定する。このアプローチは：
- パラメータが多すぎ（閾値、重み、戦略別設定）→ 過学習リスク
- インジケーター依存（遅行指標に基づく判断）
- 複雑な合意形成（15+段階のパイプライン）

V2は根本的に異なるアプローチで、**市場構造の状態遷移**に基づくトレードを行う。

## Design Philosophy

**"Less is More" - シンプルなルール、深い構造理解**

| 項目 | V1 (既存) | V2 (新規) |
|------|-----------|-----------|
| 時間足 | 7 (M1-D1) | 3 (H1/H4/D1) |
| 判断方式 | スコア加算投票 | 状態マシン遷移 |
| エントリー根拠 | インジケーター合計点 | 市場構造 (BOS/CHoCH) + プライスアクション |
| SL設定 | ATR倍数 | スイングポイント構造ベース |
| 戦略選択 | 3戦略同時評価 → 最高スコア | レジーム → 1戦略自動選択 |
| トレード頻度 | 高（スコア閾値さえ超えれば） | 低（構造条件一致時のみ） |

---

## Architecture

```
MarketContextBuilder  ← H1指標 + H4/D1構造データ
        │
        ▼
  RegimeClassifier    ← ヒステリシス付き状態マシン
   (TRENDING / RANGING / QUIET / VOLATILE)
        │
        ▼
 StrategyDispatcher   ← レジームに応じた1戦略のみ選択
    ┌───┼───┬───┐
    ▼   ▼   ▼   ▼
 Trend Range Break NoTrade
 Follow Revert out  (VOLATILE時)
    └───┼───┴───┘
        ▼
   V2RiskManager      ← 構造ベースSL/TP + レジーム別トレーリング
        │
        ▼
    Signal出力         ← core/entities.Signal（V1と同一型）
```

---

## Implementation Plan

### Phase 1: Core Framework + RegimeClassifier
**ファイル作成:**
- `autotrader/decision/v2/__init__.py`
- `autotrader/decision/v2/config.py` - 全Config dataclass
- `autotrader/decision/v2/market_context.py` - MarketContext + Builder
- `autotrader/decision/v2/regime_classifier.py` - 状態マシン

**テスト:**
- `tests/unit/decision/v2/test_market_context.py`
- `tests/unit/decision/v2/test_regime_classifier.py`

### Phase 2: Strategies
**ファイル作成:**
- `autotrader/decision/v2/strategies/__init__.py`
- `autotrader/decision/v2/strategies/base.py` - V2StrategyBase + V2EntrySignal
- `autotrader/decision/v2/strategies/trend_follow.py` - BOS確認 + プルバック
- `autotrader/decision/v2/strategies/range_revert.py` - レンジ端逆張り
- `autotrader/decision/v2/strategies/breakout.py` - スクイーズ解消ブレイクアウト

**テスト:**
- `tests/unit/decision/v2/test_trend_follow.py`
- `tests/unit/decision/v2/test_range_revert.py`
- `tests/unit/decision/v2/test_breakout.py`

### Phase 3: Risk Manager + TradeBot + Integration
**ファイル作成:**
- `autotrader/decision/v2/risk_manager.py` - 構造ベースSL/TP/トレーリング
- `autotrader/decision/v2/trade_bot.py` - V2TradeBot (SignalGeneratorProtocol)
- `autotrader/decision/v2/strategy_dispatcher.py` - レジーム→戦略ルーティング

**既存ファイル修正:**
- `autotrader/backtest/engine.py` - V2BotAdapter追加
- `autotrader/backtest/config.py` - engine_version フィールド追加
- `autotrader/backtest/runner.py` - V1/V2切り替えロジック
- `scripts/run_backtest.py` - `--engine v2` オプション追加

**テスト:**
- `tests/unit/decision/v2/test_risk_manager.py`
- `tests/unit/decision/v2/test_trade_bot.py`
- `tests/integration/test_v2_backtest.py`

### Phase 4: Initial Backtest + Iteration
- USDJPY 2020-2025でバックテスト実行
- V1 vs V2 比較レポート生成
- 結果分析 → パラメータ調整 → 再テスト（3-5ラウンド）

---

## Strategy Details

### TrendFollow (TRENDING regime)
- **エントリー**: H4構造がBULLISH/BEARISH + H1でプルバック(EMA-50近傍 or swing level) + 反転足確認 + BOS直近10足以内
- **SL**: H4直近スイングポイント - ATR×0.3バッファ (max 50pips)
- **TP**: 次のH4構造レベル or 2R距離 (min 1.5R)
- **確信度**: 構造整合+BOS鮮度+反転足品質+RSI/MACDモメンタム

### RangeRevert (RANGING regime)
- **エントリー**: BB位置<0.15(売り>0.85) + サポート/レジスタンス到達 + 反転足 or 流動性グラブ
- **SL**: レンジ外端 - ATR×0.5 (max 30pips)
- **TP**: 反対側レンジ境界の70% (min 1.2R)
- **確信度**: 流動性グラブ+反転足品質+RSIダイバージェンス+BB極値度

### Breakout (QUIET regime)
- **エントリー**: QUIET5足以上 + BBスクイーズ解消 + レンジブレイク + BOS確認 + ADX>20
- **SL**: コンソリデーションレンジ下端 - ATR×0.3
- **TP**: 測定目標値(レンジ幅×1.5) or H4構造レベル (min 1.5R)
- **確信度**: BOS確認+ADX強度+BB拡大率+D1構造整合

### NoTrade (VOLATILE regime + ブロック条件)
- VOLATILE時は全スキップ
- 低流動性時間帯 (UTC 22-3)
- スプレッド>3pips
- 連敗3回以上

---

## RegimeClassifier State Machine

ヒステリシス（遷移に複数足の確認を要求）で誤分類を防止:

| From → To | 必要確認足数 | 条件 |
|-----------|------------|------|
| QUIET → TRENDING | 3 | ADX>25, abs(ma_align)>0.3, BOS直近 |
| QUIET → RANGING | 2 | ADX<20, norm_ATR 0.7-1.3 |
| QUIET → VOLATILE | 1 (即時) | norm_ATR>1.8 |
| TRENDING → RANGING | 4 | ADX<20に低下, CHoCH検出 |
| TRENDING → VOLATILE | 2 | norm_ATR>1.5, ADX急変 |
| RANGING → TRENDING | 3 | ADX>25に上昇, BOS確認 |
| RANGING → VOLATILE | 1 (即時) | norm_ATR>1.8 |
| VOLATILE → Any | 3-5 | 危険→安全は慎重に |

**原則**: 危険状態(VOLATILE)への遷移は即座、安全状態への復帰は慎重。

---

## V1/V2 切り替え設計

**既存コード変更最小限:**
```python
# run_backtest.py に --engine v2 追加
# runner.py で:
if config.engine_version == "v2":
    generator = V2BotAdapter(V2BotConfig())
else:
    generator = UnifiedBotAdapter(existing_bot)  # V1
```

V1のコードは一切変更しない。V2は `decision/v2/` に独立して存在し、
`backtest/engine.py` の SignalGeneratorProtocol 経由で統合。

---

## Reuse (既存資産活用)

| 既存モジュール | V2での使い方 |
|--------------|-------------|
| `calculator/technical/*` | H1指標計算（RSI, MACD, ATR, BB, ADX） |
| `calculator/market_structure/*` | H4/D1構造分析（BOS/CHoCH/Swing/Liquidity） |
| `calculator/features/regime_detector.py` | 参考（V2は独自分類器だが同じ入力信号） |
| `calculator/features/volatility_features.py` | BBスクイーズ、正規化ATR |
| `calculator/technical/price_action.py` | ローソク足パターン検出 |
| `core/entities.py` | Signal, Trade, Position, Candle |
| `constraint/hard_guard.py` | マージン・ポジション上限チェック |
| `backtest/simulator.py` | TradeSimulator（変更なし） |
| `backtest/metrics.py` | MetricsCalculator（変更なし） |
| `backtest/data_loader.py` | DataLoader（変更なし） |
| `calculator/precompute.py` | PrecomputeEngine（変更なし） |

---

## Critical Files

**新規作成 (12ファイル):**
- `autotrader/decision/v2/config.py`
- `autotrader/decision/v2/market_context.py`
- `autotrader/decision/v2/regime_classifier.py`
- `autotrader/decision/v2/strategy_dispatcher.py`
- `autotrader/decision/v2/risk_manager.py`
- `autotrader/decision/v2/trade_bot.py`
- `autotrader/decision/v2/strategies/base.py`
- `autotrader/decision/v2/strategies/trend_follow.py`
- `autotrader/decision/v2/strategies/range_revert.py`
- `autotrader/decision/v2/strategies/breakout.py`

**修正 (4ファイル):**
- `autotrader/backtest/engine.py` - V2BotAdapter追加
- `autotrader/backtest/config.py` - engine_version追加
- `autotrader/backtest/runner.py` - V1/V2切り替え
- `scripts/run_backtest.py` - --engine引数追加

**テスト (8ファイル):**
- Phase 1-3の各コンポーネント単体テスト + 統合テスト

---

## Verification

1. **単体テスト**: 各コンポーネントの正常動作（pytest）
2. **統合テスト**: V2エンジンが Signal を正しく生成しSimulatorで実行できること
3. **バックテスト比較**: V1 vs V2 同一期間・同一データで性能比較
4. **Walk-forward検証**: 2015-2019訓練 → 2020-2025検証
5. **レジーム分析**: V2固有メトリクス（レジーム分布、戦略別WR/PF）

---

## Iteration Strategy

Phase 4以降、以下のサイクルで反復改善:

```
バックテスト実行 → 結果分析 → 弱点特定 → パラメータ/ロジック調整 → 再テスト
```

各ラウンドで記録:
- レジーム分布と遷移頻度
- 戦略別Win Rate / Profit Factor
- 負けトレードの原因分析
- NoTrade期間の妥当性

目標: 反復3-5ラウンドで安定した正のPFを達成。
