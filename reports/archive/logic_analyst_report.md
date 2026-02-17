# シグナル生成ロジック深層分析レポート

## 1. エグゼクティブサマリー

現在のシグナル生成パイプラインを全コンポーネントにわたり分析した結果、以下の主要問題を特定した。

| 問題 | 影響度 | 期待改善効果 |
|------|--------|-------------|
| _calculate_scoreでstrengthが未使用 | 高 | 勝率+2-3% |
| SMCスコアリングが無効化されたまま | 中 | PF+0.05-0.10 |
| HTF逆行ペナルティの二重適用 | 高 | 勝率+1-2%, トレード数+10% |
| TP/SL比率の三重設定 | 中 | 収益率安定化 |
| RSIフィルターの矛盾的使用 | 中 | 勝率+1% |
| ノイズフィルターのADX閾値が甘すぎる | 低 | 勝率+0.5% |
| InStrategyConsensusのmin_confidence低すぎ | 中 | 勝率+1-2% |

**総合期待効果**: 勝率56% -> 60-62%, PF 1.01 -> 1.10-1.15

---

## 2. シグナル生成フロー図

```
[市場データ (OHLCV + インジケーター)]
        |
        v
[StrategyPool.evaluate_all] -- 全戦略を並列評価
        |
        +-- [ScalpStrategy.evaluate]   (M1/M5/M15/H1)
        +-- [ShortMidStrategy.evaluate] (M15/H1/H4)
        +-- [SwingStrategy.evaluate]    (H1/H4/D1)
                |
                v (各戦略内)
        [BaseStrategy.evaluate]
                |
                +-- _passes_pre_filters (時間帯・スプレッド)
                |
                +-- _evaluate_timeframes
                |       |
                |       +-- [TimeframeEvaluator.evaluate] (各TFで)
                |               |
                |               +-- IndicatorStrengthCalculator.calculate
                |               |       (RSI, MACD, Trend, Divergence, BB, Stoch)
                |               |
                |               +-- _calculate_score
                |               |       (トレンド+MACD+RSIフィルター+HTF整合)
                |               |
                |               +-- _determine_direction (MIN_SCORES閾値判定)
                |               +-- _apply_noise_filter (M1/M5のみ)
                |               +-- _calculate_sl_tp (ATRベース)
                |               |
                |               v
                |       TimeframeSignal
                |
                +-- InStrategyConsensus.consolidate
                |       (重み付きスコア統合、方向決定)
                |
                +-- _calculate_htf_factor (HTF整合係数)
                +-- _calculate_edge_components
                |       (confidence * margin * regime * cost * htf)
                |
                +-- _calculate_sl_tp (TP/SL比率範囲でクランプ)
                |
                v
        ProposedTrade (edge_score >= min_edge_score で有効)
                |
                v
[PoolEvaluationResult.best_proposal]
                |
                v
[ModeAwareScoreConsensus.check_entry_conditions]
        (entry_tf確定時のみ、モード別閾値判定)
                |
                v
[最終エントリー判断]
```

---

## 3. 各コンポーネントの詳細分析

### 3.1 IndicatorStrengthCalculator (strength_calculator.py)

**概要**: 7つのインジケーターを-1.0~1.0に正規化。

| インジケーター | 買い最大 | 売り最大 | 特徴 |
|---------------|---------|---------|------|
| RSI | +1.0 | -1.0 | 逆張り的（売られすぎ=買い） |
| MACD | +1.0 | -1.0 | ヒストグラム正規化、同圏1.2倍 |
| Trend | +1.0 | -1.0 | SMA整列+ADX調整 |
| Divergence | +0.8 | -0.8 | ブーリアン（あり/なし） |
| Bollinger | +1.0 | -1.0 | %B逆張り |
| Stochastic | +1.0 | -1.0 | K/Dクロス考慮 |
| ATR Norm | 0.0~1.0 | - | ボラティリティ参考値のみ |

**問題点**:
- `total_strength`は6指標の平均だが、各指標の重みが等しい
- ダイバージェンスは最大0.8で他の指標（最大1.0）より低い
- `IndicatorStrength`の`buy_strength`/`sell_strength`は`total_strength`の正負で判定するため、1指標の強い逆シグナルで全体が反転する可能性がある

### 3.2 TimeframeEvaluator._calculate_score (timeframe_evaluator.py:180-294)

**重大な問題: `strength`パラメータが完全に未使用**

```python
def _calculate_score(self, row, candle, strength):  # strengthを受け取る
    # ... 内部ではrowから直接トレンド/MACD/RSIを計算
    # strengthは一度も参照されない
```

`evaluate()`メソッドで`strength = self.calculator.calculate(row)`で計算した`IndicatorStrength`を渡しているが、`_calculate_score`は独自にrowからインジケーター値を直接読み取ってスコアリングしている。

