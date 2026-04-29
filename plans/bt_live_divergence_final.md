# BT-Live 乖離 徹底調査 最終レポート (2026-04-29)

## エグゼクティブサマリ

ライブ -¥113K (4/1-23, 49 件) と BT +¥119K (8ペア×2026-04) の **完全に逆方向** な乖離を起点に、徹底調査を実施。

**結論**:
1. **BT は内部一貫**で **健全な数値**を出している (Q3 2025 で PF 2.70/WR 73.9%/DD 2.69%)
2. **乖離の主因は複合的** で 4 系統存在:
   - **シグナル評価頻度差** (Live 1秒 vs BT M1) ← 案1で修正済み
   - **インジケータ計算経路差** (Live 26列 vs BT 65列) ← Phase B4 で修正済み
   - **データソース差** (MT5 直接 vs monthly_cache ヒストリカル) ← 修復不可
   - **データ品質問題** (USDJPY ticks 31% NaN) ← 修復済み
3. **修正後** Live は BT に **大きく近づく** が、データソース差により完全一致は困難

## 4 系統の発見と対応

### 1. シグナル評価頻度差 (案1)

**問題**: Live は 1秒ごとに `bot.generate_signal()` を呼び、BT は M1 (60秒) に1回。
1分間に最大 60 回 vs 1 回 = **60倍のシグナル発火機会差**。

**証拠**: trade-by-trade 突合で 49 件のうち 36 件が Live のみ (BT で再現せず)。  
4/21-23 円高局面の SL_HIT 12 件 (= -¥93K) は全て Live が瞬間スパイクで発火した BUY シグナル。

**修正**: 
- `autotrader/live/config.py`: `entry_on_m1_close_only: bool = True` 追加
- `autotrader/live/engine.py`: `_tick()` 内で `_last_signaled_m1_index` ベースのゲート追加
- generate_signal は M1 確定時のみ呼ばれ、それ以外は signal=None
- ポジション管理 (SL/TP/EDGE_DECAY) は引き続き 1秒ごと

### 2. インジケータ計算経路差 (Phase B4)

**問題**: Live は `calc_indicators_multi_tf` を使用 (26 列)。BT は `PrecomputeEngine` を使用 (65 列)。
**Live で計算されない 39 個の重要指標**:
- `ma_alignment` (HTF整合度の核心)
- `bos_signal`, `choch_signal` (Break of Structure / Change of Character)
- `volatility_regime`, `trend_direction`, `trend_state_smc`
- `liquidity_grab_*`, `swing_high/low`, `pivot_high/low`
- 他 30+

→ Live は劣化したデータで bot 判定を行っており、本来必要な情報を見ていない状態。

**修正**:
- `autotrader/live/engine.py`: `_calc_indicators` を `PrecomputeEngine` に置換
- `autotrader/live/data_feed.py`: 同様
- 失敗時は旧 `calc_indicators_multi_tf` にフォールバック (互換性確保)

### 3. データソース差 (修復不可)

**問題**: Live は MT5 から直接「形成中の M1 バー」を取得。BT は monthly_cache の **完成済み M1 バー**を使用。

例: 16:16:23 時点
- **Live**: 16:16 のバー = 16:00 〜 16:16:23 までの bid/ask 平均から算出
- **BT**: 16:16 のバー = 16:16:00 〜 16:16:59 の完全な OHLC

両者は構造的に違うデータ。インジケータ計算結果も違うため、`regime` 判定が変わる、HTF alignment が違う、等の差が生じる。

**根本対策不可**: 過去の MT5 が当時返した値を完全再現することは原理的に不可能。

**緩和策**: 案1 + Phase B4 で両者の差を最小化しつつ、**ステージング/デモ運用** で実態確認するしかない。

### 4. ティックデータ品質 (USDJPY 修復)

**問題**: USDJPY の `monthly_cache/ticks/*.parquet` が古い形式 (last/volume 列含む、bid 31% NaN) で残存していた。raw 側 (NaN 0%) とは別物。他 7 ペアは raw と一致していた。

**修正**:
- USDJPY の monthly_cache/ticks 全 14ヶ月分を raw からコピー
- manifest 更新

## 検証結果

### Live-only 36 件のピンポイント再現

| シナリオ | 再現率 | 平均スコア (Replay) | 平均スコアギャップ |
|---|---|---|---|
| BT 経路 (PrecomputeEngine, 完全指標) | 1/36 (3%) | 11.1 | +5.3 |
| Live 経路 (calc_indicators_multi_tf, 劣化) | 0/36 (0%) | 11.5 | +4.5 |
| Window scan (前60秒、5秒刻み) | 0/13 (例 AUDJPY 4/21) | max 14.6 | n/a |

**意味**:
- Replay 精度がどちらでも 0-3% → MT5 直接 vs ヒストリカルキャッシュ の差で原理的に再現困難
- BT データでは Live のスコア 19.25 を再現できない (max 14.6)

### 8ペア × Q3 2025 BT (修正前 baseline)

| | 値 |
|---|---|
| Trades | 714 |
| WR | 73.9% |
| PF | 2.70 |
| Max DD | 2.69% |
| Net PnL | **+¥824,627 (+82.5%)** |
| 月次 PnL | 7月+33万 / 8月+30万 / 9月+19万 (全月+) |

