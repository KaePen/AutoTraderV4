# 判定ロジックフロー分析レポート

**日付**: 2026-02-07
**対象**: 新アーキテクチャ (`_generate_signal_new`) — Bug A/C/D修正後の現在実装

---

## 1. シグナル生成フロー全体図

`UnifiedTradeBot._generate_signal_new()` の処理フロー（8ステップ）:

```
Step 1: 日次リセット
  └─ risk_manager.reset_daily(current_time)

Step 2: レジーム検出
  └─ _detect_regime(current_time) → H1データ → RegimeDetector.detect_from_row()
  └─ 出力: regime (TREND/RANGE/HIGH_VOL/LOW_VOL), volatility_level

Step 3: モード・プラン選択
  ├─ _get_htf_alignment(current_time) → H4/D1のma_alignment平均
  ├─ mode_selector.select(regime, volatility, htf_alignment, hour_utc)
  └─ 出力: TradingPlan (mode, primary_tf, entry_tf, confirm_tfs, tp_sl_ratio_range)

Step 4: リスク管理チェック
  └─ risk_manager.can_trade() → False時はHOLD

Step 5: TF別評価 (TimeframeEvaluator.evaluate)
  ├─ 各TFに対してstrength_calculator.calculate(row) → IndicatorStrength
  ├─ _calculate_score(row, candle, strength) → buy_score, sell_score
  ├─ _determine_direction(buy_score, sell_score) → direction, confidence
  ├─ _apply_noise_filter(row, direction) → M1/M5のみADX/ATRフィルター
  └─ _calculate_sl_tp(row, strength, plan) → sl_pips, tp_pips

Step 6: コンセンサス統合 (ModeAwareScoreConsensus.consolidate)
  ├─ TF別重み × direction × strength でスコア合計
  ├─ MODE_THRESHOLDS閾値判定
  └─ 出力: ConsensusResult (direction, score, threshold)

Step 7: HTFトレンドフィルター (_check_htf_trend_alignment)
  ├─ H4/D1のSMA20/SMA50/close/MACDをチェック
  ├─ aligned_score >= 0.8 が必須条件
  └─ 不一致時はHOLD

Step 8: SL/TP・ポジションサイジング
  ├─ SL = primary_signal.sl_pips (TF評価から)
  ├─ TP = SL × plan.get_recommended_tp_sl_ratio()
  ├─ PositionSizer.calculate(equity, sl_pips, confidence, regime, ...)
  └─ 出力: ConsolidatedSignal
```

---

## 2. 各コンポーネントの現在値サマリー

### 2.1 レジーム検出 (RegimeDetectorConfig)

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| high_vol_atr_threshold | 1.5 | 高ボラ判定ATR閾値 |
| low_vol_atr_threshold | 0.7 | 低ボラ判定ATR閾値 |
| trend_adx_threshold | 20.0 | トレンド判定ADX閾値 |
| strong_trend_adx_threshold | 30.0 | 強トレンド判定ADX閾値 |
| ma_alignment_threshold | 0.3 | MA整列判定閾値 |

**判定ロジック（優先度順）**:
1. `HIGH_VOL`: normalized_atr > 1.5 かつ ADX < 25
2. `TREND`: ADX >= 20 かつ |ma_alignment| > 0.3
3. `LOW_VOL`: normalized_atr < 0.7
4. `RANGE`: それ以外

### 2.2 モード選択 (ModeSelectorConfig)

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| high_vol_threshold | 1.3 | 高ボラ→SCALPING閾値 |
| htf_alignment_threshold | 0.5 | SWING優先HTF整合閾値 |
| prefer_swing_on_strong_trend | True | 強トレンド時SWING優先 |

**選択ロジック**:
1. `HIGH_VOL` + アクティブ時間帯 → SCALPING
2. `HIGH_VOL` + 非アクティブ → DAY_TRADE
3. volatility > 1.3 → SCALPING
4. `TREND` + |htf_alignment| > 0.5 → SWING
5. `TREND` + アクティブ + volatility > 0.8 → SCALPING
6. `TREND` (その他) → DAY_TRADE
7. `RANGE`/`LOW_VOL` → DAY_TRADE

