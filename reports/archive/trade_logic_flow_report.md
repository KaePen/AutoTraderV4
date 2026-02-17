# AutoTraderV4 トレードロジック フロー図 & パラメータ詳細レポート

**生成日**: 2026-02-08
**対象アーキテクチャ**: 新アーキテクチャ (`_generate_signal_new`)
**現在のベスト設定**: v4+stoch (PF 1.17, 勝率50.5%, Sharpe 2.17)

---

## 1. 全体フロー図

```
┌─────────────────────────────────────────────────────────────────┐
│                  毎分トリガー (バックテスト/ライブ)                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│              UnifiedTradeBot.generate_signal()                   │
│           routing: _generate_signal_new() を使用                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
  │ PHASE 1      │   │ PHASE 2      │   │ PHASE 3          │
  │ セットアップ    │   │ リスクチェック   │   │ TF評価            │
  │              │   │              │   │                  │
  │ 1. 日次リセット │   │ 5. リスク管理  │   │ 6. TFセット取得    │
  │ 2. レジーム検出 │   │    判定       │   │ 7. 各TFスコア算出  │
  │ 3. HTF整合計算 │   │    NG→HOLD   │   │                  │
  │ 4. モード選択  │   │              │   │                  │
  └──────┬───────┘   └──────┬───────┘   └────────┬─────────┘
         │                  │                     │
         └──────────────────┼─────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 4: コンセンサス形成                                        │
│  8. ModeAwareScoreConsensus.consolidate()                       │
│     - TF毎の加重スコア集計                                        │
│     - モード別閾値判定 (SCALP:3.5, DAY:4.5, SWING:6.0)            │
│     - 閾値未満 → HOLD                                           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 5: フィルター群                                           │
│  9.  HTFトレンド整合 (H4,D1) : aligned_score >= 0.8 必要          │
│  10. SoftGuard (スプレッド/時間帯/ボラティリティペナルティ)             │
│  11. RANGE+DAY制限 (RANGE + DAY_TRADE + trend<0.3 → HOLD)       │
│      NG → HOLD                                                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 6: SL/TP算出 & ポジションサイジング                          │
│  12. primary_tf のシグナルから SL/TP 取得                          │
│  13. TP/SL比率をモード設定で調整                                    │
│  14. ポジションサイズ計算 (有効時)                                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 7: ConsolidatedSignal 出力                               │
│  direction, confidence, sl_pips, tp_pips, regime, mode,         │
│  consensus_score, tf_score_breakdowns                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 各コンポーネント詳細

### 2.1 マーケットレジーム検出器 (RegimeDetector)

**ファイル**: `src/autotrader/calculator/features/regime_detector.py`
**入力**: H1データ (normalized_atr, adx, ma_alignment)

```
判定ロジック（優先順位順）:

  normalized_atr > 1.5 AND adx < 25  →  HIGH_VOL
  adx >= 20 AND |ma_alignment| > 0.3 →  TREND (adx>=30で強トレンド)
  normalized_atr < 0.7               →  LOW_VOL
  その他                              →  RANGE
```

| パラメータ | デフォルト値 | 説明 |
|-----------|------------|------|
| high_vol_atr_threshold | 1.5 | 高ボラティリティ判定ATR |
| low_vol_atr_threshold | 0.7 | 低ボラティリティ判定ATR |
| trend_adx_threshold | 20.0 | トレンド判定ADX |
| strong_trend_adx_threshold | 30.0 | 強トレンド判定ADX |
| ma_alignment_threshold | 0.3 | MA整合判定閾値 |

**出力**: `RegimeResult(regime, trend_strength, volatility_level, adx, confidence, reasoning)`

---

### 2.2 モードセレクター (TradingModeSelector)

**ファイル**: `src/autotrader/decision/unified/mode_selector.py`
**入力**: regime, volatility_level, htf_alignment, hour_utc

```
モード選択ロジック:

  HIGH_VOL + アクティブセッション    →  SCALPING
  HIGH_VOL + 非アクティブ           →  DAY_TRADE
  高ボラティリティ (> 1.3)          →  SCALPING
  TREND + HTF整合 (> 0.5)          →  SWING
  TREND + アクティブ + vol > 0.8    →  SCALPING
  TREND (その他)                    →  DAY_TRADE
  RANGE / LOW_VOL                  →  DAY_TRADE
