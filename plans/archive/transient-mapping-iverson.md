# AutoTraderV4 意思決定フロー改善計画

## 調査から得られた核心的発見

### 勝てるトレーダーの本質（調査結果）

| 要素 | 発見 | 出典 |
|------|------|------|
| **勝率は重要ではない** | 勝率39%でもCAGR 57.8%達成（トレンドフォロー） | [Quantified Strategies](https://www.quantifiedstrategies.com/forex-trading-strategies/) |
| **リスクリワード比が鍵** | 勝率50%でもRR 1:1.6で年31.45%利益 | [IG International](https://www.ig.com/en/trading-strategies/_how-to-become-a-better-trader-in-2025-241230) |
| **インジケーターより価格** | プロは遅延のないプライスアクションを好む | [FXSSI](https://fxssi.com/price-action-vs-indicator) |
| **機関の動きを追う** | Smart Money Concept（流動性ゾーン、オーダーブロック） | [XS.com](https://www.xs.com/en/blog/smart-money-concept/) |
| **市場構造の理解** | BOS/CHoCH でトレンド継続/反転を確認 | [FXOpen](https://fxopen.com/blog/en/what-is-a-break-of-structure-and-how-can-you-trade-it/) |

### 97%のトレーダーが負ける理由
- オーバーフィッティング（過去データへの過適合）
- リスク管理の欠如
- 市場適応性の欠如
- **予測に依存**（プロセスではなく）

出典: [Amplework](https://www.amplework.com/blog/ai-trading-bots-failures-how-to-build-profitable-bot/), [Medium](https://medium.com/@info_32840/the-7-most-common-mistakes-in-algorithmic-trading-and-how-to-avoid-them-c94cbd4c7bfd)

---

## 現在のボットの根本的問題

### 問題1: 間違った目標設定
```
現在: 勝率60%を目標
真実: 勝率39%でも利益可能（RR比が重要）
```

### 問題2: インジケーター偏重
```
現在: 6つの遅延インジケーター（RSI, MACD, BB, Stoch, ADX, Divergence）
真実: プロはプライスアクション（遅延なし）を使用
```

### 問題3: 市場構造の欠如
```
現在: なし
必要: Higher High/Lower Low、Break of Structure、サポート/レジスタンス
```

### 問題4: 流動性の無視
```
現在: 流動性ゾーンの考慮なし
必要: 機関が狙う流動性ゾーン（ストップハンティング対策）
```

### 問題5: 時間帯の最適化不足
```
現在: 全時間帯で同じ戦略
必要: Kill Zones（ロンドン/NYセッション）での取引に限定
```

---

## 新しい目標設定

| 指標 | 旧目標 | 新目標 | 根拠 |
|------|--------|--------|------|
| 勝率 | 60% | **45-55%** | 勝率より RR 比が重要 |
| RR比 | 1.0-1.3 | **1.5-2.0** | 勝率50%でも利益可能 |
| PF | 1.2+ | **1.3+** | RR比改善で達成可能 |
| 月次収益 | 5% | **2-5%** | 現実的な目標 |

## 問題の根本原因

### 1. 情報の冗長性（二重スコアリング）
現在、2つの独立したスコアリングシステムが存在：

| システム | 場所 | 処理内容 | 使用状況 |
|----------|------|----------|----------|
| IndicatorStrength | strength_calculator.py | 6指標の等加重平均 | **未使用** |
| _calculate_score() | timeframe_evaluator.py | トレンドベーススコア | **実際に使用** |

**問題**: IndicatorStrengthで計算した値の多くが無駄になっている

### 2. 未活用指標
- **ストキャスティクス**: 計算されるが、_calculate_score()で未使用（RSIと重複）
- **ボリンジャーバンド**: 計算されるが、_calculate_score()で未使用
- **ダイバージェンス**: 0.8という強いシグナルだが、_calculate_score()で未使用

### 3. TP/SL比率の分散定義
3箇所で別々に定義されており、どれが使用されるか不明確：
- `mode_selector.py`: MODE_PLANS
- `scalp.py/swing.py/short_mid.py`: Timeframes
- `timeframe_evaluator.py`: _calculate_sl_tp()フォールバック

### 4. HTFフィルターの矛盾
4箇所で異なる処理：
- strength_calculator: ボーナス扱い（×1.5）
- timeframe_evaluator: -5点ペナルティ
- trade_bot: 閾値0.8でブロック
- in_strategy_consensus: 重み0.5-0.6

---

## 改善計画

### Phase 1: 設定の一元化（低リスク）

**目的**: 設定変更の一貫性確保、デバッグ容易化

#### 1.1 TradingConfigクラスの作成
```
新規作成: src/autotrader/decision/unified/trading_config.py
```

全設定を一箇所に集約：
- TP/SL比率（モード別）
- エントリー閾値（モード別）
- HTFフィルター設定
- ADX/RSI閾値

#### 1.2 既存ファイルの修正
| ファイル | 変更内容 |
|----------|----------|
| mode_selector.py | MODE_PLANSのTP/SL → TradingConfig参照 |
| scalp.py, swing.py, short_mid.py | tp_sl_ratio_range削除 |
| timeframe_evaluator.py | フォールバック値 → TradingConfig参照 |

---

### Phase 2: 冗長指標の削除（低リスク）

**目的**: 計算コスト削減、コード簡素化

#### 2.1 ストキャスティクス削除
- `_calculate_stoch_strength()`: 計算をスキップ（return 0.0）
- RSIと機能重複のため不要

#### 2.2 ボリンジャーバンド削除
- `_calculate_bb_strength()`: 計算をスキップ（return 0.0）
- 現在_calculate_score()で未使用

#### 修正ファイル
```
src/autotrader/decision/unified/strength_calculator.py
```

---

### Phase 3: スコアリングの一元化（中リスク）

**目的**: 情報フローの明確化

#### 3.1 IndicatorStrength.total_strengthの廃止
- `buy_strength`/`sell_strength`プロパティを`_calculate_score()`の結果に置換
- TimeframeSignalでは`_calculate_score()`の出力のみ使用

#### 3.2 Divergence活用の追加
```python
# _calculate_score()に追加
if divergence > 0.5:  # 強気ダイバージェンス
    buy_score += 2.0
elif divergence < -0.5:  # 弱気ダイバージェンス
    sell_score += 2.0
```

#### 修正ファイル
```
src/autotrader/decision/unified/timeframe_evaluator.py
src/autotrader/decision/unified/strength_calculator.py
```

---

### Phase 4: HTFフィルターの統一（中リスク）

**目的**: HTF判定の一貫性

#### 4.1 統一ロジック
`trade_bot._check_htf_trend_alignment()`に統一し、他を削除

#### 4.2 削除対象
- strength_calculator内のHTF処理
- timeframe_evaluator._score_htf_alignment()

#### 4.3 設定参照
閾値・ボーナス値をTradingConfigから読み込み

#### 修正ファイル
```
src/autotrader/decision/unified/trade_bot.py
src/autotrader/decision/unified/timeframe_evaluator.py
src/autotrader/decision/unified/strength_calculator.py
```

---

## 修正対象ファイル一覧

| ファイル | Phase | 変更内容 |
|----------|-------|----------|
| trading_config.py | 1 | **新規作成** - 統合設定クラス |
| mode_selector.py | 1 | TradingConfig参照に変更 |
| scalp.py | 1 | tp_sl_ratio_range削除 |
| swing.py | 1 | tp_sl_ratio_range削除 |
| short_mid.py | 1 | tp_sl_ratio_range削除 |
| strength_calculator.py | 2,3,4 | 冗長指標削除、HTF処理削除 |
| timeframe_evaluator.py | 1,3,4 | TradingConfig参照、Divergence追加、HTF処理削除 |
| trade_bot.py | 4 | HTFフィルター統一 |

---

## 検証計画

### 各Phase後のバックテスト
```bash
# 基準値記録
python -m autotrader.backtest --period 2023 --output baseline.json

# Phase N 完了後
python -m autotrader.backtest --period 2023 --output phase_n.json
```

### 成功基準
| 指標 | 現在値 | 目標 | 許容範囲 |
|------|--------|------|----------|
| 勝率 | 56.04% | 60% | 54%以上維持 |
| PF | 1.01 | 1.2+ | 0.95以上維持 |
| トレード数 | 244/月 | 200+ | 150以上維持 |

### ロールバック条件
- PF < 0.95
- 勝率 < 50%
- トレード数 < 100/月

---

# Part 2: 根本的なアプローチ転換（推奨）

上記 Phase 1-4 は既存システムの最適化ですが、調査結果から**根本的なアプローチ転換**が必要と判断しました。

## アプローチの転換

```
現在: インジケーター → スコア → エントリー（予測ベース）
新規: 市場構造 → 流動性 → インジケーター確認 → エントリー（プロセスベース）
```

---

## Phase 5: 市場構造分析の導入（高優先度）

### 5.1 Swing High / Swing Low 検出
```python
# 新規作成: src/autotrader/decision/unified/market_structure.py

class SwingDetector:
    """スイングポイント検出"""

    def detect_swing_high(self, candles: list[Candle], lookback: int = 5) -> list[SwingPoint]:
        """直近の高値がlookback本分の中で最高かどうか"""

    def detect_swing_low(self, candles: list[Candle], lookback: int = 5) -> list[SwingPoint]:
        """直近の安値がlookback本分の中で最低かどうか"""
```

### 5.2 Break of Structure (BOS) 検出
```python
class StructureAnalyzer:
    """市場構造分析"""

    def detect_bos(self, swings: list[SwingPoint]) -> StructureSignal:
        """
        上昇トレンド: HH + HL の連続 → BOS = 前回HHを超えた時
        下降トレンド: LH + LL の連続 → BOS = 前回LLを下回った時
        """

    def detect_choch(self, swings: list[SwingPoint]) -> StructureSignal:
        """
        Change of Character: トレンド反転の兆候
        上昇中にLLを作成 → 弱気CHoCH
        下降中にHHを作成 → 強気CHoCH
        """
```

### 5.3 トレンド状態の定義
```python
class TrendState(Enum):
    BULLISH_TREND = "bullish"      # HH + HL 継続
    BEARISH_TREND = "bearish"      # LH + LL 継続
    CONSOLIDATION = "consolidation" # 構造が不明確
    REVERSAL_PENDING = "reversal"   # CHoCH 検出
```

---

## Phase 6: 流動性ゾーンの特定（高優先度）

### 6.1 流動性ゾーン検出
```python
# 新規作成: src/autotrader/decision/unified/liquidity.py

class LiquidityAnalyzer:
    """流動性ゾーン分析"""

    def find_buy_side_liquidity(self, candles: list[Candle]) -> list[LiquidityZone]:
        """
        買い側流動性 = 直近高値の上（ショートのストップが溜まる場所）
        Equal Highs（同じ価格の高値が複数）を特に重視
        """

    def find_sell_side_liquidity(self, candles: list[Candle]) -> list[LiquidityZone]:
        """
        売り側流動性 = 直近安値の下（ロングのストップが溜まる場所）
        Equal Lows（同じ価格の安値が複数）を特に重視
        """
```

### 6.2 ストップハンティング対策
```python
def is_liquidity_grab(self, candle: Candle, liquidity_zone: LiquidityZone) -> bool:
    """
    流動性を取った後に反転したかどうか
    - ゾーンを超えた後、終値がゾーン内に戻る
    - ウィックがゾーンを超えている
    """
```

---

## Phase 7: エントリーロジックの再設計（高優先度）

### 7.1 新しいエントリー条件
```python
class SmartMoneyEntry:
    """Smart Money Concept に基づくエントリー"""

    def should_enter(
        self,
        structure: TrendState,
        liquidity: LiquidityAnalysis,
        key_levels: list[PriceLevel],
        indicators: IndicatorStrength,  # 確認用のみ
    ) -> EntrySignal:
        """
        エントリー条件（すべて必須）:
        1. 市場構造がトレンドを示している（BOS確認済み）
        2. 流動性が取られた後（ストップハンティング完了）
        3. キーレベルでの反応がある
        4. インジケーターが方向を確認（補助的）
        """
```

### 7.2 新しいエントリーフロー
```
1. 市場構造チェック
   - BOS が発生しているか？
   - トレンド方向は明確か？
   → NO → エントリーなし

2. 流動性チェック
   - 直近で流動性が取られたか？
   - ストップハンティングが完了したか？
   → NO → エントリーなし

3. キーレベルチェック
   - 価格がサポート/レジスタンス付近か？
   - レベルでの反応があるか？
   → NO → エントリーなし

4. インジケーター確認（補助）
   - トレンド方向と一致しているか？
   - RSI が極端な過熱/過冷でないか？
   → 方向確認のみ、ブロックはしない

5. エントリー実行
   - SL: 直近のスイングポイント
   - TP: 次の流動性ゾーン or RR 1:2
```

---

## Phase 8: TP/SL の再設計（高優先度）

### 8.1 構造ベースの SL 設定
```python
def calculate_stop_loss(self, entry: EntrySignal, structure: StructureAnalysis) -> float:
    """
    SL は ATR ではなく、市場構造に基づく
    - ロング: 直近のスイングロー - バッファ
    - ショート: 直近のスイングハイ + バッファ
    """
```

### 8.2 流動性ベースの TP 設定
```python
def calculate_take_profit(
    self,
    entry: EntrySignal,
    liquidity: LiquidityAnalysis,
    min_rr_ratio: float = 1.5,
) -> float:
    """
    TP は固定比率ではなく、次の流動性ゾーン
    - ロング: 次の買い側流動性ゾーン
    - ショート: 次の売り側流動性ゾーン
    - 最低でも RR 1.5 以上を確保
    """
```

---

## Phase 9: 時間帯フィルター（Kill Zones）

### 9.1 高流動性時間帯の定義
```python
KILL_ZONES = {
    "london_open": (7, 10),    # UTC 7:00-10:00
    "ny_open": (13, 16),       # UTC 13:00-16:00
    "london_ny_overlap": (13, 17),  # UTC 13:00-17:00（最高流動性）
}
```

---

## 新規作成ファイル一覧

| ファイル | 内容 |
|----------|------|
| **market_structure.py** | スイング検出、BOS/CHoCH 分析 |
| **liquidity.py** | 流動性ゾーン検出、ストップハンティング分析 |
| **key_levels.py** | サポート/レジスタンス レベル分析 |
| **smart_money_entry.py** | 新エントリーロジック |
| **kill_zones.py** | 時間帯フィルター |

---

## 推奨実装順序

| 優先度 | Phase | 内容 | 理由 |
|--------|-------|------|------|
| **1** | Phase 5 | 市場構造分析 | 最も基本的な要素 |
| **2** | Phase 7 | エントリーロジック再設計 | 構造分析と同時に必要 |
| **3** | Phase 8 | TP/SL再設計 | RR比改善の核心 |
| **4** | Phase 6 | 流動性ゾーン | エントリー精度向上 |
| **5** | Phase 9 | 時間帯フィルター | 追加フィルター |
| **6** | Phase 1-4 | 既存最適化 | 補助的改善 |

---

## 新しい成功基準

| 指標 | 現在値 | 新目標 | 根拠 |
|------|--------|--------|------|
| 勝率 | 56% | **45-55%** | RR比重視のため低くてもOK |
| RR比 | 1.0-1.3 | **1.5-2.0** | 勝率50%でも利益可能 |
| PF | 1.01 | **1.3+** | RR比改善で達成 |
| 月次トレード数 | 244 | **100-150** | 質重視で減少許容 |

---

## 検証計画

```bash
# 現行システム
python -m autotrader.backtest --period 2023 --output current.json

# SMC 統合後
python -m autotrader.backtest --period 2023 --output smc.json
```

### ロールバック条件
- PF < 0.9
- 月次トレード数 < 50
- 最大DD > 10%

---

# Part 3: 詳細実装計画

## 統合ポイント（調査結果）

| 統合箇所 | ファイル | 行番号 | 変更内容 |
|----------|----------|--------|----------|
| 事前計算 | calculator/precompute.py | 73-162 | SMC指標の追加 |
| シグナル生成 | decision/unified/timeframe_evaluator.py | 124-291 | SMC要因のスコアリング |
| バックテスト | backtest/fast_backtest.py | 213-240 | シグナル処理（変更なし） |
| シミュレータ | backtest/simulator.py | 112-179 | TP/SL処理（変更なし） |

## 新規作成ファイル

```
src/autotrader/calculator/market_structure/
├── __init__.py
├── swing_analyzer.py        # スイングハイ/ロー検出
├── structure_analyzer.py    # BOS/CHoCH検出
├── liquidity_analyzer.py    # 流動性ゾーン検出
└── key_levels.py           # S/Rレベル検出
```

## 段階的実装

### Step 1: SwingAnalyzer（スイング検出）
```python
# swing_analyzer.py
class SwingAnalyzer:
    def detect_swing_high(self, df: pd.DataFrame, lookback: int = 5) -> pd.Series:
        """
        lookback本分の中で最高値ならSwing High
        Returns: bool Series
        """

    def detect_swing_low(self, df: pd.DataFrame, lookback: int = 5) -> pd.Series:
        """lookback本分の中で最低値ならSwing Low"""
```

### Step 2: StructureAnalyzer（BOS/CHoCH）
```python
# structure_analyzer.py
class StructureAnalyzer:
    def analyze(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        列追加:
        - swing_high: bool
        - swing_low: bool
        - structure_direction: 1(上昇)/-1(下降)/0(不明)
        - bos_signal: 1(強気BOS)/-1(弱気BOS)/0
        - choch_signal: 1(強気CHoCH)/-1(弱気CHoCH)/0
        """
```

### Step 3: LiquidityAnalyzer（流動性ゾーン）
```python
# liquidity_analyzer.py
class LiquidityAnalyzer:
    def find_liquidity_zones(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        列追加:
        - buy_side_liquidity: 直近の主要高値
        - sell_side_liquidity: 直近の主要安値
        - liquidity_grab: bool (流動性を取った後の反転)
        """
```

### Step 4: TimeframeEvaluator拡張
```python
# timeframe_evaluator.py の変更

def _evaluate_smc_factors(self, row: pd.Series, candle: Candle) -> tuple[float, float, list[str]]:
    """SMC要因をスコアリング"""
    buy_bonus = 0.0
    sell_bonus = 0.0
    reasons = []

    # BOS検出
    if row.get('bos_signal', 0) == 1:  # 強気BOS
        buy_bonus += 3.0
        reasons.append("強気BOS検出")
    elif row.get('bos_signal', 0) == -1:  # 弱気BOS
        sell_bonus += 3.0
        reasons.append("弱気BOS検出")

    # 流動性グラブ
    if row.get('liquidity_grab', False):
        if row.get('structure_direction', 0) == 1:
            buy_bonus += 2.0
            reasons.append("流動性グラブ後の反転")

    return buy_bonus, sell_bonus, reasons
```

### Step 5: 構造ベースTP/SL
```python
def _calculate_sl_tp(self, row, strength, plan):
    # 構造レベルチェック
    swing_low = row.get('nearest_swing_low')
    swing_high = row.get('nearest_swing_high')

    if swing_low and swing_high:
        # 構造ベースSL（優先）
        if direction == SignalType.BUY:
            sl_price = swing_low - buffer
            tp_price = row.get('buy_side_liquidity', swing_high)
        else:
            sl_price = swing_high + buffer
            tp_price = row.get('sell_side_liquidity', swing_low)
    else:
        # ATRベース（フォールバック）
        sl_pips = atr_pips * sl_mult
        tp_pips = sl_pips * tp_sl_ratio

    return sl_pips, tp_pips
```

---

## 実装順序（推奨）

| 順序 | タスク | 期間 | 依存関係 |
|------|--------|------|----------|
| 1 | SwingAnalyzer実装 + テスト | 1日 | なし |
| 2 | StructureAnalyzer実装 + テスト | 1日 | SwingAnalyzer |
| 3 | PrecomputeEngineへの統合 | 0.5日 | StructureAnalyzer |
| 4 | TimeframeEvaluator拡張 | 1日 | PrecomputeEngine |
| 5 | バックテスト + 調整 | 1日 | TimeframeEvaluator |
| 6 | LiquidityAnalyzer実装 | 1日 | StructureAnalyzer |
| 7 | TP/SL再設計 | 1日 | LiquidityAnalyzer |
| 8 | 最終バックテスト + 調整 | 1日 | 全て |

**合計: 約7-8日**

---

## 検証方法

### 1. 単体テスト
```bash
# 各モジュールのテスト
pytest tests/calculator/market_structure/ -v
```

### 2. 統合テスト
```bash
# SMC指標が正しく計算されるか
pytest tests/integration/test_smc_integration.py -v
```

### 3. バックテスト比較
```bash
# 現行システム
python -m autotrader.backtest --period 2023 --config current

# SMC統合版
python -m autotrader.backtest --period 2023 --config smc

# 比較スクリプト
python scripts/compare_backtest.py current.json smc.json
```

### 4. 成功基準
| 指標 | 現在値 | 目標値 | 許容範囲 |
|------|--------|--------|----------|
| 勝率 | 56% | **60-65%** | 55%以上 |
| RR比 | 1.0-1.3 | 1.5-2.0 | 1.3以上 |
| PF | 1.01 | 1.3+ | 1.1以上 |
| 月次トレード | 244 | 80-120 | 60以上 |

---

# Part 4: LLMによる「負けフィルター」（勝率向上）

## 目的

勝率45%では連敗リスクとブラックスワンに弱い。
LLMを使って「負けるトレード」を事前に除外し、勝率60-65%を目指す。

## アプローチ

```
現在のフロー:
シグナル発生 → 即エントリー（勝率56%）

新しいフロー:
シグナル発生 → LLM分析 → OK → エントリー（勝率65%目標）
                 ↓
              危険検出 → スキップ
```

## LLMが検出する「危険シグナル」

### 1. マクロイベントリスク
```python
危険シグナル:
- 重要経済指標発表前後30分（NFP、FOMC、CPI等）
- 中央銀行発言の予定
- 地政学リスク（戦争、選挙等）
```

### 2. 市場環境リスク
```python
危険シグナル:
- 異常なボラティリティ（ATRが平均の2倍以上）
- スプレッドの急拡大
- 流動性枯渇時間帯
```

### 3. ニュースセンチメント
```python
危険シグナル:
- ニュースセンチメントがトレンドと逆行
- 急なセンチメント変化
- 矛盾する情報が多い
```

### 4. テクニカル不整合
```python
危険シグナル:
- 複数タイムフレームで方向が不一致
- キーレベル直前（S/R、ラウンドナンバー）
- 過去に同パターンで失敗した履歴
```

---

## 実装計画

### Phase 10: LLMエントリーフィルター

#### 10.1 LLMフィルターモジュール
```python
# 新規作成: src/autotrader/decision/llm/entry_filter.py

class LLMEntryFilter:
    """LLMによるエントリーフィルター"""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    async def should_trade(
        self,
        signal: TimeframeSignal,
        market_context: MarketContext,
    ) -> tuple[bool, str]:
        """
        LLMに以下を分析させる:
        1. 現在の市場環境は安全か？
        2. シグナルの質は十分か？
        3. 見落としているリスクはないか？

        Returns:
            (can_trade: bool, reason: str)
        """
        prompt = self._build_analysis_prompt(signal, market_context)
        response = await self._call_llm(prompt)
        return self._parse_response(response)
```

#### 10.2 マーケットコンテキスト収集
```python
# 新規作成: src/autotrader/decision/llm/market_context.py

@dataclass
class MarketContext:
    """LLM分析用の市場コンテキスト"""

    # 価格情報
    current_price: float
    price_change_1h: float
    price_change_24h: float

    # テクニカル情報
    trend_direction: str
    rsi: float
    atr: float
    atr_percentile: float  # 過去のATRと比較

    # イベント情報
    upcoming_events: list[EconomicEvent]
    recent_news: list[NewsItem]

    # 市場構造
    nearest_support: float
    nearest_resistance: float
    liquidity_zones: list[float]
```

#### 10.3 プロンプトテンプレート
```python
ANALYSIS_PROMPT = """
あなたはプロのFXトレーダーです。以下のトレードシグナルを分析し、
エントリーすべきかどうかを判断してください。

## シグナル情報
- 方向: {direction}
- 確度: {confidence}
- 理由: {reasons}

## 市場環境
- 現在価格: {current_price}
- 1時間変動: {price_change_1h}%
- ATR（ボラティリティ）: {atr}（過去30日で{atr_percentile}パーセンタイル）

## 今後のイベント
{upcoming_events}

## 最近のニュース
{recent_news}

## 分析してください
1. このトレードに潜むリスクは何ですか？
2. 見落としている要因はありますか？
3. エントリーを推奨しますか？

回答形式:
DECISION: TRADE または SKIP
REASON: 理由を1-2文で
CONFIDENCE: 0.0-1.0
"""
```

#### 10.4 経済カレンダー連携
```python
# 新規作成: src/autotrader/data/economic_calendar.py

class EconomicCalendar:
    """経済イベントカレンダー"""

    async def get_upcoming_events(
        self,
        hours_ahead: int = 24,
        min_impact: str = "medium",
    ) -> list[EconomicEvent]:
        """
        今後のイベントを取得
        - Forex Factory API
        - Investing.com API
        等から取得
        """
```

---

## 新規作成ファイル（Phase 10）

```
src/autotrader/decision/llm/
├── __init__.py
├── entry_filter.py       # LLMエントリーフィルター
├── market_context.py     # マーケットコンテキスト
├── prompts.py           # プロンプトテンプレート
└── news_analyzer.py     # ニュース分析

src/autotrader/data/
├── economic_calendar.py  # 経済カレンダー
└── news_fetcher.py      # ニュース取得
```

---

## 期待される効果

| 指標 | SMCのみ | SMC + LLM | 改善 |
|------|---------|-----------|------|
| 勝率 | 50-55% | **60-65%** | +10% |
| トレード数 | 100-150 | 60-80 | -40% |
| PF | 1.2 | **1.5+** | +25% |

**トレードオフ**: トレード数は減少するが、質が大幅向上

---

## 修正後の目標設定

| 指標 | 現在値 | 最終目標 | 根拠 |
|------|--------|----------|------|
| 勝率 | 56% | **60-65%** | LLMフィルターで低品質トレード除外 |
| RR比 | 1.0-1.3 | **1.5-2.0** | 構造ベースTP/SL |
| PF | 1.01 | **1.5+** | 勝率×RR比の改善 |
| 月次トレード | 244 | **60-80** | 質重視で大幅減少許容 |

---

## 実装優先順位（最終版）

| 順序 | Phase | 内容 | 期待効果 |
|------|-------|------|----------|
| 1 | Phase 5 | 市場構造分析（Swing, BOS） | RR比向上 |
| 2 | Phase 7 | エントリーロジック再設計 | 質向上 |
| 3 | Phase 8 | TP/SL再設計 | RR比向上 |
| 4 | **Phase 10** | **LLMエントリーフィルター** | **勝率向上** |
| 5 | Phase 6 | 流動性ゾーン | 精度向上 |
| 6 | Phase 9 | 時間帯フィルター | リスク軽減 |

---

# Part 5: バックテスト用LLMシミュレーション

## 問題

バックテストで全トレードにLLM APIを呼び出すと:
- 2023年データで約2930トレード
- 各呼び出し1-2秒 → **数時間かかる**
- API費用も膨大

## 解決策（ハイブリッドアプローチ）

**確度による振り分け**:
```
高確度（0.7以上）: そのままエントリー（LLM不要）
中確度（0.4-0.7）: LLMに判断を委ねる（本番でもバックテストでも）
低確度（0.4未満）: スキップ（LLM不要）
```

これにより:
- LLM呼び出しは全体の**20-30%程度**に削減
- 2930トレード → LLM評価は約600-900件
- 約10-15分で完了（許容範囲）

---

## 確度ベースのLLMトリガー

```python
# src/autotrader/decision/llm/confidence_router.py

class ConfidenceRouter:
    """確度に基づいてLLM評価を振り分け"""

    HIGH_CONFIDENCE_THRESHOLD = 0.70  # 即エントリー
    LOW_CONFIDENCE_THRESHOLD = 0.40   # 即スキップ

    def route(
        self,
        signal: TimeframeSignal,
    ) -> Literal["TRADE", "SKIP", "ASK_LLM"]:
        """
        確度に基づいて振り分け
        """
        if signal.confidence >= self.HIGH_CONFIDENCE_THRESHOLD:
            return "TRADE"  # LLM不要、即エントリー
        elif signal.confidence < self.LOW_CONFIDENCE_THRESHOLD:
            return "SKIP"   # LLM不要、即スキップ
        else:
            return "ASK_LLM"  # LLMに判断を委ねる
```

---

## バックテストでのLLM呼び出し（実際のAPI）

```python
# src/autotrader/backtest/llm_backtest.py

class LLMBacktestEngine:
    """LLM評価付きバックテストエンジン"""

    def __init__(
        self,
        config: BacktestConfig,
        llm_api_key: str,
        batch_size: int = 50,  # バッチ処理で効率化
    ):
        self.router = ConfidenceRouter()
        self.llm_filter = LLMEntryFilter(llm_api_key)
        self.batch_size = batch_size

    async def run_backtest(self) -> BacktestResult:
        """
        1. 全シグナルを生成
        2. 確度で振り分け
        3. 中確度シグナルをバッチでLLM評価
        4. シミュレーション実行
        """
        # Phase 1: シグナル生成
        signals = self._generate_all_signals()

        # Phase 2: 振り分け
        high_conf = []  # 即エントリー
        low_conf = []   # 即スキップ
        mid_conf = []   # LLM評価対象

        for signal in signals:
            route = self.router.route(signal)
            if route == "TRADE":
                high_conf.append(signal)
            elif route == "SKIP":
                low_conf.append(signal)
            else:
                mid_conf.append(signal)

        print(f"高確度: {len(high_conf)}, 中確度: {len(mid_conf)}, 低確度: {len(low_conf)}")

        # Phase 3: 中確度シグナルをLLM評価（バッチ処理）
        llm_approved = await self._batch_llm_evaluation(mid_conf)

        # Phase 4: エントリー対象を統合
        final_signals = high_conf + llm_approved

        # Phase 5: シミュレーション
        return self._run_simulation(final_signals)

    async def _batch_llm_evaluation(
        self,
        signals: list[TimeframeSignal],
    ) -> list[TimeframeSignal]:
        """
        バッチ処理でLLM評価（並列化）
        """
        approved = []

        for batch_start in range(0, len(signals), self.batch_size):
            batch = signals[batch_start:batch_start + self.batch_size]

            # 並列でLLM呼び出し
            tasks = [
                self.llm_filter.should_trade(sig, self._get_context(sig))
                for sig in batch
            ]
            results = await asyncio.gather(*tasks)

            for sig, (should_trade, reason) in zip(batch, results):
                if should_trade:
                    approved.append(sig)

            # 進捗表示
            print(f"LLM評価: {batch_start + len(batch)}/{len(signals)}")

        return approved
```

---

## バックテスト用シミュレーションフィルター

### 1. 経済イベントフィルター（過去データ）
```python
# 新規作成: src/autotrader/backtest/filters/event_filter.py

class EventFilter:
    """経済イベントベースのフィルター"""

    def __init__(self, calendar_data: pd.DataFrame):
        """
        calendar_data: 過去の経済カレンダー（CSV等から読み込み）
        - datetime, event_name, currency, impact(high/medium/low)
        """
        self.calendar = calendar_data

    def should_skip(self, timestamp: datetime, symbol: str) -> tuple[bool, str]:
        """
        高インパクトイベント前後30分はスキップ
        """
        currency = symbol[:3]  # USD, EUR等
        window_start = timestamp - timedelta(minutes=30)
        window_end = timestamp + timedelta(minutes=30)

        events = self.calendar[
            (self.calendar['datetime'] >= window_start) &
            (self.calendar['datetime'] <= window_end) &
            (self.calendar['currency'] == currency) &
            (self.calendar['impact'] == 'high')
        ]

        if len(events) > 0:
            return True, f"高インパクトイベント: {events.iloc[0]['event_name']}"
        return False, ""
```

### 2. ボラティリティフィルター
```python
# 新規作成: src/autotrader/backtest/filters/volatility_filter.py

class VolatilityFilter:
    """異常ボラティリティフィルター"""

    def __init__(self, atr_threshold_percentile: float = 90):
        self.threshold = atr_threshold_percentile

    def should_skip(self, row: pd.Series, df_history: pd.DataFrame) -> tuple[bool, str]:
        """
        ATRが過去30日の90パーセンタイルを超えたらスキップ
        """
        current_atr = row.get('atr_14', 0)
        atr_percentile = (df_history['atr_14'] < current_atr).mean() * 100

        if atr_percentile > self.threshold:
            return True, f"異常ボラティリティ (ATR {atr_percentile:.0f}パーセンタイル)"
        return False, ""
```

### 3. セッションフィルター
```python
# 新規作成: src/autotrader/backtest/filters/session_filter.py

class SessionFilter:
    """取引セッションフィルター"""

    LOW_LIQUIDITY_HOURS = [
        (21, 23),  # NYクローズ後
        (0, 7),    # アジア深夜
    ]

    def should_skip(self, timestamp: datetime) -> tuple[bool, str]:
        """
        低流動性時間帯はスキップ
        """
        hour = timestamp.hour
        for start, end in self.LOW_LIQUIDITY_HOURS:
            if start <= hour < end:
                return True, f"低流動性時間帯 ({hour}時UTC)"
        return False, ""
```

### 4. パターン学習フィルター（過去データから学習）
```python
# 新規作成: src/autotrader/backtest/filters/pattern_filter.py

class PatternFilter:
    """過去の類似パターンから学習"""

    def __init__(self, historical_trades: pd.DataFrame):
        """
        historical_trades: 過去のトレード結果
        - pattern_features, outcome(win/loss)
        """
        self.model = self._train_model(historical_trades)

    def should_skip(self, features: dict) -> tuple[bool, str]:
        """
        類似パターンの過去勝率が40%未満ならスキップ
        """
        predicted_win_rate = self.model.predict_proba(features)

        if predicted_win_rate < 0.40:
            return True, f"低勝率パターン (予測勝率 {predicted_win_rate:.0%})"
        return False, ""
```

---

## 統合フィルターマネージャー

```python
# 新規作成: src/autotrader/backtest/filters/filter_manager.py

class BacktestFilterManager:
    """バックテスト用フィルターの統合管理"""

    def __init__(
        self,
        calendar_path: str,
        use_event_filter: bool = True,
        use_volatility_filter: bool = True,
        use_session_filter: bool = True,
        use_pattern_filter: bool = False,  # オプション
    ):
        self.filters = []

        if use_event_filter:
            calendar = pd.read_csv(calendar_path)
            self.filters.append(EventFilter(calendar))

        if use_volatility_filter:
            self.filters.append(VolatilityFilter())

        if use_session_filter:
            self.filters.append(SessionFilter())

        # パターンフィルターは学習データが必要
        if use_pattern_filter:
            self.filters.append(PatternFilter(historical_trades))

    def should_skip(
        self,
        timestamp: datetime,
        row: pd.Series,
        symbol: str,
        df_history: pd.DataFrame,
    ) -> tuple[bool, str]:
        """
        いずれかのフィルターがスキップを返したらスキップ
        """
        for filter in self.filters:
            skip, reason = filter.should_skip(...)
            if skip:
                return True, reason
        return False, ""
```

---

## fast_backtest.py への統合

```python
# src/autotrader/backtest/fast_backtest.py の変更

class FastBacktestEngine:
    def __init__(
        self,
        config: BacktestConfig,
        llm_filter_enabled: bool = True,  # 新規パラメータ
    ):
        # ... 既存の初期化

        # LLMフィルターシミュレーション
        if llm_filter_enabled:
            self.filter_manager = BacktestFilterManager(
                calendar_path=config.economic_calendar_path,
                use_event_filter=True,
                use_volatility_filter=True,
                use_session_filter=True,
            )
        else:
            self.filter_manager = None

    def _process_signal(self, signal, timestamp, row, df_history):
        """シグナル処理時にフィルターを適用"""

        # LLMフィルターシミュレーション
        if self.filter_manager:
            skip, reason = self.filter_manager.should_skip(
                timestamp=timestamp,
                row=row,
                symbol=self.config.symbol,
                df_history=df_history,
            )
            if skip:
                # フィルターでスキップ
                return None, f"LLMフィルター: {reason}"

        # 通常のシグナル処理
        return signal, ""
```

---

## ライブトレード用LLMフィルター

```python
# src/autotrader/decision/llm/entry_filter.py（ライブ用）

class LiveLLMFilter:
    """ライブトレード用の実際のLLMフィルター"""

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    async def should_trade(
        self,
        signal: TimeframeSignal,
        market_context: MarketContext,
    ) -> tuple[bool, str]:
        """
        実際のLLM APIを呼び出して判断
        1分足の間隔があるので十分な時間あり
        """
        prompt = self._build_prompt(signal, market_context)
        response = await self.client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        return self._parse_response(response)
```

---

## 経済カレンダーデータの準備

### データソース
1. **Forex Factory**: https://www.forexfactory.com/calendar
2. **Investing.com**: https://www.investing.com/economic-calendar/
3. **DailyFX**: https://www.dailyfx.com/economic-calendar

### CSVフォーマット
```csv
datetime,event_name,currency,impact,actual,forecast,previous
2023-01-06 13:30:00,Non-Farm Payrolls,USD,high,223K,200K,256K
2023-01-11 13:30:00,CPI m/m,USD,high,0.1%,0.0%,0.1%
...
```

### データ取得スクリプト
```python
# scripts/fetch_economic_calendar.py

async def fetch_calendar(start_date: str, end_date: str) -> pd.DataFrame:
    """
    過去の経済カレンダーを取得
    バックテスト用のCSVを生成
    """
```

---

## 新規作成ファイル（Part 5）

```
src/autotrader/backtest/filters/
├── __init__.py
├── filter_manager.py     # 統合フィルター管理
├── event_filter.py       # 経済イベントフィルター
├── volatility_filter.py  # ボラティリティフィルター
├── session_filter.py     # セッションフィルター
└── pattern_filter.py     # パターン学習フィルター（オプション）

data/
└── economic_calendar_2023.csv  # 過去の経済カレンダー

scripts/
└── fetch_economic_calendar.py  # カレンダー取得スクリプト
```

---

## バックテスト実行例

```bash
# LLMフィルターシミュレーション有効
python -m autotrader.backtest \
    --period 2023 \
    --llm-filter \
    --calendar data/economic_calendar_2023.csv \
    --output results_with_llm_filter.json

# LLMフィルターなし（比較用）
python -m autotrader.backtest \
    --period 2023 \
    --output results_baseline.json
```

---

## 期待される効果（シミュレーション）

| フィルター | スキップ率 | 勝率改善 |
|-----------|-----------|----------|
| イベントフィルター | 5-10% | +2-3% |
| ボラティリティフィルター | 10-15% | +3-5% |
| セッションフィルター | 20-30% | +2-3% |
| **合計** | **30-40%** | **+7-10%** |

勝率: 56% → 63-66%（シミュレーション）

---

# Part 6: LLMによるTP/SL計算と乖離分析

## コンセプト

**ロジック計算**と**LLM計算**を並行して行い、乖離を分析することで:
1. ロジックが見落としているリスクを検出
2. より精度の高いTP/SLを設定
3. 両者のアンサンブルで最適化

```
シグナル発生
    │
    ├─→ ロジック計算 → SL: 20pips, TP: 40pips
    │
    └─→ LLM計算 → SL: 25pips, TP: 35pips
            │
            ▼
       乖離分析
            │
       ┌────┴────┐
       │         │
    小乖離     大乖離
       │         │
    ロジック採用  アンサンブル or 警告
```

---

## LLMへの入力情報

```python
# src/autotrader/decision/llm/sl_tp_analyzer.py

class LLMSLTPAnalyzer:
    """LLMによるSL/TP計算"""

    def build_analysis_prompt(
        self,
        candles: list[Candle],      # 直近50-100本のローソク足
        current_signal: TimeframeSignal,
        indicators: dict,            # RSI, MACD, ADX等
        market_structure: dict,      # スイングポイント、S/R等
    ) -> str:
        """
        LLMに渡すプロンプトを構築
        """
        return f"""
あなたはプロのFXトレーダーです。以下の情報を分析し、最適なエントリーポイント、
ストップロス、テイクプロフィットを計算してください。

## 現在のシグナル
- 方向: {current_signal.direction}
- 確度: {current_signal.confidence}
- 時間足: {current_signal.timeframe}

## 直近のローソク足データ（新しい順）
{self._format_candles(candles[-20:])}

## テクニカル指標
- RSI(14): {indicators['rsi']:.1f}
- MACD: {indicators['macd']:.5f} / Signal: {indicators['macd_signal']:.5f}
- ADX: {indicators['adx']:.1f}
- ATR(14): {indicators['atr']:.5f} ({indicators['atr_pips']:.1f}pips)

## 市場構造
- 直近スイングハイ: {market_structure['swing_high']}
- 直近スイングロー: {market_structure['swing_low']}
- 最寄りのサポート: {market_structure['support']}
- 最寄りのレジスタンス: {market_structure['resistance']}
- トレンド状態: {market_structure['trend_state']}

## 分析してください
1. 推奨エントリー価格（現在価格 or 指値）
2. 推奨ストップロス価格と理由
3. 推奨テイクプロフィット価格と理由
4. リスクリワード比
5. この設定の自信度（0.0-1.0）

回答形式（JSON）:
{{
    "entry_price": 141.500,
    "stop_loss": 141.200,
    "take_profit": 142.100,
    "sl_reason": "直近スイングローの下に設定",
    "tp_reason": "次のレジスタンスレベル",
    "risk_reward": 2.0,
    "confidence": 0.75,
    "warnings": ["ニュース発表まで2時間", "ボラティリティ高め"]
}}
"""
```

---

## 乖離分析ロジック

```python
# src/autotrader/decision/llm/divergence_analyzer.py

class DivergenceAnalyzer:
    """ロジック計算とLLM計算の乖離分析"""

    DIVERGENCE_THRESHOLD_PIPS = 10  # 10pips以上の乖離で警告

    def analyze(
        self,
        logic_result: SLTPResult,
        llm_result: SLTPResult,
        current_price: float,
    ) -> DivergenceResult:
        """
        乖離を分析し、最終的なSL/TPを決定
        """
        # SLの乖離（pips）
        sl_divergence = abs(logic_result.sl_pips - llm_result.sl_pips)

        # TPの乖離（pips）
        tp_divergence = abs(logic_result.tp_pips - llm_result.tp_pips)

        # 乖離度
        divergence_level = max(sl_divergence, tp_divergence)

        if divergence_level < self.DIVERGENCE_THRESHOLD_PIPS:
            # 小乖離: ロジックを採用
            return DivergenceResult(
                final_sl=logic_result.sl_pips,
                final_tp=logic_result.tp_pips,
                method="logic",
                divergence_pips=divergence_level,
                warnings=[],
            )
        else:
            # 大乖離: アンサンブル or 保守的な方を採用
            return self._resolve_divergence(logic_result, llm_result)

    def _resolve_divergence(
        self,
        logic: SLTPResult,
        llm: SLTPResult,
    ) -> DivergenceResult:
        """
        大きな乖離を解決する戦略
        """
        # 戦略1: より保守的な方を採用（SLは広く、TPは近く）
        conservative_sl = max(logic.sl_pips, llm.sl_pips)
        conservative_tp = min(logic.tp_pips, llm.tp_pips)

        # 戦略2: 加重平均（LLM確度が高ければLLM寄り）
        llm_weight = llm.confidence
        logic_weight = 1 - llm_weight
        weighted_sl = logic.sl_pips * logic_weight + llm.sl_pips * llm_weight
        weighted_tp = logic.tp_pips * logic_weight + llm.tp_pips * llm_weight

        # 戦略3: LLMが警告を出していればスキップ
        if llm.warnings:
            return DivergenceResult(
                final_sl=None,
                final_tp=None,
                method="skip",
                divergence_pips=max(abs(logic.sl_pips - llm.sl_pips),
                                     abs(logic.tp_pips - llm.tp_pips)),
                warnings=llm.warnings + ["大きな乖離によりスキップ推奨"],
            )

        # デフォルト: 保守的な方を採用
        return DivergenceResult(
            final_sl=conservative_sl,
            final_tp=conservative_tp,
            method="conservative",
            divergence_pips=max(abs(logic.sl_pips - llm.sl_pips),
                                 abs(logic.tp_pips - llm.tp_pips)),
            warnings=[f"乖離あり: SL {abs(logic.sl_pips - llm.sl_pips):.1f}pips, "
                      f"TP {abs(logic.tp_pips - llm.tp_pips):.1f}pips"],
        )
```

---

## 統合フロー

```python
# src/autotrader/decision/llm/hybrid_decision.py

class HybridDecisionEngine:
    """ロジック + LLM のハイブリッド意思決定エンジン"""

    async def make_decision(
        self,
        signal: TimeframeSignal,
        candles: list[Candle],
        indicators: dict,
        market_structure: dict,
    ) -> TradeDecision:
        """
        1. ロジックでSL/TP計算
        2. LLMでSL/TP計算（中確度シグナルのみ）
        3. 乖離分析
        4. 最終決定
        """
        # Step 1: ロジック計算
        logic_result = self.logic_calculator.calculate(
            signal=signal,
            candles=candles,
            indicators=indicators,
            market_structure=market_structure,
        )

        # Step 2: 確度による振り分け
        route = self.router.route(signal)

        if route == "SKIP":
            return TradeDecision(action="skip", reason="低確度")

        if route == "TRADE":
            # 高確度: ロジックのみ
            return TradeDecision(
                action="trade",
                sl_pips=logic_result.sl_pips,
                tp_pips=logic_result.tp_pips,
                method="logic_only",
            )

        # Step 3: 中確度 → LLM計算
        llm_result = await self.llm_analyzer.calculate(
            candles=candles,
            signal=signal,
            indicators=indicators,
            market_structure=market_structure,
        )

        # Step 4: 乖離分析
        divergence = self.divergence_analyzer.analyze(
            logic_result=logic_result,
            llm_result=llm_result,
            current_price=candles[-1].close,
        )

        # Step 5: 最終決定
        if divergence.method == "skip":
            return TradeDecision(
                action="skip",
                reason=", ".join(divergence.warnings),
            )

        return TradeDecision(
            action="trade",
            sl_pips=divergence.final_sl,
            tp_pips=divergence.final_tp,
            method=divergence.method,
            warnings=divergence.warnings,
            llm_analysis=llm_result,
            logic_analysis=logic_result,
        )
```

---

## 期待される効果

### 乖離パターンと対応

| 乖離パターン | 頻度 | 対応 | 効果 |
|-------------|------|------|------|
| 小乖離（<10pips） | 60% | ロジック採用 | 高速処理 |
| 中乖離（10-20pips） | 25% | アンサンブル | 精度向上 |
| 大乖離（>20pips） | 15% | スキップ or 警告 | リスク回避 |

### 改善指標

| 指標 | ロジックのみ | LLM乖離分析あり |
|------|-------------|-----------------|
| SLヒット率 | 44% | **38%**（-6%） |
| 平均損失 | -1.2% | **-0.9%**（-25%） |
| TPヒット率 | 56% | **62%**（+6%） |
| 平均利益 | +1.8% | **+2.1%**（+17%） |

---

## 新規作成ファイル（Part 6）

```
src/autotrader/decision/llm/
├── sl_tp_analyzer.py      # LLMによるSL/TP計算
├── divergence_analyzer.py # 乖離分析
├── hybrid_decision.py     # ハイブリッド意思決定
└── prompts/
    └── sl_tp_prompt.py    # SL/TP計算用プロンプト
```

---

## 実装の優先順位（最終版）

| 順序 | Phase | 内容 | 依存関係 |
|------|-------|------|----------|
| 1 | Phase 5 | 市場構造分析 | なし |
| 2 | Phase 7 | エントリーロジック | Phase 5 |
| 3 | Phase 8 | TP/SL再設計（ロジック） | Phase 5, 7 |
| 4 | Phase 10 | LLMエントリーフィルター | Phase 7 |
| 5 | **Phase 11** | **LLM SL/TP + 乖離分析** | Phase 8, 10 |
| 6 | Phase 6 | 流動性ゾーン | Phase 5 |
| 7 | Phase 9 | 時間帯フィルター | なし |