**アクティブ時間帯** (UTC): 東京 0-3, ロンドン 7-10, NY 13-18

### 2.3 MODE_PLANS（モード別トレードプラン）

| 項目 | SCALPING | DAY_TRADE | SWING |
|------|----------|-----------|-------|
| primary_tf | M5 | M15 | H4 |
| entry_tf | M1 | M5 | H1 |
| confirm_tfs | M15 | H1, H4 | D1 |
| manage_tf | M5 | M15 | H4 |
| max_holding_bars | 18 (90分) | 32 (8時間) | 12 (2日) |
| tp_sl_ratio_range | (1.0, 1.3) | (1.1, 1.4) | (1.2, 1.6) |

### 2.4 TimeframeEvaluatorスコアリング閾値

| TF | NORMALIZED_MIN_SCORES (比率) | MIN_SCORES (絶対値) |
|----|------------------------------|---------------------|
| M1 | 0.10 | 1.5 |
| M5 | 0.12 | 1.8 |
| M15 | 0.14 | 2.1 |
| H1 | 0.16 | 2.4 |
| H4 | 0.18 | 2.7 |
| D1 | 0.20 | 3.0 |

MAX_POSSIBLE_SCORE = 15.0

### 2.5 コンセンサス設定 (ConsensusConfig)

**ロール別デフォルト重み**:

| ロール | デフォルト重み |
|--------|--------------|
| PRIMARY | 3.0 |
| ENTRY | 2.0 |
| CONFIRM | 1.5 |
| MANAGE | 1.0 |
| OTHER | 0.5 |

**モード別ロール重み (ROLE_WEIGHTS_BY_MODE)**:

| ロール | SCALPING | DAY_TRADE | SWING |
|--------|----------|-----------|-------|
| PRIMARY | 2.0 | 3.0 | 3.5 |
| ENTRY | 3.0 | 2.5 | 2.0 |
| CONFIRM | 2.5 | 2.0 | 2.5 |
| MANAGE | 1.0 | 1.5 | 1.5 |
| OTHER | 0.2 | 0.3 | 0.3 |
| **合計** | **8.7** | **9.3** | **9.8** |

**MODE_THRESHOLDS**:

| モード | 閾値 | 典型重み合計 | 通過に必要なstrength概算 |
|--------|------|-------------|----------------------|
| SCALPING | 3.5 | 8.7 | ~40% |
| DAY_TRADE | 4.5 | 9.3 | ~48% |
| SWING | 6.0 | 9.8 | ~61% |

### 2.6 PositionSizer設定

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| base_risk_pct | 0.02 (2%) | 基本リスク率 |
| pip_value | 1000.0 | 1lot=100,000通貨 |
| min_lot | 0.01 | 最小ロット |
| max_lot | 10.0 | 最大ロット |
| confidence_high_threshold | 0.7 | 高確度 → 1.2x |
| confidence_low_threshold | 0.5 | 低確度 → 0.5x |
| dd_reduction_threshold | 0.05 (5%) | DD減額開始 |
| dd_max_reduction | 0.7 (70%) | DD最大減額率 |
| consecutive_loss_threshold | 5 | 連敗減額開始 |

### 2.7 TradingParams (シミュレーター共通)

| パラメータ | 値 |
|-----------|-----|
| spread_pips | 1.5 |
| slippage_pips | 0.5 |
| pip_value | 100.0 |
| commission_per_lot | 0.0 |

---

## 3. スコアリング計算の詳細

### 3.1 `_calculate_score` パターン別スコア配点

`TimeframeEvaluator._calculate_score()`の基本スコア:

| パターン | 買いスコア | 売りスコア | 条件 |
|---------|-----------|-----------|------|
| 完全上昇+MACD↑ | 5.0 | — | close > SMA20 > SMA50 かつ MACD > Signal |
| 完全上昇+MACD↑+ADX>20 | 7.0 | — | 上記 + ADX > 20 |
| 完全下降+MACD↓ | — | 5.0 | close < SMA20 < SMA50 かつ MACD < Signal |
| 完全下降+MACD↓+ADX>20 | — | 7.0 | 上記 + ADX > 20 |
| 上昇+MACD↑ | 4.0 | — | close > SMA20 かつ MACD > Signal |
| 下降+MACD↓ | — | 4.0 | close < SMA20 かつ MACD < Signal |
| 上昇トレンドのみ | 2.5 | — | close > SMA20 (MACDなし) |
| 下降トレンドのみ | — | 2.5 | close < SMA20 (MACDなし) |

