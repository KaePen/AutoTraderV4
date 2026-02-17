# AutoTraderV4 トレード判定フロー評価レポート

**評価者**: genius-trader（プロトレーダー視点）
**評価日**: 2026-02-07
**対象**: シグナル生成 -> フィルタリング -> エントリー判定 -> TP/SL設定の全フロー

---

## 1. エグゼクティブサマリー

現在の成績（勝率56%、PF 1.01、年+0.23%）は、トレードコストを差し引くとほぼ盈亏均衡の状態。月244トレードという高頻度にもかかわらず収益性が極めて低い。以下に重要度順の問題を列挙する。

### 問題リスト（重要度順）

| # | 問題 | 重要度 | 影響 |
|---|------|--------|------|
| 1 | TP/SL設定が3箇所で計算され、最終的にどれが使われるか不透明 | CRITICAL | 意図したリスクリワードが実現しない |
| 2 | エントリー条件が「トレンド+MACD」に偏重、レンジ相場でトレードし過ぎ | HIGH | 勝率低下・不要トレードの増加 |
| 3 | HTFフィルターが二重適用（evaluator内 + trade_bot外側） | HIGH | 過剰フィルタリングまたはフィルター漏れ |
| 4 | コンセンサス閾値が低すぎ（Scalp: 2.5, DayTrade: 3.5） | HIGH | 低品質シグナルの通過 |
| 5 | スコアリング設計にスプレッド/スリッページの影響が未反映 | MEDIUM | 実効期待値がマイナスのトレードをエントリー |
| 6 | レジーム判定がH1単一足依存で遅延する | MEDIUM | モード選択のミスマッチ |
| 7 | 3つのアーキテクチャが並存（legacy/new/convergent） | LOW | 保守性低下、テスト困難 |

---

## 2. 判定フロー全体の評価

### 2.1 フロー概要

```
[毎分呼出]
  |
  v
generate_signal()
  |-- _detect_regime() .............. H1データからレジーム判定
  |-- mode_selector.select() ....... レジーム+HTF整合+時間帯 -> モード選択
  |-- tf_router.route() ............ モードに応じたTFセット決定
  |-- risk_manager.can_trade() ..... 日次損失+クールダウン
  |
  |-- [各TFでevaluate()] ........... 指標計算+スコアリング+SL/TP計算
  |     |-- calculator.calculate() . RSI/MACD/BB/Stoch等の強度
  |     |-- _calculate_score() ..... トレンド+MACD+ADX+RSI+HTF整合スコア
  |     |-- _determine_direction() . 最小スコア閾値判定
  |     |-- _apply_noise_filter() .. M1/M5のADX/ATRフィルター
  |     |-- _calculate_sl_tp() ..... ATRベースSL + TP/SL比率でTP計算
  |
  |-- consensus.consolidate() ...... 加重スコア統合+閾値判定
  |
  |-- _check_htf_trend_alignment() . H4/D1のSMA+MACD一致確認（閾値0.8）
  |
  |-- SL/TP最終計算 ................ primary_tfのSL * plan.tp_sl_ratio
  |
  |-- position_sizer.calculate() ... ロットサイズ決定
  |
  v
ConsolidatedSignal
```

### 2.2 プロトレーダーとしての総合評価

**良い点**:
- マルチタイムフレーム分析の基本思想は正しい
- レジーム検出による戦略切替は合理的
- ポジションサイジングにリスク管理が組み込まれている

**根本的な問題点**:
このシステムは「多くの条件を複合的に組み合わせれば精度が上がる」という前提で設計されているが、実際にはフィルターの重複・矛盾・希薄化が発生し、**エッジが消失**している。PF 1.01は「フィルターを通過したトレードにエッジがほぼない」ことを示す。

---

## 3. 各評価観点の詳細分析

### 3.1 エントリー条件の組み合わせ

#### 現状の条件構成