```

**アクティブセッション時間 (UTC)**:
- 東京: 0-3 (JST 9-12)
- ロンドン: 7-10
- NY: 13-18

#### モード別設定

| 設定項目 | SCALPING | DAY_TRADE | SWING |
|---------|----------|-----------|-------|
| primary_tf | M5 | M15 | H4 |
| entry_tf | M1 | M5 | H1 |
| confirm_tfs | [M15] | [H1, H4] | [D1] |
| max_holding_bars | 18 (90分) | 32 (8時間) | 12 (2日) |
| tp_sl_ratio | 1.0-1.3 | 1.1-1.4 | 1.2-1.6 |

---

### 2.3 タイムフレーム評価器 (TimeframeEvaluator)

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py`
**入力**: 各TFのインジケータデータ (row)
**最大スコア**: 15.0

```
スコアリングフロー (BUY例):

  ┌─────────────────────────────────────────────────────┐
  │  1. トレンド検出 (0 ~ 5.0)                           │
  │     close > sma_20 > sma_50 + MACD bullish = 5.0    │
  │     上昇 + MACD bullish = 4.0                        │
  │     上昇トレンドのみ = 2.5                             │
  │                                                     │
  │  2. ADXボーナス (+2.0)                               │
  │     ADX > 20 → +2.0                                 │
  │                                                     │
  │  3. RSIフィルター (+1.0 / -999)                      │
  │     RSI > 80 (買い) → -999 (ブロック)                 │
  │     RSI 30-70 → +1.0                                │
  │                                                     │
  │  4. MACDヒストグラム傾斜 (+2.5 / -2.0)  ⭐v4追加     │
  │     順方向 → +2.5 / 逆方向 → -2.0                    │
  │                                                     │
  │  5. ダイバージェンス (+1.5 / -2.0)  ⭐v4追加          │
  │     順方向 → +1.5 / 逆方向 → -2.0                    │
  │                                                     │
  │  6. EMAクロス (+0.5 / -2.5)  ⭐v4追加                │
  │     EMA12>EMA26 (買い) → +0.5 / 逆 → -2.5           │
  │                                                     │
  │  7. ストキャスティクス (+0.5 / -1.5)  ⭐最新追加       │
  │     過買(>80) → -1.5 / 好zone → +0.5                │
  │                                                     │
  │  8. HTF整合ボーナス (+2.0 ~ +4.0)                    │
  │     2+TF整合 → +4.0 / 1TF整合 → +2.0                │
  └─────────────────────────────────────────────────────┘
```

**非対称性**: 逆方向ペナルティ > 順方向ボーナス（これが改善の鍵）

#### スコア詳細テーブル

| コンポーネント | BUY順方向 | SELL順方向 | 逆方向ペナルティ | 条件 |
|-------------|----------|----------|--------------|------|
| トレンド | +2.5~5.0 | +2.5~5.0 | 0 | SMA/MACD |
| ADX | +2.0 | +2.0 | 0 | ADX > 20 |
| RSI | +1.0 | +1.0 | -999 (ブロック) | 30-70 / >80 <20 |
| MACDスロープ | +2.5 | +2.5 | -2.0 | histogram slope |
| ダイバージェンス | +1.5 | +1.5 | -2.0 | RSI divergence |
| EMAクロス | +0.5 | +0.5 | -2.5 | EMA12 vs EMA26 |
| ストキャスティクス | +0.5 | +0.5 | -1.5 | K/D position |
| HTF整合 | +2.0~4.0 | +2.0~4.0 | 0 | HTF alignment |