**ボーナス加点**:

| ボーナス | スコア | 条件 |
|---------|--------|------|
| RSI順方向ボーナス | +1.0 | 30 <= RSI <= 70 |
| HTF整合（1TF） | +2.0 | 上位足1TF一致 |
| HTF強整合（2TF+） | +4.0 | 上位足2TF以上一致 |

**フィルター（HOLD化）**:

| フィルター | 条件 |
|-----------|------|
| RSI過熱 | buy_score > 0 かつ RSI > 80 |
| RSI過冷 | sell_score > 0 かつ RSI < 20 |

**最大到達可能スコア**: 7.0 (基本) + 2.0 (ADX) + 1.0 (RSI) + 4.0 (HTF) = **14.0**

### 3.2 方向決定 (`_determine_direction`)

```
min_score = MIN_SCORES[timeframe]  (例: M15 = 2.1)

1. max(buy, sell) < min_score → HOLD
2. |buy - sell| < min_score * 0.4 → HOLD (方向不明確)
3. confidence = min(score_diff / 15.0 + max_score / 30.0, 1.0)
4. buy > sell → BUY, else → SELL
```

### 3.3 HTF整合性スコアリング (`_score_htf_alignment`)

- HTFデータ各TFについて、`_get_htf_row`で現在時刻以前の最新行を取得
- 完全トレンド一致（close > SMA20 > SMA50 or close < SMA20 < SMA50）でカウント
- 2TF+ → +4.0, 1TF → +2.0, 0TF → 0.0

---

## 4. フィルタリングパイプライン

各段階の閾値と推定影響:

```
[全足データ] (M1/M5/M15/H1/H4/D1 各足ごと)
    │
    ▼
[Step 5a: _calculate_score]
    ├─ close vs SMA20判定 → トレンドなし足はスコア0
    ├─ RSI > 80 or RSI < 20 → 除外
    └─ MACDなし時は低スコア(2.5)
    │
    ▼
[Step 5b: _determine_direction]
    ├─ max_score < MIN_SCORES[tf] → HOLD
    │   M1: 1.5, M5: 1.8, M15: 2.1, H1: 2.4, H4: 2.7, D1: 3.0
    ├─ score_diff < min_score * 0.4 → HOLD (方向不明確)
    └─ ★ 推定通過率: ~60-70%
    │
    ▼
[Step 5c: _apply_noise_filter] (M1/M5のみ)
    ├─ M1: ADX < 10 → HOLD
    ├─ M5: ADX < 8 → HOLD
    └─ ATR/ATR_MA < 0.3 or > 3.5 → HOLD
    │
    ▼
[Step 6: Consensus統合]
    ├─ weighted_score = Σ(weight × |direction| × strength)
    ├─ SCALPING閾値: 3.5 (重み合計8.7)
    ├─ DAY_TRADE閾値: 4.5 (重み合計9.3)
    ├─ SWING閾値: 6.0 (重み合計9.8)
    └─ ★ 推定通過率: ~30-50%
    │
    ▼
[Step 7: HTFトレンドフィルター]
    ├─ H4/D1チェック (SMA20/SMA50/close/MACD)
    ├─ aligned_score >= 0.8 必須
    ├─ 完全トレンド=+1.0, 短期トレンド=+0.5, MACD一致=+0.3
    └─ ★ 推定通過率: ~50-70%
    │
    ▼
[最終シグナル出力]
    └─ ★ 全体推定通過率: ~10-25%
```

### フロー分析実績値（修正後バックテスト）
- レジーム: TREND 47.3%, RANGE 51.3%, HIGH_VOL 0.8%, LOW_VOL 0.6%
- モード: SCALP 18.8%, DAY 61.1%, SWING 20.1%
- BUY/SELL比: 1.5:1
- 最終シグナル発生: 1197トレード/年 (100/月)