```
_calculate_score() のスコアリング:
  (1) トレンド判定: close vs SMA20/SMA50 -> 必須条件
  (2) MACDモメンタム: MACD vs Signal -> 主要加点（+5点）
  (3) ADX: > 20 -> 加点（+2点）
  (4) RSI: 極端値除外、順方向で加点（+1点）
  (5) HTF整合: 逆行で-5点/0.5倍、整合で+2~4点
```

#### 問題点

**A. トレンド+MACDへの過度な依存**

スコアの大半が「close > SMA20」と「MACD > Signal」の2条件で決まる。これらは本質的にラギング指標であり、トレンドの「確認」にはなるが「エントリータイミング」の特定には不向き。

プロトレーダーの視点では:
- SMA20を超えた時点でトレンドの初動はすでに過ぎている
- MACDクロスはさらに遅延する
- 結果として「トレンドの中間〜終盤」でエントリーし、TP到達前に反転する

**B. 価格アクション（プライスアクション）の不在**

実際のプロトレーダーが最も重視する要素:
- サポート/レジスタンスレベルでの反応
- ブレイクアウト（レベル突破）のモメンタム
- ローソク足パターン（ピンバー、包み足等）
- 出来高の確認

これらが一切スコアリングに含まれていない。指標ベースの判定のみでは、**エントリーの精度に構造的な上限がある**。

**C. レンジ相場でのオーバートレード**

`_calculate_score()`は`close > SMA20`の時点で`buy_score = 2.5`を付与する。レンジ相場でSMA20を跨いで振動する場合、頻繁にシグナルが生成されるが勝率は低い。月244トレードの多くがこのパターンと推測される。

### 3.2 マルチタイムフレーム合意の仕組み

#### 現状のMTF構造

```
[新アーキテクチャ] _generate_signal_new():
  evaluator毎に独立スコアリング
    -> _score_htf_alignment() で evaluator内HTFチェック（第1層）
  consensus.consolidate() で加重統合
    -> mode_thresholds で閾値判定
  _check_htf_trend_alignment() で最終HTFフィルター（第2層）

[輻輳型] _generate_signal_convergent():
  各戦略が独自にTF評価+コンセンサス
    -> _calculate_htf_factor() で戦略内HTFチェック
  strategy_selector.choose() でedge_scoreベスト選択
```

#### 問題点

**A. HTFフィルターの二重適用**

`TimeframeEvaluator._score_htf_alignment()`:
- 各TFの評価時にHTFデータを参照してスコア加減点（-5〜+4点）
- HTF逆行でスコアが0.5倍に減衰

`UnifiedTradeBot._check_htf_trend_alignment()`:
- コンセンサス後にH4/D1のSMA+MACDを再度チェック
- 閾値0.8未満で完全ブロック

**影響**: HTF情報が2回チェックされるため、HTF順行トレードが過度に優遇され、HTF逆行（だが短期TFで有効な）トレードが完全に排除される。レンジ相場やHTF転換点付近での機会損失が大きい。

**B. TF間の重み設定が実際の情報量と不一致**

`ConsensusConfig`:
- primary_weight: 3.0
- entry_weight: 2.0
- confirm_weight: 1.5

M5（primary）の「情報量」とH4（confirm）の「情報量」は根本的に異なる。H4の1本はM5の48本分の情報を含む。にもかかわらず、重みの差は2倍程度しかない。上位足の重みが相対的に低すぎる。

**C. consensus閾値が低すぎる**

```
scalping_threshold: 2.5
day_trade_threshold: 3.5
swing_threshold: 4.0
```

スコアリングの最大値を考慮すると:
- 完全上昇+MACD+強ADX+RSI順方向+HTF強整合 = 5+2+1+4 = 12点
- primary_weight=3.0の場合: 12 * 3.0 * strength(0-1) = 最大36

閾値2.5〜4.0は最大スコアの7〜11%程度で、**非常に多くの低品質シグナルが通過**する。

### 3.3 市場状態の判定と戦略選択

#### 現状のレジーム判定

```python
_detect_regime():  H1データ1行のみでレジーム判定
_select_mode():
  HIGH_VOL + active_session -> SCALPING
  HIGH_VOL + inactive       -> DAY_TRADE
  vol > 1.3               -> SCALPING
  TREND + htf > 0.5       -> SWING
  TREND + active + vol>0.8 -> SCALPING
  それ以外                  -> DAY_TRADE
```

