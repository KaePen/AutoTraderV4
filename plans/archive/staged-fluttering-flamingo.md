# トレードボット改善計画: 勝率60%・収益率15%達成

## 概要

現在のAutoTraderV4トレードボットは十分な勝率と収益を達成できていない。
15年分のバックテストデータを活用し、過剰フィッティングを避けながら、
勝率60%・年間収益率15%を目指す。

## 目標

| 指標 | 現状（推定） | 目標 |
|------|-------------|------|
| 勝率 | 45-50% | 60%+ |
| 年間収益率 | 5-8% | 15%+ |
| プロフィットファクター | 1.0-1.2 | 1.6+ |
| 最大DD | 15-20% | 10%以下 |
| トレード数/年 | 50-100 | 150+ |

## 制約条件

- LLMなしモードで実行（自動判定のみ）
- 過剰フィッティング回避（訓練/検証/テスト分割）
- 短期足（M1/M5）も含む多様な時間足で収益

## 発見された問題

### 1. TP/SL比率が1:1（最重要）

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py`

```python
# 現在のコード
tp_pips = sl_pips  # 1:1固定
```

**影響**: 勝率50%でもブレークイーブン、スプレッド・スリッページで損失

### 2. スコア閾値の不整合

- `TimeframeEvaluator.MIN_SCORES`: 絶対値（3.0～7.0）
- `ModeAwareScoreConsensus.MODE_THRESHOLDS`: 別の閾値系
- **問題**: 同じスコア値でも意味が異なる

### 3. 短期足戦略の不十分さ

- `enable_scalping`オプションはあるが戦略詳細が不足
- 時間帯フィルターなし
- ノイズ対策が弱い

## データ分割方針

```
16年分データ（2010-2025）:
├── 訓練期間 (2010-2017): 8年 - パラメータ最適化
├── 検証期間 (2018-2021): 4年 - ハイパーパラメータ調整
└── テスト期間 (2022-2025): 4年 - 最終評価（触れない）
```

## 実装計画

### Phase 1: TP/SL比率改善（最優先・即効性高）

**期待効果**: 勝率+5-10%, 収益率+5%

#### 1-1. ダイナミックTP/SL計算

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py`

```python
def _calculate_sl_tp(
    self,
    row: pd.Series,
    strength: IndicatorStrength,
    plan: TradingPlan | None = None,
) -> tuple[float, float]:
    """ダイナミックSL/TP計算"""
    atr = row.get("atr_14")
    if atr is None or pd.isna(atr):
        return 20.0, 30.0

    atr_pips = atr * 100  # USDJPY

    # 時間足別SL係数
    sl_mult = {
        "M1": 1.0, "M5": 1.2, "M15": 1.5,
        "H1": 2.0, "H4": 2.5, "D1": 3.0
    }.get(self.timeframe, 1.5)

    sl_pips = atr_pips * sl_mult

    # モード別TP/SL比率
    tp_sl_ratio = 1.5  # デフォルト
    if plan:
        ratio_range = plan.tp_sl_ratio_range
        tp_sl_ratio = (ratio_range[0] + ratio_range[1]) / 2

    tp_pips = sl_pips * tp_sl_ratio

    # 制限
    sl_pips = max(8.0, min(sl_pips, 60.0))
    tp_pips = max(12.0, min(tp_pips, 120.0))

    return sl_pips, tp_pips
```

#### 1-2. TradingPlanをSL/TP計算に連携

**ファイル**: `src/autotrader/decision/unified/trade_bot.py`

- `_generate_signal_new`内でTimeframeEvaluatorにTradingPlanを渡す

### Phase 2: スコア閾値正規化

**期待効果**: 勝率+3-5%, 判定の一貫性向上