**影響**: IndicatorStrengthCalculatorの正規化済み強度値（RSI/MACD/Trend/BB/Stoch/Divergence）が、実際のスコアリングに全く反映されていない。7つのインジケーターの統合的な強度情報が無駄になっている。

**スコアリングの実態**:
- トレンド（SMA20/50）+ MACD方向 = 基本スコア（最大7点）
- RSIフィルター（80超/20未満で拒否、範囲内+1点）
- HTF整合性（±5点）
- MAX_POSSIBLE_SCORE = 15.0 だが実際の最大は約12点程度

### 3.3 _score_rsi / _score_macd / _score_trend 等のメソッド群 (384-550行)

これらのメソッドは**存在するが `_calculate_score` から呼ばれていない**。`_evaluate_smc_factors`と同様のパターンで、呼び出し元がない。

具体的な分析:
- `_score_rsi`: RSI値に基づく3段階スコアリング（±1/2/3点）
- `_score_macd`: MACD方向+ADXで4段階
- `_score_trend`: SMA整列の多段階評価
- `_score_adx`: ADX強度の評価
- `_score_stochastic`: K/Dクロスパターン
- `_score_divergence`: ダイバージェンスの±3点
- `_score_bollinger`: %Bの逆張りスコア

**問題**: これらの精緻なスコアリングメソッドが使われておらず、`_calculate_score`内の簡易的なロジック（トレンド+MACD+RSIフィルターのみ）が実際に使われている。

### 3.4 HTF整合性の二重チェック問題

HTF整合性が**2箇所で独立にチェック**されている:

1. **TimeframeEvaluator._score_htf_alignment** (552-610行):
   - `_calculate_score`内から呼び出し
   - 逆トレンド検出時: -5.0点（実質的にシグナル半減）
   - 整合時: +2.0~4.0点

2. **BaseStrategy._calculate_htf_factor** (230-280行):
   - `evaluate`内でInStrategyConsensus後に呼び出し
   - HTF衝突 + htf_weight >= 0.5 の場合: factor = **0.0**（完全ブロック）
   - EdgeScoreComponentsの乗算因子として使用

**二重ペナルティの影響**: HTF逆行時、まずTimeframeEvaluatorでスコアが半減し、さらにBaseStrategy._calculate_htf_factorでedge_score自体が0になる。結果として、HTFとのわずかな不一致でもシグナルが完全に消失する。これは過度に保守的。

### 3.5 _calculate_sl_tp の三重設定

TP/SL計算は3段階で行われる:

1. **TimeframeEvaluator._calculate_sl_tp** (693-761行):
   - ATRベース計算（SLマルチプライヤー × ATR pips）
   - TF別固定TP/SL比率（M1:1.0, M5:1.05, ..., D1:1.25）
   - `plan.get_recommended_tp_sl_ratio()`で上書き可能
   - SL範囲制限: 10-50 pips

2. **BaseStrategy._calculate_sl_tp** (343-377行):
   - primary_tfのTimeframeSignalのSL/TPを使用
   - `StrategyTimeframes.tp_sl_ratio_range`でクランプ
   - Scalp: 1.0-1.3, ShortMid: 1.1-1.4, Swing: 1.2-1.6

3. **TradingPlan.get_recommended_tp_sl_ratio**:
   - `tp_sl_ratio_range`の中央値を返す
   - TimeframeEvaluatorに渡してATR計算時のTP/SL比率を上書き

**問題**: `plan`が渡された場合、TimeframeEvaluatorでplanの中央値が使われ、その後BaseStrategyで再度範囲クランプされる。planが渡されない場合はTF別固定値が使われる。この多段設定が意図通りに動作しているか検証が困難。

### 3.6 _determine_direction (612-647行)

```python
MIN_SCORES = {
    "M1": 0.10 * 15.0 = 1.5,
    "M5": 0.12 * 15.0 = 1.8,
    "M15": 0.14 * 15.0 = 2.1,
    "H1": 0.16 * 15.0 = 2.4,
    "H4": 0.18 * 15.0 = 2.7,
    "D1": 0.20 * 15.0 = 3.0,
}
```

- 最小スコア未満 -> HOLD
- スコア差 < min_score * 0.4 -> HOLD（方向不明確）
- 確度 = score_diff / 15.0 + max_score / 30.0（最大1.0）

**問題**: `_calculate_score`の実際の最大スコアは約12点だがMAX_POSSIBLE_SCOREは15.0。この不一致により確度計算が低めに出る。例: buy_score=7.0, sell_score=0の場合、confidence = 7/15 + 7/30 = 0.70。MAX_POSSIBLE_SCORE=12なら 7/12 + 7/24 = 0.87になるはず。