#### 問題点

**A. レジーム判定の遅延と不安定性**

H1足1本の情報のみでレジーム判定を行っている。レジームは通常数時間〜数日の単位で変化するものであり、H1の1行だけでは:
- ノイズに影響されやすい
- レジーム転換の検出が1時間遅れる
- 「トレンド中のレンジ」と「レンジ中のトレンド」を区別できない

**B. SCALPINGモードの選択基準が曖昧**

「高ボラ=スキャルピング」は直感に反する。プロの世界では:
- 高ボラ時はスプレッド拡大、スリッページ増加でスキャルピング不利
- 高ボラ時はSL幅が大きくなり、スキャルピングのRR比が悪化
- むしろ高ボラ時こそスイングで大きなトレンドを取るべき

**C. DAY_TRADEがデフォルトの問題**

RANGE/LOW_VOL時のデフォルトがDAY_TRADEだが、レンジ相場でトレンドフォロー型のDAY_TRADEを行うのは本質的に不利。レンジ相場では:
- 逆張り（サポート/レジスタンスからの反発）
- ブレイクアウト待ち
- トレード頻度の大幅削減
が適切。

### 3.4 TP/SL設定の分散問題

#### 現状のTP/SL計算フロー（3つの経路）

**経路1: timeframe_evaluator._calculate_sl_tp()**
```python
# TF別ハードコード比率
tp_sl_ratios = {"M1": 1.0, "M5": 1.05, "M15": 1.1, ...}
# planがあればplan比率で上書き
if plan is not None:
    tp_sl_ratio = plan.get_recommended_tp_sl_ratio()
```

**経路2: mode_selector.MODE_PLANS**
```python
TradingPlan.tp_sl_ratio_range -> get_recommended_tp_sl_ratio()
# (範囲の中央値を返す)
# Scalp: (1.0+1.3)/2 = 1.15
# DayTrade: (1.1+1.4)/2 = 1.25
# Swing: (1.2+1.6)/2 = 1.4
```

**経路3: strategies/base.py._calculate_sl_tp() [輻輳型のみ]**
```python
# timeframes.tp_sl_ratio_rangeで範囲制限
min_ratio, max_ratio = self.timeframes.tp_sl_ratio_range
if current_ratio < min_ratio:
    tp_pips = sl_pips * min_ratio
```

#### 問題点

**A. 最終値の不透明性**

`_generate_signal_new()`のフローでは:
1. `evaluator.evaluate()` -> `_calculate_sl_tp(plan)` で TF別比率 -> planで上書き
2. `consensus.consolidate()` は `_calculate_consolidated_sl_tp()` で primary 70% + 平均 30% の加重平均（ただし新アーキテクチャでは直接呼ばれない）
3. `_generate_signal_new()` 最後で `primary_signal.sl_pips * plan.tp_sl_ratio` で再計算

つまり、evaluator内でplanの比率が適用された後、trade_bot側で再度planの比率を掛けている可能性がある。**TP/SL比率が二重適用される**。

具体的に: evaluatorで `tp_pips = sl_pips * 1.15`（plan比率）が計算され、trade_botで `tp_pips = sl_pips * 1.15` が再計算される。結果的にevaluator内で計算されたtp_pipsは捨てられ、trade_bot側の計算が使われるが、**evaluator内のtp_pipsはconsensus変換時のstrength計算に影響する可能性がある**。

**B. SL距離の固定的な制限**

```python
sl_pips = max(10.0, min(sl_pips, 50.0))
```

SLが10〜50pipsに制限されているが、通貨ペアによってATRは大きく異なる。GBP/JPYのATRとEUR/USDのATRでは倍以上の差がある。固定pips制限はマルチペア対応を阻害する。

**C. スプレッド/スリッページが実効比率に与える影響の無視**