#### 2-1. 閾値を正規化比率で統一

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py`

```python
# 最大スコア15点に対する比率
NORMALIZED_MIN_SCORES: dict[str, float] = {
    "M1": 0.25,   # 3.75点 - 低い閾値でエントリー機会増
    "M5": 0.30,   # 4.5点
    "M15": 0.35,  # 5.25点
    "H1": 0.40,   # 6.0点
    "H4": 0.40,   # 6.0点
    "D1": 0.45,   # 6.75点
}
MAX_POSSIBLE_SCORE: float = 15.0
```

#### 2-2. コンセンサス閾値との整合

**ファイル**: `src/autotrader/decision/unified/mode_aware_consensus.py`

```python
# モード別閾値を正規化
MODE_THRESHOLDS_NORMALIZED: dict[TradingStrategyMode, float] = {
    TradingStrategyMode.SCALPING: 0.30,
    TradingStrategyMode.DAY_TRADE: 0.40,
    TradingStrategyMode.SWING: 0.50,
}
```

### Phase 3: コンセンサス重み最適化

**期待効果**: ノイズ低減、精度向上

#### 3-1. モード別重み付け

**ファイル**: `src/autotrader/decision/unified/mode_aware_consensus.py`

```python
ROLE_WEIGHTS_BY_MODE: dict[TradingStrategyMode, dict[TimeframeRole, float]] = {
    TradingStrategyMode.SCALPING: {
        TimeframeRole.PRIMARY: 2.0,   # M5
        TimeframeRole.ENTRY: 3.0,     # M1（重視）
        TimeframeRole.CONFIRM: 2.5,   # M15
        TimeframeRole.MANAGE: 1.0,
        TimeframeRole.OTHER: 0.2,
    },
    TradingStrategyMode.DAY_TRADE: {
        TimeframeRole.PRIMARY: 3.0,   # M15
        TimeframeRole.ENTRY: 2.5,     # M5
        TimeframeRole.CONFIRM: 2.0,   # H1, H4
        TimeframeRole.MANAGE: 1.5,
        TimeframeRole.OTHER: 0.3,
    },
    TradingStrategyMode.SWING: {
        TimeframeRole.PRIMARY: 3.5,   # H4
        TimeframeRole.ENTRY: 2.0,     # H1
        TimeframeRole.CONFIRM: 2.5,   # D1
        TimeframeRole.MANAGE: 1.5,
        TimeframeRole.OTHER: 0.3,
    },
}
```

### Phase 4: 短期足ノイズフィルター

**期待効果**: 偽シグナル削減、勝率向上

#### 4-1. M1/M5用フィルター追加

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py`

```python
def _apply_noise_filter(
    self,
    row: pd.Series,
    signal: TimeframeSignal,
) -> TimeframeSignal:
    """短期足用ノイズフィルター"""
    if self.timeframe not in ["M1", "M5"]:
        return signal

    # ADX最小値
    adx = row.get("adx", 0)
    if adx < 15:
        return signal._replace(direction=SignalType.HOLD)

    # ボラティリティ範囲
    atr_norm = row.get("normalized_atr", 1.0)
    if atr_norm < 0.5 or atr_norm > 2.0:
        return signal._replace(direction=SignalType.HOLD)

    return signal
```

### Phase 5: モード選択改善

**期待効果**: 適切な戦略選択、収益機会増加

#### 5-1. 時間帯考慮

**ファイル**: `src/autotrader/decision/unified/mode_selector.py`

```python
def _select_mode(
    self,
    regime: MarketRegime,
    volatility_level: float,
    htf_alignment: float,
    hour_utc: int,
) -> TradingStrategyMode:
    """時間帯を考慮したモード選択"""
    # アクティブ時間帯（東京・ロンドン・NY）
    is_active = hour_utc in range(0, 3) or hour_utc in range(7, 10) or hour_utc in range(13, 18)

    # 高ボラ + アクティブ時間 → SCALPING
    if regime == MarketRegime.HIGH_VOL and is_active:
        return TradingStrategyMode.SCALPING

    # 強トレンド + 高HTF整合 → SWING
    if regime == MarketRegime.TREND and abs(htf_alignment) > 0.7:
        return TradingStrategyMode.SWING

    # デフォルト
    return TradingStrategyMode.DAY_TRADE
```

## 対象ファイル一覧

| ファイル | 変更内容 | 優先度 |
|---------|---------|--------|
| `decision/unified/timeframe_evaluator.py` | TP/SL比率改善、閾値正規化、ノイズフィルター | Phase 1-2, 4 |
| `decision/unified/trade_bot.py` | TradingPlan連携 | Phase 1 |
| `decision/unified/mode_aware_consensus.py` | 閾値整合、重み最適化 | Phase 2-3 |
| `decision/unified/mode_selector.py` | 時間帯フィルター | Phase 5 |

## 検証方法

### Phase 1完了後
```bash
uv run python scripts/run_backtest.py --years 2018-2021 -v
```

### Phase 2-3完了後
```bash
uv run python scripts/run_backtest.py --years 2018-2021 --compare-consensus
```

### Phase 4-5完了後（スキャルピング検証）
```bash
uv run python scripts/run_backtest.py --years 2018-2021 --enable-scalping
```

### 最終検証（テスト期間）
```bash
# 訓練・検証期間で調整完了後のみ実行
uv run python scripts/run_backtest.py --years 2022-2025
```

## リスク対策

- **後方互換性**: デフォルトパラメータは現行維持
- **段階的検証**: 各Phase完了後にバックテスト実行
- **過学習監視**: 訓練/検証期間の結果乖離をチェック
- **テスト期間保護**: 2022-2025年データは最終評価まで触れない

## 実装順序

```
Phase 1 (TP/SL比率) ─── 最優先・即効性高
     │
     ├── 検証: 2018-2021年バックテスト
     │
     v
Phase 2 (閾値正規化) ─── 判定一貫性
     │
     v
Phase 3 (コンセンサス最適化) ─── 精度向上
     │
     v
Phase 4 (ノイズフィルター) ─── 偽シグナル削減
     │
     v
Phase 5 (モード選択改善) ─── 戦略適正化
     │
     v
最終検証 (2022-2025) ─── テスト期間評価
```
