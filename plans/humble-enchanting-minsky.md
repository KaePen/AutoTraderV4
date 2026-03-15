# ボット堅牢性強化 - 進捗と残項目整理

## Context

バックテスト性能をリアル運用で維持するため、6項目の防御層強化を計画。
実装過程でBT基盤の重大な問題（CT不統一、スプレッドパイプライン未接続）を発見・修正し、
BT信頼性が大幅に向上した。

---

## 完了項目

### #1. EdgeValidator（統計的エッジ検定）- コード完了、閾値チューニング未実施
- **PR #677**: 実装完了（edge_validator.py、41テスト通過）
- **PR #679**: 閾値修正（期待WR 0.80→0.65、auto_cb=OFF、連敗閾値5→8）
- **現状**: 監視ログは出力されるが、自動アクション（CB発動）はOFF
- **残タスク**: リアル運用データでの閾値最適化、WebUI通知連携

### #2. サーキットブレーカー強化 - コード完了
- **PR #677**: 連続敗戦トリガー(Layer 6)追加
- **PR #679**: 閾値を8に調整
- **現状**: 8連敗でCB発動（60分停止）。BT検証でトリガー実績確認済み
- **残タスク**: 発動履歴のログ構造化（いつ・なぜ・何分）

### #3. スプレッド閾値ペア別化 - コード完了、BT検証で効果なし判定
- **PR #677**: ペア別sg_spread_threshold_pips追加
- **PR #680**: spread_pipsをプリセットからbot_configに注入
- **BT結果**: CT=17環境で**SoftGuardスプレッドペナルティの追加効果ゼロ**
  - 理由: CT=17で既に低品質トレードが除外されているため
- **判定: 不採用**（コードは残し、将来CT調整時に再評価可能）

### 計画外: BT基盤の重大修正（最も価値の高い成果）
| PR | 修正内容 | 影響 |
|----|---------|------|
| #681 | consensus_threshold BT/ライブ統一（9→17） | BT結果がT47基準と整合 |
| #683-688 | 実スプレッドCSVデータの完全パイプライン構築 | BT信頼性の根本改善 |
| #689 | マルチペアBTでも実スプレッド対応 | 8ペア統合BT精度向上 |

### 実スプレッドBT結果（6JPYペア単体、2023-2025）
| ペア | 固定spread PF | 実spread PF | Net差 |
|------|-------------|------------|-------|
| USDJPY | 4.76 | 4.56 | -3% |
| EURJPY | 4.18 | 4.09 | -2% |
| GBPJPY | 3.28 | 3.09 | -5% |
| AUDJPY | 3.65 | 3.20 | -9% |
| CADJPY | 4.20 | 3.54 | -12% |
| CHFJPY | 3.57 | 3.33 | -7% |

→ 実スプレッドで全ペアPF>3.0維持。BTの楽観バイアスを是正。

---

## 未着手・未検証の残項目

### #4. スプレッド分布モデル - コード完了、未検証、優先度↓
- `autotrader/backtest/spread_model.py` は作成済み
- 実スプレッドCSVパイプラインが完成したため**優先度が下がった**
- 実データで時間帯・イベント別のスプレッド変動が自然に反映されるため
- **判断**: 実スプレッドBTで十分なら不要。テールリスクのモデリングが必要と判断された場合のみ有効化

### #5. マクロレジームフィルタ（VIX）- コード完了、BT検証なし
- `autotrader/calculator/features/macro_regime.py` 作成済み
- `enabled=False` で全機能無効
- **残タスク**:
  1. VIXデータの取得方法確立（yfinance or MT5経由）
  2. BTでVIXデータを注入する仕組み
  3. 2020年3月（コロナショック VIX=82）での効果検証
  4. HardGuard/SoftGuard統合のBT検証
- **工数見積**: 実装1日 + BT検証1日

### #6. WebUI監視パネル - 未着手
- EdgeValidator/MacroRegime状態の表示
- diagnostics.pyにフィールド追加済み（edge_alert_level, macro_vix等）
- **判断**: 別セッションでデザイン検討

### ライブエンジン: リアルタイムスプレッド連携 - 未着手
- MT5アダプタに`get_spread_async()`は存在するがエンジンから未呼び出し
- BT検証完了後に着手予定
- **対象ファイル**: `autotrader/live/engine.py`

---

---

## 次セッション実装計画: #5 VIXフィルタBT検証

### 目的
マクロ環境の急変（コロナショック等）をVIXで検知し、トレード停止/慎重化の効果を検証する。