#### TF別最低スコア閾値

| タイムフレーム | 正規化閾値 | 実質点数 |
|-------------|----------|---------|
| M1 | 0.10 | 1.5 |
| M5 | 0.12 | 1.8 |
| M15 | 0.14 | 2.1 |
| H1 | 0.16 | 2.4 |
| H4 | 0.18 | 2.7 |
| D1 | 0.20 | 3.0 |

#### ノイズフィルター (M1/M5のみ)
- 最低ADX: M1=10, M5=8
- ATR比率範囲: 0.3 ~ 3.5

---

### 2.4 モード対応コンセンサス (ModeAwareScoreConsensus)

**ファイル**: `src/autotrader/decision/unified/mode_aware_consensus.py`
**入力**: 各TFのTimeframeSignal + TradingPlan

#### ロール別重み

| ロール | SCALPING | DAY_TRADE | SWING |
|-------|----------|-----------|-------|
| PRIMARY | 2.0 | 3.0 | 3.5 |
| ENTRY | 3.0 | 2.5 | 2.0 |
| CONFIRM | 2.5 | 2.0 | 2.5 |
| MANAGE | 1.0 | 1.5 | 1.5 |

#### コンセンサス閾値

| モード | 閾値 | 説明 |
|-------|------|------|
| SCALPING | 3.5 (※設定は4.0) | 低め設定で取引数確保 |
| DAY_TRADE | 4.5 (※設定は5.5) | バランス型 |
| SWING | 6.0 | 厳選シグナルのみ |

**計算式**:
```
buy_score  = Σ (weight × direction_value × strength)  [BUYシグナルのTF]
sell_score = Σ (weight × direction_value × strength)  [SELLシグナルのTF]

final_score = max(buy_score, sell_score)
if final_score < threshold → HOLD
```

---

### 2.5 HTFトレンド整合フィルター

**ファイル**: `src/autotrader/decision/unified/trade_bot.py` (lines 1149-1210)
**チェック対象**: H4, D1

| 条件 | スコア |
|------|-------|
| 完全トレンド (close > sma_20 > sma_50) | +1.0 |
| 短期整合 (close > sma_20) | +0.5 |
| MACDモメンタム (macd > signal) | +0.3 |

**合格条件**: `aligned_score >= 0.8`

---

### 2.6 SoftGuard (ペナルティシステム)

**ファイル**: `src/autotrader/constraint/soft_guard.py`

| チェック項目 | 条件 | ペナルティ | 適用 |
|------------|------|----------|------|
| スプレッド | > 2.0 pips | 0.1~0.5 | 常時 |
| 拒否時間帯 | 22-3 UTC | 1.0 (ブロック) | 常時 |
| オフ時間帯 | 8-18 UTC外 | 0.15 | 常時 |
| 低ボラティリティ | ATR比 < 0.5 | 0.1 | 常時 |
| 高ボラティリティ | ATR比 > 2.0 | 0.1 | 常時 |
| 連敗 | 3連敗以上 | 0.2 | エントリ時 |
| MTFコンフリクト | シグナル混在 | 0.15 | エントリ時 |
| 弱トレンド | strength < 0.3 | 0.1 | エントリ時 |

**合計ペナルティ上限**: 0.8
**現在の使用状況**: オフ時間ペナルティのみ実質使用、トレードブロックなし

---

### 2.7 ポジション管理 (PositionManager)

**ファイル**: `src/autotrader/decision/unified/position_manager.py`

```
Exit判定フロー（優先順位順）:

  1. SL判定 → 価格がSLに到達 → FULL_CLOSE (STOP_LOSS)
  2. TP判定 → 価格がTPに到達 → FULL_CLOSE (TAKE_PROFIT)
  3. 時間Exit → 保有時間超過 → FULL_CLOSE (TIME_EXIT)
  4. シグナル反転 → 逆シグナル発生 → FULL_CLOSE (SIGNAL_REVERSAL)
  5. 部分利確 1R → 30%決済 + SLを建値移動
  6. 部分利確 2R → 30%決済 + SLを+1Rに移動
  7. トレーリングストップ → 2R以降、ATR×2.0距離で追従
```