---

## 5. 戦略別設定比較表

### 5.1 StrategyConfig

| パラメータ | Scalp | ShortMid | Swing | 説明 |
|-----------|-------|----------|-------|------|
| min_edge_score | 0.10 | 0.12 | 0.15 | 最小エッジスコア |
| max_spread_atr_ratio | 0.30 | 0.30 | 0.35 | スプレッド/ATR上限 |
| allowed_hours_utc | None | None | None | 全時間帯許可 |

### 5.2 StrategyTimeframes

| パラメータ | Scalp | ShortMid | Swing |
|-----------|-------|----------|-------|
| primary_tf | M5 | M15 | H1 |
| entry_tf | M1 | H1 | H4 |
| confirm_tfs | (M15,) | (H4,) | (D1,) |
| htf_refs | (H1,) | (H4,) | (D1,) |
| htf_weight | 0.5 | 0.5 | 0.8 |
| tp_sl_ratio_range | (1.0, 1.3) | (1.1, 1.4) | (1.2, 1.6) |

### 5.3 InStrategyConsensusConfig

| パラメータ | Scalp | ShortMid | Swing |
|-----------|-------|----------|-------|
| primary_weight | 3.0 | 3.0 | 3.0 |
| entry_weight | 2.5 | 2.5 | 2.5 |
| confirm_weight | 1.5 | 1.8 | 2.0 |
| htf_ref_weight | 1.2 | 1.5 | 2.0 |
| min_confidence | 0.25 | 0.30 | 0.35 |
| score_margin_required | 0.10 | 0.12 | 0.15 |

### 5.4 レジーム適合係数

| レジーム | Scalp | ShortMid | Swing |
|---------|-------|----------|-------|
| HIGH_VOL | 1.1 | 1.0 | 0.8 |
| TREND | 1.2 | 1.3 | 1.4 |
| RANGE | 0.6 | 0.5 | 0.4 |
| LOW_VOL | 0.5 | 0.6 | 0.5 |

### 5.5 SL/TP計算

**SLマルチプライヤー（ATR倍率）**:

| TF | 倍率 |
|----|------|
| M1 | 1.2 |
| M5 | 1.3 |
| M15 | 1.4 |
| H1 | 1.5 |
| H4 | 1.6 |
| D1 | 1.8 |

**SL制限**: min=10.0pips, max=50.0pips

**TPデフォルト比率（TF別）**:

| TF | TP/SL比率 |
|----|-----------|
| M1 | 1.2 |
| M5 | 1.3 |
| M15 | 1.4 |
| H1 | 1.5 |
| H4 | 1.6 |
| D1 | 1.8 |

**最終TP計算**: `_generate_signal_new`のStep 8で `tp_pips = sl_pips × plan.get_recommended_tp_sl_ratio()` を使用。TF評価時の`_calculate_sl_tp`で計算されたtp_pipsは最終的に使われず、`plan.tp_sl_ratio_range`の中央値が適用される。

---

## 6. ガード機能一覧

### 6.1 HardGuard（絶対禁止）

| チェック | 条件 | 適用タイミング |
|---------|------|--------------|
| 証拠金維持率 | margin_ratio < 150% | 常時 |
| 日次損失上限 | daily_pnl < -5% | 常時 |
| 取引時間帯 | 土日 or 0時/23時(UTC) | 常時 |
| データ品質 | data_quality == "error" | 常時 |
| ポジション上限 | position_count >= 3 | エントリー時のみ |
| 高インパクトニュース | has_news かつ 15分以内 | エントリー時のみ |

### 6.2 SoftGuard（ペナルティ）

| チェック | ペナルティ | 条件 | 適用タイミング |
|---------|-----------|------|--------------|
| 高スプレッド | 0.1 × (1 + excess/2)、max 0.5 | spread > 2.0 pips | 常時 |
| 低流動性時間帯 | 1.0 (事実上ブロック) | 22-3時 UTC | 常時 |
| オフタイム | 0.15 | 8-18時UTC以外 | 常時 |
| 低ボラティリティ | 0.1 | ATR比 < 0.5 | 常時 |
| 高ボラティリティ | 0.1 | ATR比 > 2.0 | 常時 |
| 連敗 | 0.2 | 3連敗以上 | エントリー時のみ |
| MTF不整合 | 0.15 | MTF conflicting/mixed | エントリー時のみ |
| 弱トレンド | 0.1 | trend_strength < 0.3 | エントリー時のみ |