### 現状
- `MacroRegimeFilter`クラスは完全実装済み（`macro_regime.py`）
- `UnifiedBotConfig`にパラメータ定義済み（`enabled=False`）
- `UnifiedTradeBot`への統合が**未実装**（初期化・呼び出しなし）
- VIXデータのBT注入メカニズムが**未実装**

### VIXデータの粒度
- **BT**: 日次Close（yfinanceで無料取得可能な粒度）
- **ライブ**: 時間単位（yfinanceリアルタイム or MT5経由）
- **根拠**: VIXは30日予想変動率のマクロ指標。閾値20/30/40は数日〜数週間の持続的恐怖を示し、
  日中の短期変動ではなくレジームシフトの検知が目的。コロナショックも数日かけて20→82に上昇。

### 実装ステップ

#### Step 1: VIXデータローダー（新規）
- `autotrader/backtest/vix_loader.py` を作成
- yfinance で `^VIX` の日次データ（Close）を取得
- ローカルキャッシュ（CSV）に保存して再利用（`{data_dir}/vix/vix_YYYY.csv`）
- 年指定でロード: `load_vix_data(year, data_dir) -> dict[date, float]`

#### Step 2: UnifiedTradeBot にMacroRegimeFilter統合
- `_init_new_components()` で `MacroRegimeFilter` を初期化（`config.macro_regime_enabled`時）
- `update_macro_regime(vix: float)` メソッド追加
- `generate_signal()` 内で:
  - `should_block_trade()` → HOLD返却（HardGuard相当）
  - `get_penalty()` → sg_contextに加算（SoftGuard相当）
- **対象ファイル**: `autotrader/decision/unified/trade_bot.py`

#### Step 3: シングルBT（year_runner）にVIXデータ注入
- `run_unified_year()` の引数に `vix_data: dict[date, float] | None` 追加
- ループ内で日付変更検出時に `bot.update_macro_regime(vix)` 呼び出し
- 既存のファンダメンタル/スプレッド注入パターンを踏襲
- **対象ファイル**: `autotrader/backtest/year_runner.py`

#### Step 4: マルチBT（run_multi_pair_backtest）にVIXデータ注入
- `run_multi_pair_year()` にVIXデータ引数追加
- ループ内で日付変更時にVIX更新（全ペア共通値）
- `setup_pair_context()` / `_run_year_worker()` にVIXデータを伝搬
- **対象ファイル**: `scripts/run_multi_pair_backtest.py`

#### Step 5: backtest_queue_runner にVIX対応（シングル・マルチ両対応）
- `overrides.bot.macro_regime_enabled=true` でVIXフィルタ有効化
- `_execute_month_single()` でVIXデータ自動ロード・year_runnerに渡す
- `_execute_month_multi()` でVIXデータ自動ロード・run_multi_pair_yearに渡す
- **対象ファイル**: `scripts/backtest_queue_runner.py`

#### Step 6: BT検証ジョブ投入（3年で実行）
```
ROB-V1: USDJPY 2020-2022 VIXフィルタOFF（コロナショック期ベースライン）
ROB-V2: USDJPY 2020-2022 VIXフィルタON
ROB-V3: 8ペアマルチ 2020-2022 VIXフィルタOFF
ROB-V4: 8ペアマルチ 2020-2022 VIXフィルタON
ROB-V5: 8ペアマルチ 2023-2025 VIXフィルタON（通常期の機会損失確認）
```

### 検証ポイント
- 2020年3月（VIX=82.69）でEXTREME_FEAR発動→全トレード停止の効果
- DD圧縮 vs 機会損失のトレードオフ
- ELEVATED/HIGH_FEARのペナルティが実効的か（CT=17で#3と同様に効果なしの可能性）
- 通常期（2023-2025）での誤検知・不要停止がないか

### 注意事項
- VIXは日次データなので足ごとの更新は不要（日付変更時のみ）
- yfinanceの取得はBT開始前に1回のみ（キャッシュ化）
- 全ペア共通のVIX値を使用（ペア別ではない）
- **シングルBTとマルチBTの両方に同時実装**（スプレッドの教訓）

### 将来拡張（ライブ連携）
- `autotrader/live/engine.py` にVIX定期取得タスク追加（1時間ごと）
- yfinanceリアルタイム（15分遅延）またはMT5のVIXシンボル経由
- BT検証で効果確認後に着手

### 既存で再利用可能なパターン
- スプレッドデータ注入: `year_runner.py` L209-216
- ファンダメンタルデータ注入: `year_runner.py` L253-297
- 日付変更検出: `year_runner.py` L161-182
- マルチBT対応: `run_multi_pair_backtest.py` のspread注入パターン（PR #689）