### 3.7 _apply_noise_filter (649-691行)

- M1/M5のみに適用
- ADX最小値: M1=10.0, M5=8.0（非常に緩い）
- ATR比率: 0.3~3.5（非常に広い範囲）

**問題**: ADX閾値が10/8と非常に低く、ほぼフィルタリングされない。典型的にADX < 20はトレンドなしとされるが、現在の設定ではADX 10-20の弱トレンドでもエントリーされる。スキャルピングでは低勝率トレードが増加する原因。

### 3.8 InStrategyConsensus (in_strategy_consensus.py)

**重み設定**:

| 戦略 | Primary | Entry | Confirm | HTF Ref | min_confidence | margin |
|------|---------|-------|---------|---------|---------------|--------|
| Scalp | 3.0 | 2.5 | 1.5 | 1.2 | 0.22 | 0.10 |
| ShortMid | 3.0 | 2.5 | 1.8 | 1.5 | 0.25 | 0.12 |
| Swing | 3.0 | 2.5 | 2.0 | 2.0 | 0.28 | 0.15 |

**問題点**:
- `min_confidence`が全戦略で0.22-0.28と低い。最大可能スコアに対して22-28%の確度でエントリーするため、低品質シグナルが多い
- `score_margin_required`も0.10-0.15と低く、買い/売りスコアの差が10-15%あればエントリーする。方向確信度が低い

### 3.9 ModeAwareScoreConsensus (mode_aware_consensus.py)

**モード別閾値**:
- SCALPING: 1.5
- DAY_TRADE: 2.0
- SWING: 2.5

**重み合計の典型値**: SCALPING=7.7, DAY_TRADE=8.3, SWING=8.5

**問題**: 閾値がweight * |direction| * strengthの合計に対して設定されるが、strengthは`signal.strength`（= buy_strength or sell_strength）で最大1.0。direction_valueも±1.0。よって各TFの最大寄与はweight値そのまま。全TFがBUYで強度1.0の場合、SCALPING合計=7.7。閾値1.5は全体の19%程度であり、非常に緩い。

### 3.10 SMCスコアリングの無効化 (296-382行)

`_evaluate_smc_factors`メソッドは実装されているが、`evaluate`メソッド内でコメントアウトされている:

```python
# SMCスコアリング（無効化 - 性能低下のため）
# 将来的にフィルターとして活用検討
pass
```

BOS/CHoCH、流動性グラブ、スイングレベルなどのSMC指標は、エントリー品質を向上させるポテンシャルがある。完全無効化ではなく、**フィルターとしての選択的使用**が有効。

### 3.11 EdgeScoreComponents (strategies/types.py)

```
edge_score = base_confidence * score_margin_factor
             * regime_fit_factor * cost_factor * htf_conflict_factor
```

**問題**: 5つの係数の乗算で、1つでも低い値があるとedge_scoreが激減する。
- cost_factor: spread 1.5pipsで0.875（妥当）
- regime_fit_factor: RANGEレジームで0.4-0.6（厳しい）
- htf_conflict_factor: HTF衝突で0.0（致命的）

min_edge_score = 0.2なので、base_confidence=0.5 * margin=1.2 * regime=0.6 * cost=0.875 * htf=1.0 = 0.315は通過。しかしhtf=0.0で即座に0。

---

## 4. 問題点と改善提案

### P1（高優先度）: _calculate_scoreでstrength/個別スコアメソッドを活用

**現状**: `_calculate_score`は簡易的なトレンド+MACDロジックのみ。7つの精緻なスコアリングメソッドと`IndicatorStrength`が未使用。

**提案**: 既存の`_score_rsi`, `_score_macd`, `_score_trend`, `_score_adx`, `_score_stochastic`, `_score_divergence`, `_score_bollinger`メソッドを`_calculate_score`から呼び出す構成に変更。

```python
def _calculate_score(self, row, candle, strength):
    buy_score = 0.0
    sell_score = 0.0
    reasons = []

    # 各インジケーターのスコアを加算
    for scorer in [self._score_rsi, self._score_macd, ...]:
        score, reason = scorer(row)
        if score > 0:
            buy_score += score
        else:
            sell_score += abs(score)
        if reason:
            reasons.append(reason)

    # HTF整合性
    htf_bonus, htf_reason = self._score_htf_alignment(...)
    ...
```

**期待効果**: 勝率+2-3%（多角的なシグナル確認による精度向上）

---

### P2（高優先度）: HTF二重ペナルティの解消

**現状**: TimeframeEvaluator._score_htf_alignmentとBaseStrategy._calculate_htf_factorで二重にHTFチェック。

**提案**: TimeframeEvaluatorのHTFチェックを削除し、BaseStrategy._calculate_htf_factorに一本化。または、TimeframeEvaluator側をボーナスのみ（整合時+スコア）にし、ペナルティはBaseStrategy側のみにする。

