# 予測ベーストレードモード実装計画

## Context

現在のAutoTraderV4は「現在の市場状態に反応する」ロジック（MTFコンセンサス / Reactiveブレイクアウト）のみ。
ユーザーは「機械的予測 → 予測に基づくエントリー → 乖離検出で早期撤退」という新モードを検討。
予測の失敗を前提としたロバストな設計が必須条件。

## 調査結果サマリー

### 予測手法の実用性評価

| 手法 | 推奨TF | 精度 | 推論速度 | 小売向き | 判定 |
|------|--------|------|----------|----------|------|
| LightGBM/XGBoost | M15-H4 | 特徴量次第で55-65% | 1-5ms | ◎ | **Phase1採用** |
| LSTM/GRU | M5-H1 | MAPE 0.12-2% | 50-200ms | ○ | Phase2候補 |
| LSTM+ARIMA hybrid | M5-H1 | LSTM単体比-15% RMSE | 中 | ○ | Phase2候補 |
| Attention LSTM | M5-H1 | LSTM比+5-15% | 30-80ms | △ | 将来検討 |
| Transformer | M15-H1 | SOTA級 | 100-200ms | △ | 将来検討 |
| HMM | H1-D1 | レジーム検出に有効 | 低 | ○ | 補助的利用可 |
| Microstructure | tick | 理論的には最強 | 高 | ✕ | データ入手不可 |

### 重要な発見

1. **予測精度 ≠ 収益性** — リスク調整リターン（Sharpe等）で最適化すべき
2. **M1の点予測は非現実的** — ノイズ支配 + スプレッド消費で不採算
3. **特徴量の質 > モデルの複雑さ** — 25個の厳選特徴量 > 150個の雑多な特徴量
4. **90%超の学術戦略が実資金で失敗** — Walk-forward検証が必須

## アーキテクチャ設計

### 多層時間足予測 — 各TFに異なる役割

```
H4/D1  ──→  方向予測（ML分類器）   ──→ "次の4-24時間の方向は？"
              │                          UP / DOWN / FLAT (確率付き)
              │
H1     ──→  タイミング確認（ルール）──→ "モメンタムが方向と一致？"
              │                          MACD方向 + EMA配列
              │
M15    ──→  エントリー最適化       ──→ "最適なSL/TPは？"
              │                          ATRベース + リトレース検出
              │
M15    ──→  乖離モニター（常時）   ──→ "予測はまだ有効？"
                                        再予測 → 確率低下で早期撤退
```

**ポイント**: 方向予測は S/N比の高いH4/D1で行い、エントリー精度はM15で担保。
M1での価格予測は行わない（ノイズ支配のため）。

### 予測失敗への6層防御

| 層 | 機能 | 閾値 |
|----|------|------|
| 1. 保守的エントリー | P(direction) ≥ 0.6でのみエントリー | 40%は「不明」として見送り |
| 2. H1タイミングゲート | MACD/EMA一致を確認 | 短期フロー逆行時は不参加 |
| 3. 乖離モニター | M15毎に再予測、確率低下で撤退 | P < 0.3で即退出 |
| 4. 既存リスク管理 | SL/TP, 時間制限, 利益停滞検出 | 現行設定を維持 |
| 5. Edge Decay | ローリングWR監視 | 勝率低下でアラート |
| 6. モデル鮮度 | OOS性能劣化でUNIVERSALにフォールバック | 自動切替 |

### 新パッケージ構成

```
autotrader/prediction/
  __init__.py
  config.py                 # PredictionConfig (frozen dataclass)
  feature_builder.py        # PrecomputeEngine出力 → ML特徴量行列
  direction_predictor.py    # LightGBM方向分類器 (UP/DOWN/FLAT)
  divergence_monitor.py     # リアルタイム乖離追跡 + 早期撤退
  prediction_signal.py      # PredictionSignalGenerator (評価パイプライン)
```

### 特徴量設計（既存PrecomputeEngineの出力を再利用）

1. **トレンド**: trend_strength, ma_alignment, slope_consistency, deviation_score
2. **モメンタム**: RSI(14), MACD histogram, Stoch %K/%D, MACD slope
3. **ボラティリティ**: normalized_atr, bb_squeeze, range_expansion, bb_width, bb_%b
4. **市場構造**: structure_direction, trend_state_smc, bos_signal, choch_signal
5. **クロスTF**: H1/H4/D1方向一致スコア
6. **派生**: ATR変化率, price/SMA比率, ADX slope