#### 時間Exit設定

| モード | 最大保有時間 |
|-------|-----------|
| SCALPING | 90分 |
| DAY_TRADE | 480分 (8時間) |
| SWING | 2880分 (2日) |

#### 部分利確・トレーリング設定

| パラメータ | 値 |
|-----------|-----|
| 1R部分利確比率 | 30% |
| 2R部分利確比率 | 30% |
| トレーリング開始 | 2R |
| トレーリング距離 | ATR × 2.0 |

---

## 3. インジケータレイヤー詳細

### 3.1 テクニカルインジケータ

#### トレンド系 (`calculator/technical/trend.py`)

| インジケータ | パラメータ | 用途 |
|-----------|----------|------|
| SMA | 10, 20, 50, 100, 200 | トレンド識別、S/R |
| EMA | 10, 20, 50, 100, 200 | 高速トレンド、クロス判定 |
| ADX | period=14 | トレンド強度 (閾値: 20, 25, 30) |
| Slope | pct_change(5) | モメンタム検出 |
| Deviation | (close-MA)/MA×100 | 過買/過売 |

#### モメンタム系 (`calculator/technical/momentum.py`)

| インジケータ | パラメータ | 用途 |
|-----------|----------|------|
| RSI | period=14 | 過買/過売 (20/30/70/80) |
| MACD | fast=12, slow=26, signal=9 | トレンド+モメンタム |
| MACD Histogram | ↑のヒストグラム | 加速度検出 |
| MACD Hist Slope | histogram傾斜 | ⭐スコアリングv4 |
| Stochastic | K=14, D=3, smooth=3 | 過買/過売 (20/80) |

#### ボラティリティ系 (`calculator/technical/volatility.py`)

| インジケータ | パラメータ | 用途 |
|-----------|----------|------|
| ATR | period=14 | SL/TP算出、ボラ判定 |
| Normalized ATR | ATR/ATR_mean(100) | レジーム判定 |
| Bollinger Bands | period=20, std=2.0 | スクイーズ検出 |
| BB Width | upper-lower | ボラティリティ幅 |
| BB %B | (close-lower)/(upper-lower) | バンド内位置 |

#### 価格構造系 (`calculator/technical/price_structure.py`)

| インジケータ | パラメータ | 用途 |
|-----------|----------|------|
| Pivot High/Low | left=5, right=5 | 構造ポイント |
| HH/LL/HL/LH | swing比較 | トレンド構造 |

---

### 3.2 フィーチャーレイヤー

#### トレンドフィーチャー (`calculator/features/trend_features.py`)

| フィーチャー | 計算式 | 範囲 |
|-----------|--------|------|
| trend_direction | alignment + strength | STRONG_UP~STRONG_DOWN |
| trend_strength | ADX/50 | 0~1 |
| ma_alignment | (EMA10-EMA50)/EMA50×100 | -1~1 |
| slope_consistency | 正slope比率(5bar) | -1~1 |
| deviation_score | (close-EMA50)/EMA50×100 | -1~1 |

#### ボラティリティフィーチャー (`calculator/features/volatility_features.py`)

| フィーチャー | 計算式 | 範囲 |
|-----------|--------|------|
| volatility_regime | normalized_ATR分類 | VERY_LOW~VERY_HIGH |
| bb_squeeze | 1-(width-min)/(max-min) | 0~1 |
| range_expansion | range/avg_range(100) | 0~ |
| volatility_trend | ATR.diff(5)/ATR.shift(5) | -0.1~0.1 |

#### ダイバージェンスフィーチャー (`calculator/features/divergence_features.py`)