**累積ペナルティ上限**: 0.8（confidence × (1 - penalty)で適用）

---

## 7. 修正済みバグ一覧

### Bug A: runner.pyインジケータ不足
- **症状**: `normalized_atr`, `ma_alignment`, `ema_12`, `ema_26` がrunner.pyで計算されていなかった
- **影響**: レジーム検出が常にデフォルト値（RANGE）、EMAベースの判定が不可能
- **修正**: runner.pyの`_calculate_indicators()`に4指標を追加
- **ma_alignment計算**: ATRベース正規化 `(ema_12 - ema_26) / atr_14`（価格比率だとFXでスケールが小さすぎた）

### Bug C: HTFスコアリングのiloc[-1]問題
- **症状**: `_score_htf_alignment`がHTFデータの最終行（未来データ）を参照していた
- **影響**: バックテストで未来のHTFデータを見てしまい、HTFボーナスが不正確
- **修正**: `_get_htf_row`メソッドを追加。numpy `searchsorted`で現在時刻以前の最新行を高速検索

### Bug D: RSIボーナス範囲の非対称性
- **症状**: BUY: RSI 40-70, SELL: RSI 35-60 と非対称だった
- **影響**: BUY方向に偏ったボーナス付与
- **修正**: 両方向とも RSI 30-70 に統一

### インデックス更新バグ (`_get_all_tf_data`)
- **症状**: `_get_all_tf_data`でTF別インデックスカウンタが更新されていなかった
- **影響**: 毎回データフレーム全体をスキャンし、不正確な行が返される可能性
- **修正**: `_get_current_row`でインデックス追跡を正しく実装

---

## 8. 現在のバックテスト結果

**テスト期間**: 2023年（12ヶ月）
**対象**: USDJPY M5
**アーキテクチャ**: 新アーキテクチャ (`_generate_signal_new`)
**`use_convergent_architecture`**: False

| 指標 | 値 |
|------|-----|
| 取引数 | 1197 (100/月) |
| 勝率 | 48.0% |
| PF (Profit Factor) | 1.01 |
| 純利益 | +13,118円 |
| 最大ドローダウン | 5.36% |
| 年間収益率 | 1.3% |
| 月間プラス率 | 41.7% (5/12ヶ月) |

**モード別実績**:

| モード | 比率 | 勝率 |
|--------|------|------|
| SCALP | 18.8% | 56.3% |
| DAY | 61.1% | 44.8% |
| SWING | 20.1% | 61.3% |

**レジーム別実績**:

| レジーム | 比率 | 勝率 |
|---------|------|------|
| TREND | 47.3% | 54.5% |
| RANGE | 51.3% | 42.8% |

---

## 9. 改善候補リスト

### 優先度: 高

| # | 改善案 | 期待効果 | 根拠 |
|---|--------|---------|------|
| 1 | **DAY_TRADE勝率改善** | 全体勝率+4-6% | DAY 61%のボリュームで勝率44.8%が全体を引き下げ。SCALP(56.3%)やSWING(61.3%)は良好 |
| 2 | **RANGE時のDAY_TRADEフィルター強化** | 全体勝率+3-5% | RANGE勝率42.8%が足を引っ張る。RANGE時のDAY_TRADE閾値を上げるか、シグナル品質要件を追加 |
| 3 | **DAY_TRADE MODE_THRESHOLD引き上げ** | 品質向上、取引数減 | 現在4.5 → 5.0-5.5に。重み合計9.3に対してstrength ~53-59%要求 |

### 優先度: 中

