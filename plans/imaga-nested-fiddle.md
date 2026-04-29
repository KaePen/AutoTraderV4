# BT-Live 乖離 徹底調査・改善プラン (2026-04-29)

## Context (なぜこの作業が必要か)

直近の実証で、以下が判明した:

1. **8 ペア × 2026-04 (BT)** : PF 10.0, WR 75%, DD 0.24%, +¥119K
2. **ライブ DB (同期間 49 件)** : PF 0.30, WR 44.9%, DD ~11%, **-¥113K**
3. **trade-by-trade 突合**: 49 件中 **BT で再現できたのは 1 件のみ (3%)**、平均スコアギャップ +4.7

すでに **案1 (live engine の M1-close ゲート)** を実装済みだが、これだけでは再現率 1/36 にとどまる。原因は重層的で、追加で **3 系統の調査・修正** が必要と判明:

| 系統 | 内容 | 影響 |
|---|---|---|
| **a. インジケータ計算経路差** | Live は `_calc_indicators_multi_tf()` で毎秒再計算、BT は `monthly_cache` 保存値を使用 | 計算アルゴリズム/バージョン差で score 差 |
| **b. 累積 bot 状態差** | Live は 1ヶ月連続で `edge_validator`/`adaptive_tuner` の状態保持、BT は年単位リセット | tuner threshold delta 累積差で score 差 |
| **c. ティックデータ NaN** | 49 件中 4 件で USDJPY tick NaN | Live では一時的に評価失敗・誤動作 |

ゴール: **BT が Live を高精度で再現できる状態にし**、改善活動を定量的に進められる土台を作る。

---

## 実装プラン (4 フェーズ、autopilot で完走可能な範囲)

### Phase A: 静的調査 (read-only、~30分)

#### A1. インジケータ計算経路の詳細マッピング
**目的**: `monthly_cache` に保存されたインジケータ値と、ライブ `_calc_indicators_multi_tf()` の計算結果が一致するか確認

**調査対象ファイル**:
- `autotrader/backtest/data_pipeline.py:121-209` (`prepare_ohlcv` で保存される計算)
- `autotrader/live/engine.py:1471` (`_calc_indicators` の呼び出し元)
- `autotrader/calculator/precompute.py` (計算エンジン本体)

**手段**:
- 同じ M1 OHLC で両者を呼び、列ごとの値差を CSV で出力
- `tmp/indicator_diff_USDJPY.csv` 等にダンプ

#### A2. 累積 bot 状態の影響経路
**調査対象**:
- `autotrader/decision/unified/adaptive/tuner.py:102-140` (`AdaptiveParameterTuner._evaluate`)
- `autotrader/decision/unified/adaptive/edge_validator.py:156-282` (`EdgeValidator.record_trade`)
- `autotrader/decision/unified/trade_bot.py:1589-1601` (`get_overrides()` 適用箇所)
- `autotrader/decision/unified/trade_bot.py:3453-3502` (`on_trade_executed()`)

**手段**:
- Live でどのトレード履歴があれば `consensus_threshold_delta` がどの値になるか、シミュレートで dump

#### A3. ティック NaN の発生条件
**対象データ**: `D:/Projects/AutoTraderV4_data/data/USDJPY/raw/ticks/ticks_2026_04.parquet`

**手段**:
- NaN 行の前後の正常 tick 時刻と比較 → 接続切断/低流動性パターン特定
- 時間帯ヒートマップで集中時刻を可視化

---

### Phase B: 仮説検証ツール実装と実行 (~3-4時間)

#### B1. 「Live 状態スナップショット注入」機能を追加
**目的**: BT で Live の累積状態を再現できるようにする

**実装内容**:
- 新ファイル: `autotrader/decision/unified/adaptive/state_snapshot.py`
  - `EdgeValidator.dump()` → JSON
  - `AdaptiveParameterTuner.dump()` → JSON
  - `load()` で BT 開始時に注入
- `multi_pair_runner.py` に `bot_state_snapshot_path` パラメータ追加
- Live 側で `tmp/autotrader.db` から過去トレードを再生して bot 状態を再構築する補助関数

#### B2. 1秒間隔評価モード BT (簡易版)
**目的**: 案1 の仮説を直接検証 — 「1秒評価 → live と同じ災害トレード再現される」を確認

**実装内容**:
- `multi_pair_runner.py` に `eval_interval_sec: int = 60` パラメータ追加
  - デフォルト = 60 (M1毎、現状)
  - 1 を指定すると 1秒ごとに `generate_signal()` を呼び、live engine と同じ live-mid overwrite を適用
- 計算量爆発を抑えるため対象期間を 4/21-23 (3日) に絞る
- 4 ペア (AUDJPY/CADJPY/GBPJPY/CHFJPY) のみ

#### B3. ライブ B1+B2 連動の再検証
**目的**: 累積状態 + 1秒評価の合せ技で、ライブの 36 件中何件が再現できるか

**手段**:
- B1 で live 状態を BT に injection
- B2 で 1秒評価モード ON
- `live_replay.py` の精度を再測定 → 1/36 が何件まで上がるか

#### B4. Phase A1 で見つけた「インジケータ計算差」の標準化
**目的**: BT と Live で同じ計算経路にする

**実装方向 (調査結果次第)**:
- BT 側で `_calc_indicators_multi_tf()` を `_load_all_timeframes` 後に呼び直す  
- または monthly_cache 生成時に `_calc_indicators_multi_tf()` を使う (バージョン統一)

---

### Phase C: 大規模 BT 検証 (~2-3時間)