| 種類 | 条件 | スコア影響 |
|------|------|----------|
| REGULAR_BULLISH | 価格LL + RSI HL | BUY+1.5, SELL-2.0 |
| REGULAR_BEARISH | 価格HH + RSI LH | SELL+1.5, BUY-2.0 |
| HIDDEN_BULLISH | 価格HL + RSI LL | 継続確認 |
| HIDDEN_BEARISH | 価格LH + RSI HH | 継続確認 |

パラメータ: swing_lookback=5, min_distance=5, max_distance=50

#### MTFフィーチャー (`calculator/features/mtf_features.py`)

TF重みテーブル:

| TF | 重み |
|----|------|
| M1 | 0.10 |
| M5 | 0.15 |
| M15 | 0.20 |
| H1 | 0.40 |
| H4 | 0.60 |
| D1 | 0.80 |
| W1 | 1.00 |

---

### 3.3 マーケット構造 (SMC)

#### スイング分析 (`calculator/market_structure/swing_analyzer.py`)
- Lookback: 5 bars, Lookforward: 2 bars
- 出力: swing_high, swing_low, last_swing_high/low, bars_since

#### 構造分析 (`calculator/market_structure/structure_analyzer.py`)
- **BOS** (Break of Structure): トレンド継続シグナル
- **CHoCH** (Change of Character): トレンド反転シグナル
- トレンド状態: BULLISH / BEARISH / CONSOLIDATION

#### 流動性分析 (`calculator/market_structure/liquidity_analyzer.py`)
- 買い側流動性: スイングハイ上方
- 売り側流動性: スイングロー下方
- 流動性グラブ: ストップ狩り検出
- パラメータ: tolerance=5pips, min_zone_strength=1

---

## 4. SL/TP計算詳細

### ATR乗数 (TF別)

| TF | SL乗数 | TP乗数 |
|----|--------|--------|
| M1 | 1.2 | 1.2×TP/SL比率 |
| M5 | 1.3 | 〃 |
| M15 | 1.4 | 〃 |
| H1 | 1.5 | 〃 |
| H4 | 1.6 | 〃 |
| D1 | 1.8 | 〃 |

### SL制約
- 最小: 10.0 pips
- 最大: 50.0 pips

### モード別TP/SL比率

| モード | 比率範囲 |
|-------|---------|
| SCALPING | 1.0 ~ 1.3 |
| DAY_TRADE | 1.1 ~ 1.4 |
| SWING | 1.2 ~ 1.6 |

### 実効比率の劣化
- スプレッド: 1.5 pips
- スリッページ: 0.5 pips
- **実効TP/SL比率は設定値より約25%低下**

---

## 5. プリコンピュートエンジン

**ファイル**: `src/autotrader/calculator/precompute.py`

```
処理フロー:
  1. OHLCVデータ読込
  2. キャッシュチェック (MD5ハッシュ)
  3. キャッシュあり → Parquetから読込
  4. キャッシュなし →
     a. テクニカルインジケータ計算
     b. SMCインジケータ計算
     c. フィーチャー計算
     d. Parquetキャッシュ保存
  5. 全インジケータ付きDataFrame返却 (170+列)
```

**キャッシュ**: `data/cache/precomputed/{symbol}_{tf}_{start}_{end}_{hash}.parquet`

### デフォルト設定

| パラメータ | 値 |
|-----------|-----|
| SMA期間 | 10, 20, 50, 100, 200 |
| EMA期間 | 10, 20, 50, 100, 200 |
| RSI期間 | 14 |
| MACD | fast=12, slow=26, signal=9 |
| ATR期間 | 14 |
| BB期間 | 20 |
| ADX期間 | 14 |

---

## 6. データフロー全体図