| # | 改善案 | 期待効果 | 根拠 |
|---|--------|---------|------|
| 4 | **RANGE時モード切替ロジック追加** | RANGE勝率+5% | 現在RANGE→一律DAY_TRADE。RANGE+低ボラ時はSWINGが有利な可能性 |
| 5 | **_calculate_scoreのトレンドのみパターン(2.5点)の扱い再検討** | ノイズ除去 | MACD確認なし時のスコアが低すぎてノイズ化。閾値上げるか除外 |
| 6 | **HTFトレンドフィルター閾値の条件分岐** | モード別最適化 | SCALP(0.5)→緩和、SWING(1.0)→厳格化のようにモード別に |
| 7 | **TP/SL比率の二重定義整理** | 一貫性 | `_calculate_sl_tp`のTPデフォルト比率と`plan.tp_sl_ratio_range`が別系統。_calculate_sl_tpのTP計算は最終出力で上書きされるため冗長 |

### 優先度: 低

| # | 改善案 | 期待効果 | 根拠 |
|---|--------|---------|------|
| 8 | **SoftGuard統合** | 実効性向上 | 現在の新アーキテクチャフローではSoftGuardが未使用。統合すれば低品質時間帯の除外が可能 |
| 9 | **InStrategyConsensus活用** | 戦略内品質向上 | 輻輳型パス専用。新アーキテクチャでは未使用だが、コンポーネント自体は健全 |
| 10 | **PositionSizer pip_value不整合** | 正確なロット計算 | PositionSizerConfig.pip_value=1000 vs TradingParams.pip_value=100。用途が異なるが紛らわしい |

### 数値改善シミュレーション

**改善案#1+#2+#3を同時適用した場合の推定**:
- DAY_TRADE閾値: 4.5 → 5.5（取引数 ~730 → ~500に減少）
- RANGE時DAY_TRADE追加フィルター（RANGE勝率42.8% → 50%に改善想定）
- 推定結果: 取引数 ~800/年(67/月), 勝率 ~53%, PF ~1.10

---

## 付録: ファイル一覧

| ファイル | 行数目安 | 主要シンボル |
|---------|---------|------------|
| `trade_bot.py` | ~1100 | `_generate_signal_new`, `_check_htf_trend_alignment`, `_detect_regime`, `_get_htf_alignment` |
| `timeframe_evaluator.py` | ~780 | `evaluate`, `_calculate_score`, `_score_htf_alignment`, `_determine_direction`, `_apply_noise_filter`, `_calculate_sl_tp`, `_get_htf_row` |
| `mode_aware_consensus.py` | ~280 | `consolidate`, `_get_weight`, `MODE_THRESHOLDS`, `ROLE_WEIGHTS_BY_MODE` |
| `mode_selector.py` | ~220 | `select`, `_select_mode`, `MODE_PLANS` |
| `strength_calculator.py` | ~100 | `calculate` (RSI/MACD/Trend/Divergence/BB/Stoch/ATR) |
| `strategies/scalp.py` | ~90 | `ScalpStrategy`, `DEFAULT_CONFIG`, `TIMEFRAMES` |
| `strategies/short_mid.py` | ~90 | `ShortMidStrategy`, `DEFAULT_CONFIG`, `TIMEFRAMES` |
| `strategies/swing.py` | ~90 | `SwingStrategy`, `DEFAULT_CONFIG`, `TIMEFRAMES` |
| `strategies/base.py` | ~400 | `BaseStrategy`, `StrategyConfig` |
| `strategies/in_strategy_consensus.py` | ~150 | `InStrategyConsensus`, `InStrategyConsensusConfig` |
| `constraint/hard_guard.py` | ~220 | `HardGuard`, `HardGuardConfig` |
| `constraint/soft_guard.py` | ~250 | `SoftGuard`, `SoftGuardConfig` |
| `position_sizer.py` | ~160 | `PositionSizer`, `PositionSizerConfig` |
| `backtest/runner.py` | ~600 | `BacktestRunner`, `_calculate_indicators` |
| `backtest/simulator.py` | ~580 | `TradeSimulator`, `process_candle`, `_check_exit_conditions` |
| `calculator/features/regime_detector.py` | ~240 | `MarketRegimeDetector`, `detect`, `_determine_regime` |
| `config/trading_params.py` | ~35 | `TradingParams`, `DEFAULT_TRADING_PARAMS` |