```python
# TimeframeEvaluator._score_htf_alignment（修正案）
if counter_trend_count > 0:
    return 0.0, "HTF逆行"  # ペナルティなし、ボーナスなし
if aligned_count >= 2:
    return 4.0, f"HTF強整合({aligned_count}TF)"
elif aligned_count >= 1:
    return 2.0, f"HTF整合({aligned_count}TF)"
return 0.0, ""
```

**期待効果**: トレード数+10-15%、勝率維持（HTF整合のフィルタリングはBaseStrategy側で維持）

---

### P3（中優先度）: InStrategyConsensusの閾値調整

**現状**: min_confidence=0.22-0.28、score_margin=0.10-0.15が緩すぎる。

**提案**:
| 戦略 | min_confidence | score_margin |
|------|---------------|-------------|
| Scalp | 0.30 | 0.20 |
| ShortMid | 0.35 | 0.25 |
| Swing | 0.40 | 0.30 |

**期待効果**: トレード数-15-20%だが勝率+2-3%、PF向上

---

### P4（中優先度）: SMCフィルターの選択的再有効化

**現状**: `_evaluate_smc_factors`が完全に無効化。

**提案**: シグナル品質のフィルターとして再有効化。ただし、スコア加算ではなくconfidence補正として使用:

```python
# SMCを確度補正として使用（加算ではなく乗算）
smc_buy, smc_sell, smc_reasons = self._evaluate_smc_factors(row)
if direction == SignalType.BUY and smc_buy > 0:
    confidence *= 1.1  # SMC支持で確度アップ
elif direction == SignalType.BUY and smc_sell > 2:
    confidence *= 0.8  # SMC反対で確度ダウン
```

**期待効果**: PF+0.05-0.10（高確度シグナルの選別）

---

### P5（中優先度）: RSIフィルターの統一

**現状**: `_calculate_score`内のRSIフィルター（80/20で拒否）と`_score_rsi`（overbought/oversold設定値）が別々の閾値を使用。また、RSIが逆張り指標として使われる箇所（strength_calculator）とトレンドフィルターとして使われる箇所（_calculate_score）が混在。

**提案**: RSIの使用方針を明確化。トレンドフォロー戦略では:
- RSI 30-70: 順方向エントリー許可
- RSI < 30 or > 70: 逆張りモードに切り替え or エントリー抑制
- RSI 80/20の極端値のみ完全拒否

---

### P6（低優先度）: MAX_POSSIBLE_SCOREの修正

**現状**: MAX_POSSIBLE_SCORE = 15.0だが、`_calculate_score`の実際の最大値は約12点。

**提案**: `_calculate_score`のスコアリングロジック修正後に、実際の最大可能スコアに基づいてMAX_POSSIBLE_SCOREを更新。

---

### P7（低優先度）: ノイズフィルターのADX閾値引き上げ

**現状**: M1=10, M5=8。

**提案**: M1=15, M5=12に引き上げ。短期足のノイズ耐性向上。

**期待効果**: トレード数-5%、勝率+0.5%

---

### P8（低優先度）: ModeAwareScoreConsensus閾値の見直し

**現状**: SCALPING=1.5, DAY_TRADE=2.0, SWING=2.5。重み合計の19-29%。

**提案**: 重み合計の30-40%程度に引き上げ:
- SCALPING: 2.5
- DAY_TRADE: 3.0
- SWING: 3.5

---

## 5. 実装優先順位のまとめ

| 優先度 | 提案 | 工数 | 期待効果 | リスク |
|--------|------|------|----------|--------|
| P1 | スコアリングメソッドの統合 | 中 | 勝率+2-3% | 要バックテスト検証 |
| P2 | HTF二重ペナルティ解消 | 小 | トレード数+10-15% | 低 |
| P3 | Consensus閾値調整 | 小 | 勝率+2-3% | トレード数減少 |
| P4 | SMCフィルター再有効化 | 中 | PF+0.05-0.10 | 過去のパフォーマンス低下要因の再調査必要 |
| P5 | RSIフィルター統一 | 小 | 勝率+1% | 低 |
| P6 | MAX_POSSIBLE_SCORE修正 | 小 | 確度計算の正確化 | P1依存 |
| P7 | ノイズフィルターADX閾値 | 小 | 勝率+0.5% | 低 |
| P8 | ModeAwareConsensus閾値 | 小 | 勝率+1% | トレード数減少 |

**推奨実装順序**: P2 -> P1 -> P3 -> P5 -> P7 -> P6 -> P8 -> P4

P2（HTF二重ペナルティ）は工数が小さくリスクも低いため最優先。P1（スコアリング統合）は最大効果だが要検証。