### ラベル構築（方向予測の正解定義）

- `direction_horizon_bars`本先の価格変化を計算（H4なら6本 = 24時間先）
- 変化が **+1 ATR超 → UP**, **-1 ATR超 → DOWN**, **±1 ATR以内 → FLAT**
- ATR相対で定義することでレジーム適応的になる

### 乖離モニター詳細

```python
@dataclass(frozen=True)
class DivergenceState:
    original_direction: SignalType     # エントリー時の予測方向
    original_probability: float        # エントリー時の確率 (例: 0.72)
    bars_since_entry: int
    current_probability: float         # 最新の再予測確率
    min_probability: float             # エントリー以降の最低確率
    should_exit: bool                  # True → 即撤退
    exit_reason: str                   # "PROBABILITY_DROP" / "RAPID_DECAY" / "TIMEOUT"
```

**撤退トリガー**:
- `current_probability < 0.3` → 即撤退
- 2バー以内に確率が0.25以上低下 → 急速崩壊として撤退
- `divergence_decay_bars`超経過 + 確率下降傾向 → タイムアウト撤退

### 既存コードへの統合ポイント

| ファイル | 変更内容 |
|----------|----------|
| `decision/unified/config.py` | `prediction_*` 設定フィールド追加 |
| `decision/unified/trade_bot.py` | `signal_mode="PREDICTION"` 分岐追加 |
| `core/enums.py` | `ExitReason.PREDICTION_DIVERGENCE` 追加 |
| `decision/unified/position_manager.py` | 乖離チェックをexit判定に追加 |

## 実装フェーズ

### Phase 1: 特徴量パイプライン + LightGBM方向分類器

1. `autotrader/prediction/` パッケージ作成
2. `FeatureBuilder` — PrecomputeEngine出力からML特徴量行列を構築
3. `DirectionPredictor` — LightGBM 3クラス分類（UP/DOWN/FLAT）
4. ラベル構築（ATR相対、先読みバイアス排除）
5. Walk-forward訓練スクリプト (`scripts/train_prediction_model.py`)
6. 2010-2025 USDJPYデータで検証

**ゲート**: OOS精度55%超 かつ ランダムベースライン超でなければ中止

### Phase 2: シグナル生成 + TradeBot統合

1. `PredictionSignalGenerator` 実装（ReactiveSignalGeneratorと同パターン）
2. `trade_bot.py` に `signal_mode="PREDICTION"` ディスパッチ追加
3. `UnifiedBotConfig` に prediction設定追加
4. バックテスト比較: PREDICTION vs UNIVERSAL vs REACTIVE

**ゲート**: リスク調整リターンの改善がなければ中止

### Phase 3: 乖離モニター + ポジション管理統合

1. `DivergenceMonitor` 実装
2. `PositionManager` のexit判定チェーンに統合
3. `ExitReason.PREDICTION_DIVERGENCE` 追加
4. 乖離exitの有効/無効比較テスト

**ゲート**: 最大DD削減 + PF維持

### Phase 4: ライブ統合 + モニタリング

1. モデルの遅延読み込み + キャッシュ（推論50ms以内）
2. モデル読み込み失敗時のUNIVERSALモードフォールバック
3. 予測確率・乖離レベルのロギング

## 検証方法

1. **Walk-forward検証**: IS 12ヶ月 / OOS 3ヶ月のローリングウィンドウ
2. **ベースライン比較**: ランダム予測 vs LightGBM → エッジの存在確認
3. **バックテスト**: 既存キューランナーで PREDICTION モードを実行
4. **メトリクス**: Sharpe比, 最大DD, PF, WR, 月別損益推移
5. **過学習検出**: CSCV（Combinatorial Purged Cross-Validation）

## 作らないもの

- M1の価格点予測（ノイズ支配）
- 10モデルのアンサンブル（検証困難）
- オンライン学習（Walk-forwardで定期再訓練）
- Phase1でのDeep Learning（LightGBMが不十分な場合のみ）
- 予測専用データベース（既存Parquetキャッシュを利用）