スプレッド1.5pips + スリッページ0.5pips = 実効コスト2.0pips。
SL=15pipsの場合: 実効TP/SL = (TP - 2.0) / (SL + 2.0) = (15*1.15 - 2.0) / (15 + 2.0) = 15.25/17.0 = 0.897

つまり設定上1.15のTP/SL比率が、実効では0.90未満になる。**勝率56%で実効TP/SL 0.90は損失**になる（必要勝率 = 1/(1+0.90) = 52.6%だが、コスト込みの期待値は 0.56*0.90 - 0.44*1.0 = 0.504 - 0.44 = +0.064 で辛うじてプラス）。

### 3.5 シグナル強度スコアリングの設計思想

#### 現状のスコアリング構造

```
IndicatorStrengthCalculator.calculate():
  RSI強度 (0-1)
  MACD強度 (0-1)
  トレンド強度 (0-1)
  ダイバージェンス強度 (0-1)
  ボリンジャー強度 (0-1)
  ストキャスティクス強度 (0-1)
  ATR正規化 (0-1)

_calculate_score():
  上記strengthは直接使用されず、独自にスコアリング
  -> トレンド+MACDで基本スコア -> RSIフィルター -> HTFボーナス
```

#### 問題点

**A. IndicatorStrengthCalculatorの出力が事実上無視されている**

`calculate()`で7つの指標強度を計算しているが、`_calculate_score()`ではrowから直接RSI/MACD/ADXを読み、独自の条件分岐でスコアを付けている。`IndicatorStrength`オブジェクトは`_calculate_sl_tp()`に渡されるが、そこでも使われていない。**指標強度の計算が空回りしている**。

**B. スコアリングが離散的すぎる**

```python
if full_uptrend and macd_bullish:
    buy_score = 5.0  # ジャンプ
elif uptrend and macd_bullish:
    buy_score = 4.0  # ジャンプ
elif full_uptrend or uptrend:
    buy_score = 2.5  # ジャンプ
```

スコアが5.0/4.0/2.5の3段階に離散化されており、**シグナルの「強さの濃淡」が表現できない**。例えば「SMA20からわずかに上にいる」のと「SMA20から大きく乖離している」のが同じスコアになる。

**C. ADXの使い方が不完全**

ADX > 20 で +2点のボーナスだが、ADXの値そのものは使われていない。ADX = 21 と ADX = 60 が同じ扱い。ADXの絶対値はトレンドの「品質」を示す重要な情報。

---

## 4. 具体的な改善提案

### 優先度1: TP/SL計算の一元化（即効性: HIGH）

**現状**: 3箇所で計算、最終値が不透明
**提案**: TP/SL計算を単一の責任に集約

```
改善方向:
1. TimeframeEvaluator._calculate_sl_tp() は「ATRベースのSL距離」のみ計算
2. TP/SL比率の適用は trade_bot 側の1箇所のみ
3. 輻輳型の BaseStrategy._calculate_sl_tp() も同じロジックを参照
```

**期待効果**: 設定変更の影響が予測可能になる。PF改善の前提条件。

### 優先度2: スプレッド/スリッページを考慮した実効TP/SL比率（即効性: HIGH）

**現状**: 名目TP/SL比率のみ使用
**提案**: エントリー判定時にコスト込みの実効比率を計算し、実効比率が1.0未満のトレードをフィルタリング

```
改善方向:
effective_tp = tp_pips - spread_pips - slippage_pips
effective_sl = sl_pips + spread_pips + slippage_pips
effective_ratio = effective_tp / effective_sl
if effective_ratio < 1.0:
    HOLD（エントリーしない）
```

**期待効果**: PF 1.01 -> 1.05〜1.10（低品質トレードの排除、トレード数20〜30%減少）

### 優先度3: コンセンサス閾値の引き上げ（即効性: HIGH）

**現状**: Scalp=2.5, DayTrade=3.5, Swing=4.0
**提案**: 最低でも最大スコアの25%以上に引き上げ

```
改善方向:
scalping_threshold: 5.0 〜 6.0
day_trade_threshold: 6.0 〜 8.0
swing_threshold: 8.0 〜 10.0
```