```
┌─────────────────────────────────────────────────────────────────────┐
│                        OHLCVデータ入力                                │
│                 (M1, M5, M15, H1, H4, D1)                           │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     PrecomputeEngine                                │
│           キャッシュ確認 → 計算 → Parquet保存                          │
└────────────────────────────┬────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
┌──────────────────┐ ┌─────────────┐ ┌──────────────────┐
│ テクニカル指標     │ │ フィーチャー  │ │ マーケット構造     │
│                  │ │             │ │ (SMC)            │
│ SMA/EMA (5種)    │ │ トレンド特徴  │ │ スイングH/L       │
│ RSI (14)         │ │ ボラ特徴     │ │ BOS/CHoCH        │
│ MACD (12,26,9)   │ │ レジーム検出  │ │ 流動性ゾーン       │
│ Stoch (14,3,3)   │ │ ダイバージェンス│ │                  │
│ ADX (14)         │ │ MTF整合      │ │                  │
│ ATR (14)         │ │             │ │                  │
│ BB (20,2.0)      │ │             │ │                  │
│ 価格構造          │ │             │ │                  │
└────────┬─────────┘ └──────┬──────┘ └────────┬─────────┘
         │                  │                  │
         └──────────────────┼──────────────────┘
                            │
                            ▼
            ┌───────────────────────────────┐
            │   170+列のDataFrame (TF毎)    │
            └───────────────┬───────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ H1データ     │   │ 各TFデータ    │   │ H4/D1データ   │
│              │   │              │   │              │
│ レジーム検出  │   │ TF評価器     │   │ HTF整合      │
│ ↓           │   │ スコアリング   │   │ フィルター     │
│ モード選択    │   │ (8指標)      │   │              │
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │ コンセンサス形成        │
              │ (加重スコア集計)       │
              └───────────┬───────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │ フィルター適用          │
              │ (HTF, SoftGuard,     │
              │  RANGE+DAY制限)      │
              └───────────┬───────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │ BUY / SELL / HOLD     │
              │ + SL/TP + confidence  │
              └───────────┬───────────┘
                          │
               ┌──────────┴──────────┐
               │                     │
               ▼                     ▼
      ┌──────────────┐     ┌──────────────┐
      │ エントリ実行   │     │ HOLD         │
      │ ↓            │     │ (待機)        │
      │ PositionMgr  │     └──────────────┘
      │ ↓            │
      │ SL/TP監視    │
      │ 部分利確      │
      │ トレーリング   │
      │ 時間Exit     │
      └──────────────┘
```

---

## 7. 現在のパフォーマンス (v4+stoch, 2020-2023)

| 指標 | 値 |
|------|-----|
| 取引数 | 4,254 (89/月) |
| 勝率 | 50.5% |
| PF | 1.17 |
| 純利益 | +678,062円 |
| 最大DD | 3.34% |
| シャープレシオ | 2.17 |
| 年間収益率 | 17.0% |
| 月間プラス率 | 72.9% |

### 年別詳細

| 年 | 取引数 | 勝率 | PF | 利益 | DD |
|----|-------|------|-----|-------|-----|
| 2020 | 1,027 | 49.2% | 1.09 | +65k | 3.07% |
| 2021 | 874 | 49.0% | 1.11 | +59k | 3.11% |
| 2022 | 1,184 | 52.5% | 1.27 | +295k | 3.04% |
| 2023 | 1,169 | 51.2% | 1.23 | +259k | 3.34% |

---

## 8. パラメータ最適化履歴

| 変更 | 効果 |
|------|------|
| MACDスロープ v1(1.0)→v4(2.5) | PF大幅改善 |
| MACDスロープ v4(2.5)→v5(3.0) | 収穫逓減 |
| EMAペナルティ v1(1.0)→v4(2.5) | PF改善 |
| MIN_SCORES引き上げ | 効果なし |
| モメンタムフィルター(H1 RSI 70/30) | 効果なし |
| ストキャスティクス追加 | 収益微減、Sharpe/DD改善 |
| RANGE+DAY制限 | 月間プラス率 41.7%→58.3% |
| 輻輳型アーキテクチャ | 40%勝率で大幅劣化 |