#### C1. 全期間 BT (8ペア × 2025) — Phase B 修正後
**目的**: 修正後 BT が依然 健全な数値を出すか確認 (PF 過剰悪化していないか)

**コマンド** (Phase B 完了後):
```bash
python -m autotrader.backtest run-portfolio \
    --symbols USDJPY,EURJPY,GBPJPY,AUDJPY,CADJPY,CHFJPY,EURUSD,GBPUSD \
    --start 2025 --end 2025 \
    --use-tick-exit --trades-csv tmp/c1_full_2025.csv \
    --out tmp/c1_full_2025.json
```
推定実行時間: 30-40 分

#### C2. 長期 BT (8ペア × 2020-2025)
**目的**: handover ベースライン (PF 4.95 / WR 81.2% / DD 1.61%) を再現できるか

**手段**: `--start 2020 --end 2025` で全期間
推定実行時間: 2-3 時間 (キャッシュ熱があれば)

#### C3. ストレステスト (4/21-23 集中 BT)
**目的**: 災害期間に修正後 BT がライブ動作を完全再現するか

**手段**: 4ペア × 4/21-23 期間で B2 の 1秒評価モード適用
推定実行時間: 30 分

---

### Phase D: 修正提案と最終レポート (~1時間)

#### D1. 修正コードの整合
- 案1 のまま採用 or 案1+追加 (Phase B 結果次第)
- 各層 (engine.py / multi_pair_runner.py / decision/unified/) のコミット計画

#### D2. 最終レポート
- ファイル: `plans/bt_live_divergence_root_cause.md` 補遺2
- BT 健全性証明: 内部一貫 + Live 再現率
- 改善後の Live 期待値範囲 (PF/Sharpe/DD)
- ステージング/デモ運用の検証手順

---

## 重要ファイル参照

| 役割 | パス | 主要関数 |
|---|---|---|
| Live エンジン | `autotrader/live/engine.py` | `_tick`, `_update_market_data`, `_calc_indicators` |
| BT マルチペア | `autotrader/backtest/multi_pair_runner.py` | `_setup_pair_context`, `_run_year` |
| BT データロード | `autotrader/backtest/data_pipeline.py` | `prepare_ohlcv`, `load_monthly_cache` |
| Bot ロジック | `autotrader/decision/unified/trade_bot.py` | `set_market_data`, `generate_signal`, `on_trade_executed` |
| 累積状態 | `autotrader/decision/unified/adaptive/{tuner,edge_validator,overrides}.py` | `record_trade`, `get_overrides`, `dump/load` (新規) |
| BT 分析 | `autotrader/backtest/analysis.py` | `match_bt_live_trades`, `compare_summaries` |
| Live 再現 | `autotrader/backtest/live_replay.py` | `replay_live_only_trades` |

---

## 既存ユーティリティ (再利用)

- `autotrader.backtest.analysis.match_bt_live_trades()` — trade-by-trade 突合
- `autotrader.backtest.analysis.compare_summaries()` — A/B サマリ比較
- `autotrader.backtest.live_replay.replay_one_trade()` — 単一時刻の再現
- `autotrader.backtest.tick_simulator.check_tick_exit` — ティック精密 exit 判定
- `autotrader.backtest.data_pipeline.load_monthly_cache` — 月別キャッシュロード

---

## 検証方法 (end-to-end)

### Phase A 完了基準
- [ ] `tmp/indicator_diff_*.csv` に Live と BT のインジケータ値差が dump されている
- [ ] tuner / edge_validator の state 影響パスが具体的にコード行レベルで特定されている
- [ ] tick NaN 発生時刻のヒストグラムが出ている

### Phase B 完了基準
- [ ] `bot_state_snapshot_path` を渡した BT が、新規 BT と異なる結果を出す (注入動作確認)
- [ ] 1秒評価 BT (4/21-23、4ペア) で 12+ の SL_HIT BUY シグナルが発生する (ライブ災害再現)
- [ ] live_replay の再現率が 1/36 → 20/36 以上に改善

### Phase C 完了基準
- [ ] 8ペア×2025 BT が修正前と比較して PF 1.5 以上維持 (致命的悪化なし)
- [ ] 8ペア×2020-2025 BT が PF 3.0 以上 (handover 4.95 比 60% 以上)
- [ ] 4/21-23 集中 BT で Live PnL の ±20% 以内

### Phase D 完了基準
- [ ] レポートに BT 健全性証明 + Live 再現率 + ステージング検証手順記載
- [ ] 全変更ファイルが構文・import チェックを通過
- [ ] 案1 と Phase B 修正の同時影響確認

---

## 現実的な進行順 (autopilot 推奨)

```
1. Phase A1+A2+A3 並列実行 (Explore agents 3つ並列)
   ↓
2. Phase B1 (snapshot 機能実装)
   ↓
3. Phase B2 (1秒評価モード実装)
   ↓
4. Phase B3 (連動再検証 — replay_live_only)
   ↓
5. Phase B4 (インジケータ統一、A1 結果次第)
   ↓
6. Phase C1+C3 並列 (8ペア×2025 + 4/21-23 集中)
   ↓
7. Phase C2 (長期 BT、2020-2025) - バックグラウンドで放置
   ↓
8. Phase D 報告
```

**想定総時間**: 6-9 時間 (autopilot 連続稼働、ユーザー操作不要)

---

## オフスイッチ・ロールバック

- 各修正には config フラグ (`entry_on_m1_close_only`, `bot_state_snapshot_enabled`, `eval_interval_sec=60` 等) を付ける
- `git checkout` で各ファイル単位でロールバック可
- Phase A/B 完了時点で smoke BT (1ペア×1ヶ月) を走らせ、健全性確認