**期待効果**: トレード数 244/月 -> 100〜150/月、勝率 56% -> 60〜65%

### 優先度4: エントリータイミングの改善（中期）

**現状**: ラギング指標のみでエントリー
**提案**: プライスアクション要素の追加

```
改善方向:
1. サポート/レジスタンスレベルからの距離を計算
2. 直近のレベルブレイクアウト検出
3. ローソク足パターン認識（ピンバー、包み足）
4. これらをスコアリングに「必須条件」として追加
```

**期待効果**: エントリー精度の構造的改善。勝率60%+を目指せる基盤。

### 優先度5: HTFフィルターの整理（中期）

**現状**: evaluator内 + trade_bot外側で二重チェック
**提案**: HTFフィルターを1箇所に集約

```
改善方向:
1. evaluator内の_score_htf_alignment()はボーナス加点のみ（ペナルティなし）
2. 最終的なHTFフィルターは trade_bot の _check_htf_trend_alignment() に集約
3. ただし完全ブロックではなく、スコア減衰（例: 0.6倍）に変更
```

**期待効果**: HTF転換点付近でのトレード機会回復。

### 優先度6: レンジ相場の明示的な処理（中期）

**現状**: RANGE -> DAY_TRADE（トレンドフォロー）
**提案**: RANGE時の独自ロジック

```
改善方向:
1. RANGE検出時はトレード頻度を大幅削減
2. レンジ上下限からの逆張りエントリーのみ許可
3. TP/SL比率を1.5以上に引き上げ（レンジ幅ベース）
```

**期待効果**: レンジ相場での不要トレード50%削減。

### 優先度7: スコアリングの連続化（長期）

**現状**: 離散的な3段階スコア
**提案**: 指標値の連続的なスコア変換

```
改善方向:
1. SMAからの乖離率をスコアに反映
2. MACDのクロス後の加速度を考慮
3. ADXの絶対値を連続的にスコアに反映
4. IndicatorStrengthCalculatorの出力を実際に活用
```

**期待効果**: シグナル品質の粒度が向上し、上位シグナルのみでのトレードが可能に。

---

## 5. 期待される改善効果

### 短期（優先度1-3の実施後）

| 指標 | 現状 | 期待値 |
|------|------|--------|
| トレード数/月 | 244 | 100〜150 |
| 勝率 | 56% | 58〜62% |
| PF | 1.01 | 1.10〜1.20 |
| 年間収益率 | +0.23% | +3〜8% |

### 中長期（優先度4-7の実施後）

| 指標 | 短期改善後 | 期待値 |
|------|-----------|--------|
| トレード数/月 | 100〜150 | 60〜100 |
| 勝率 | 58〜62% | 62〜68% |
| PF | 1.10〜1.20 | 1.25〜1.50 |
| 年間収益率 | +3〜8% | +10〜20% |

### 実施順序の推奨

```
フェーズ1（1-2週間）: 優先度1-3
  -> TP/SL一元化 + コスト考慮 + 閾値引き上げ
  -> バックテストで効果確認

フェーズ2（2-4週間）: 優先度4-5
  -> プライスアクション追加 + HTF整理
  -> バックテストで効果確認

フェーズ3（1-2ヶ月）: 優先度6-7
  -> レンジ処理 + スコアリング連続化
  -> 総合バックテスト + ウォークフォワード検証
```

---

## 補足: アーキテクチャの並存問題

`generate_signal()`は3つのアーキテクチャを`config`フラグで切り替える:
- `_generate_signal_legacy()` - 旧式
- `_generate_signal_new()` - 新アーキテクチャ
- `_generate_signal_convergent()` - 輻輳型

これら3つは同じ`evaluator`を共有するが、コンセンサスロジックとTP/SL計算が異なる。長期的には1つに統合し、デッドコードを削除すべき。輻輳型のedge_scoreベースの戦略選択は設計思想として良いが、base strategyの`_calculate_sl_tp()`が独自にTP/SL比率制限を持つことで、TP/SL分散問題がさらに複雑化している。