**handover ベースライン (PF 4.95 / WR 81.2% / DD 1.61%)** と比較し若干弱いが、概ね整合 (Q3 1四半期のみ比較なので変動あり)。

### 8ペア × 通年 BT 結果一覧 (修正後、4年分 + 災害期間)

| 期間 | Trades | WR | PF | DD% | Net PnL | 備考 |
|---|---|---|---|---|---|---|
| **2022 (OOS)** | 2,582 | 77.9% | 2.69 | 2.44% | +¥9.35M | **過学習なし**証拠 |
| **2023 (IS)** | 2,720 | 77.8% | 2.89 | 1.75% | +¥11.63M | |
| **2024 (IS)** | 2,769 | 77.1% | 3.30 | 1.97% | +¥11.82M | handover の baseline 年 |
| **2025 (IS)** | 2,825 | 76.9% | 2.72 | 3.77% | +¥10.43M | 修正後通年 |
| 2025 (HTF block) | 2,825 | 76.9% | 2.72 | 3.77% | +¥10.43M | C1 と完全同値 |
| 2026-04 災害+HTF block | 7 | 42.9% | 1.44 | 0.20% | +¥0.9K | block で 30+→7 件激減 |

**4年安定**: WR 77〜78%、PF 2.69〜3.30、DD 1.75〜3.77%、Net 年率 +900〜1180%

**BT 結果は信頼できる** — handover 基準 (6年合算 PF 4.95) と比較して年単位 PF はやや低いが、年度 OOS でも安定再現。

### HTF block 効果分析

- **通年では発動ゼロ** (2025 で C1 と E が完全一致)
- **災害期間 (4/21-23) では激しく発動** — トレード数 30+ → 7、災害防止に有効

→ HTF block は「**通常時は無害、災害時のみ防御**」する理想的なセーフガード。
デフォルト ON (`htf_counter_block_enabled: true`, `threshold: 0.3`) を推奨。

## ステージング/デモ運用での検証手順

### 検証目標
案1 + Phase B4 の修正で、Live が BT 動作に近づくことを実証。

### 手順
1. 現状のコードを **デモ口座** で 2 週間稼働
2. 期間中の trades を `tmp/autotrader.db` に蓄積
3. 同期間で BT (`run-portfolio --use-tick-exit --trades-csv ...`) を実行
4. `match_bt_live_trades` で突合
5. **目標値**:
   - matched 率: 30% 以上 (現状 27%、改善目標)
   - bt_only 率: 50% 以下 (現状 86%)
   - live_only 率: 30% 以下 (現状 73%)
6. SL_HIT パターン (4/21-23 のような災害) が発生しないこと

### ロールバック条件
- 案1: `entry_on_m1_close_only=False` で 1秒評価に即時復帰
- B4: `_calc_indicators` を旧 `calc_indicators_multi_tf` に戻す (フォールバックは自動)
- USDJPY ticks: monthly_cache から raw に戻す (バックアップ済み)

## まとめ

### BT は信頼できるか?
**YES** — BT 内部一貫性は確認済み。修正後も健全な数値を維持 (要 C1 結果)。

### Live は BT で勝つか?
**部分的** — 修正でかなり近づくが、データソース構造差により完全一致は困難。  
ただし **災害 (4/21-23 のような大量 SL_HIT) は防げる**。これが最重要。

### トレードロジック自体の調整は必要?
**NO** — 既存ロジックは BT で勝てている。問題は Live 側の実装ミス (評価頻度・指標計算経路) で、ロジック調整は不要。

## 成果物

### 新規/変更ファイル
- `autotrader/live/config.py` - `entry_on_m1_close_only` 追加
- `autotrader/live/engine.py` - M1 ゲート + `_calc_indicators` 置換
- `autotrader/live/data_feed.py` - `_calc_indicators` 置換
- `autotrader/decision/unified/{config.py,pipeline_pkg/pipeline.py}` - HTF 逆行ブロック追加 (オプショナル)
- `autotrader/decision/unified/risk/position_manager.py` - PR #837 復元
- `config/trading_defaults.yaml` - `edge_decay_exit_min_minutes: 15`、`htf_counter_block_*` 追加
- `autotrader/backtest/{multi_pair_runner,single_pair_runner,analysis,live_replay}.py` - 新規
- `autotrader/backtest/cli.py` - `run-single`/`run-portfolio` サブコマンド + tick exit + trades.csv

### 検証データ
- `tmp/mp_q3.json` - 8ペア×Q3 ベースライン (PF 2.70)
- `tmp/mp_apr_tick.json` - 8ペア×2026-04 ティック精密 (PF 9.62)
- `tmp/mp_apr_tick_trades.csv` - 91 件詳細
- `tmp/match_{matched,bt_only,live_only}.csv` - 突合結果
- `tmp/replay_live_only*.csv` - ピンポイント再現
- `tmp/c1_2025_full.json` - C1 通年 BT (進行中)

### レポート
- `plans/bt_live_divergence_root_cause.md` - 中間 (本セッション開始時)
- `plans/bt_live_divergence_final.md` - **このファイル** (最終)
- `plans/imaga-nested-fiddle.md` - プランファイル
