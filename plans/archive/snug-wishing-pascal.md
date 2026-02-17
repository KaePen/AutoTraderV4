# 設計バグ修正計画: レジーム・モード・HTFの根本的欠陥

## Context

トレードフロー分析レポート（2023年）で以下の**設計レベルの実装バグ**が判明した。パラメータ微調整以前に、プログラムが設計通りに動いていないため、まずこれを修正する。

## 発見されたバグ一覧

### Bug A: レジーム検出が常にRANGE（致命的）

**現象**: 全74,482バーでレジームが100% RANGE

**根本原因**: `BacktestRunner._calculate_indicators()` (runner.py:190-244) が `normalized_atr` と `ma_alignment` を計算していない。

`regime_detector.py:detect_from_row()` は以下カラムを探す:
- `normalized_atr` → **存在しない** → デフォルト `1.0`
- `adx` → 存在する（`adx_cols`で検出）
- `ma_alignment` → **存在しない** → デフォルト `0.0`

デフォルト値 `(1.0, any_adx, 0.0)` での判定結果:
| 判定 | 条件 | 結果 |
|------|------|------|
| HIGH_VOL | `1.0 > 1.5` | **常にFALSE** |
| TREND | `adx >= 20 AND abs(0.0) > 0.3` | **常にFALSE**（ma_alignment=0） |
| LOW_VOL | `1.0 < 0.7` | **常にFALSE** |
| **RANGE** | デフォルト | **常にTRUE** |

### Bug B: モード選択が常にDAY_TRADE（Bug Aの連鎖）

**現象**: SCALPING 0%, DAY_TRADE 100%, SWING 0%

**根本原因**: Bug AでレジームがRANGEのため、`mode_selector.py:_select_mode()` の条件分岐:
- SCALPING条件 `regime == HIGH_VOL` → 不成立（常にRANGE）
- SCALPING条件 `volatility_level > 1.3` → `volatility_level`もRegimeResultから来るので不正確
- SWING条件 `regime == TREND` → 不成立（常にRANGE）
- **最終行 `return DAY_TRADE`** に常に到達

加えて `_get_htf_alignment()` も `ma_alignment` カラム不在で常に0.0を返す。

### Bug C: HTFスコアリングが常に同じ値を返す（致命的）

**現象**: BUY偏重 (BUY 27,618 vs SELL 12,220 = 2.3倍)

**根本原因**: `timeframe_evaluator.py:_score_htf_alignment()` (行545-592) が `df.iloc[-1]` を使用。

```python
for tf, df in self._htf_data.items():
    latest = df.iloc[-1]  # ← データフレーム全体の最終行 = 2023年末の値
```

`set_higher_tf_data()` は**データフレーム全体**を渡す。バックテスト中のどの時点でも `iloc[-1]` は**同じ2023年末の行**。2023年末がBUY方向だった場合、年間通してBUYにHTFボーナス(+2〜+4点)が付与される。

**対比**: `trade_bot.py:_check_htf_trend_alignment()` は `_get_current_row(tf, current_time)` を正しく使用 → 時刻に応じた行を取得。

### Bug D: RSIボーナスの非対称性（軽微）

**現象**: BUY偏重の一因

**詳細**: `timeframe_evaluator.py:_calculate_score()` 行273-276:
- BUY: `40 <= rsi <= 70` (30ポイント範囲)
- SELL: `35 <= rsi <= 60` (25ポイント範囲)

BUY側が20%広い受容範囲。

## 修正計画

### Step 1: `_calculate_indicators`に不足カラムを追加

**ファイル**: `src/autotrader/backtest/runner.py` `_calculate_indicators()`

追加するカラム:
```python
# normalized_atr（ATRを20期間平均で正規化）
df["normalized_atr"] = df["atr_14"] / df["atr_14"].rolling(20).mean()

# ma_alignment（MA整列度: -1〜1）
# close > sma_20 > sma_50 → +1方向
# close < sma_20 < sma_50 → -1方向
sma_diff = (df["sma_20"] - df["sma_50"]) / df["sma_50"]
price_diff = (df["close"] - df["sma_20"]) / df["sma_20"]
df["ma_alignment"] = (sma_diff + price_diff).clip(-1, 1)

# ema_12, ema_26（_calculate_scoreで使用、現状Noneで無視されている）
df["ema_12"] = ta.ema(df["close"], length=12)
df["ema_26"] = ta.ema(df["close"], length=26)
```

### Step 2: `_score_htf_alignment`を現在時刻基準に修正

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py` `_score_htf_alignment()`

**現状の問題**:
```python
latest = df.iloc[-1]  # 常に2023年末
```

**修正方針**: 評価器がevaluate()呼び出し時に現在時刻を受け取っているので、それを利用して`_htf_data`から該当時刻の行を取得する。

```python
# evaluate()からcurrent_timeを_score_htf_alignmentに伝搬
# _htf_dataのDataFrameからcurrent_time以前の最新行を取得
```

具体的には:
1. `evaluate()`内で`_current_eval_time`を保持
2. `_score_htf_alignment()`で`_current_eval_time`を使い、`df.loc[:current_time].iloc[-1]`でHTF行を取得

### Step 3: RSIボーナス範囲を対称化

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py` `_calculate_score()`

```python
# 修正前
if buy_score > 0 and 40 <= rsi <= 70:
    buy_score += 1.0
elif sell_score > 0 and 35 <= rsi <= 60:
    sell_score += 1.0

# 修正後（対称化: 30-70）
if buy_score > 0 and 30 <= rsi <= 70:
    buy_score += 1.0
elif sell_score > 0 and 30 <= rsi <= 70:
    sell_score += 1.0
```

## 変更ファイル一覧

| ファイル | 変更内容 | 影響度 |
|---------|---------|-------|
| `src/autotrader/backtest/runner.py` | `_calculate_indicators`に`normalized_atr`, `ma_alignment`, `ema_12/26`追加 | **致命的修正** |
| `src/autotrader/decision/unified/timeframe_evaluator.py` | `_score_htf_alignment`を現在時刻基準に修正、RSI対称化 | **致命的修正** |

## 検証方法

```bash
# 1. フロー分析で修正効果確認
uv run python scripts/run_trade_flow_analysis.py --years 2023

# 確認ポイント:
# - レジーム分布: TREND/RANGE/HIGH_VOL/LOW_VOLに分散
# - モード分布: SCALPING/DAY_TRADE/SWINGに分散
# - BUY/SELL比: 概ね1:1に近づく
# - [6.0+)帯以外の勝率がランダム(50%)に近いなら閾値の問題

# 2. 通常バックテストで回帰確認
uv run python scripts/run_backtest.py --years 2023
```

## 修正しないもの（次フェーズ）

- コンセンサス閾値の調整（DAY_TRADE=4.5等）→ バグ修正後のデータで判断
- TP/SL比率の調整 → 上記に同じ
- シグナル発生率53.5%の問題 → Bug C修正でHTFボーナスが適正化されれば自然に減少する見込み
