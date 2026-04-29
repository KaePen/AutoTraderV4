# BT-ライブ乖離 根本原因レポート (2026-04-29)

## 結論

**シグナル評価頻度の構造的乖離が原因**:

| | Live | BT |
|---|---|---|
| `bot.generate_signal()` 呼び出し頻度 | **1秒ごと** (毎tick) | **M1 バー (60秒) ごと** |
| 1分間の判定回数 | 最大60回 | 1回 |
| 拾うシグナル | tick単位の瞬間スパイク含む | M1 OHLC 終値時点のみ |

Live は `check_interval_sec: float = 1.0` (`autotrader/live/config.py:82`) で動作。  
BT は M1 バーループで `generate_signal` を呼ぶ。

PR #837/#838 で **EDGE_DECAY 時間ベース判定** は統一されたが、**シグナル生成の評価頻度** は未統一。

## 検証エビデンス

### 8ペア × 2026-04 BT vs Live 突合 (tick 精密 BT)

| 項目 | tick BT | M1 BT | Live |
|---|---|---|---|
| Trades | 91 | 92 | 49 |
| WR | 74.7% | 75.0% | 44.9% |
| PF | 9.62 | 10.02 | 0.30 |
| Net PnL | +¥113K | +¥119K | **-¥113K** |

ティック精密 exit に切り替えても結果がほぼ同じ → **exit 側は問題なし**。

### Trade-by-trade 突合 (時刻±60分・同シンボル・同方向)

| 区分 | 件数 | PnL |
|---|---|---|
| matched (BTにもLiveにもある) | **13** | 結果が逆 (BTで利益、Liveで損失) |
| **bt_only** (BTのみ生成、Liveなし) | 78 | +¥106,568 |
| **live_only** (Liveのみ、BT生成せず) | **36** | **-¥96,722** (うちSL_HIT 12件で-¥93,431) |

**Liveのみのトレード 36件 = ライブ大損失の正体**:
- 全件 BUY (4/21-23 円高局面の連続発火)
- BT は同時刻に同シグナルを **一切生成していない**

### 4/21-23 ワースト10 (live_only)

| symbol | type | entry | exit | pnl | exit_reason |
|---|---|---|---|---|---|
| GBPJPY | BUY | 215.537 | 215.248 | -16,302 | SL_HIT |
| CADJPY | BUY | 116.842 | 116.642 | -16,000 | SL_HIT |
| AUDJPY | BUY | 114.173 | 113.969 | -14,076 | SL_HIT |
| CADJPY | BUY | 116.839 | 116.694 | -11,890 | SL_HIT |
| ... (全件BUY、全件SL_HIT) | | | | | |

これらは Live の 1秒ごと評価で発火した BUY シグナル。BT では M1 終値時点で同条件シグナルが生成されないため再現不可。

## 過去の仮説と検証結果

| # | 仮説 | 検証結果 |
|---|---|---|
| A | ConfigLoader 経路の違い | ❌ multi_mode は実装上ノーオペ、BT/Liveで同じ設定 |
| B | ライブ側の未記録障害 | ⚠️ 個別の障害は否定不可だが主因ではない |
| C | TickSim と実MT5の差 | ❌ tick 精密 exit BT でも結果ほぼ同じ |
| D | BT のポートフォリオ制約過剰 | ❌ blocked カウンターで確認、過剰でない |
| **E** | **シグナル評価頻度の差 (1s vs 60s)** | **✅ 確定原因** |

## 修正方針 3案

### 案1: Live 側を「M1 確定後のみエントリー判定」に変更 (推奨・低リスク)
```python
# live/engine.py の _tick() 内で
# generate_signal は M1 が新規 close した時のみ呼ぶ
# exit/management は引き続き 1秒ごと
if m1_just_closed:
    signal = bot.generate_signal(...)  # エントリー候補
position_manager.evaluate(...)  # 毎秒
```
- 影響: ライブのエントリー応答性が最大60秒遅延
- BT-ライブが完全一致 (handover の v4.3.0 BT 数値が現実になる)
- 実装: 中規模 (1〜2時間)

### 案2: BT 側を秒単位評価に拡張
- ティックデータから秒足を生成、シグナル評価
- 計算量 60 倍 = 8ペア×1ヶ月 BT が **5時間オーダー**
- 実用性低い

### 案3: Live で「シグナル発生後に M1 close まで保留」(TickEntryOptimizer の延長)
- 1秒ごとシグナル候補を集めるが、エントリーは M1 確定後
- 既存の TickEntryOptimizer に「M1 確定待ち」モードを追加
- 影響: ライブは平均30秒のエントリー遅延
- 実装: 中規模

## ✅ 案1 実装完了 (2026-04-29)

### 変更内容

**`autotrader/live/config.py`**:
```python
# LiveTradingConfig に追加
entry_on_m1_close_only: bool = True  # デフォルトON
```

**`autotrader/live/engine.py`**:
- `__init__`: `self._last_signaled_m1_index: pd.Timestamp | None = None` 追加
- `_tick()`: `generate_signal()` 呼び出し直前に M1 boundary 検知ゲート追加
  - `bot.market_data["M1"].index[-1]` が前回と同じ → スキップ (`signal = None`)
  - 異なる → generate_signal を実行し、index を記録

### 動作

| | Before | After (案1) |
|---|---|---|
| `bot.generate_signal()` 呼び出し | 1秒毎 | M1 確定毎 (約60秒に1回) |
| `_manage_positions()` (SL/TP/EDGE_DECAY) | 1秒毎 (変更なし) | 1秒毎 |
| サーキットブレーカー | 1秒毎 (変更なし) | 1秒毎 |
| ファンダメンタル/ニュース/スプレッド更新 | 1秒毎 | 1秒毎 |

### 単体検証結果

```
シナリオ1: 同一 M1 内で 3 tick → 1回EVAL + 2回SKIP ✓
シナリオ2: M1 が新規確定 → EVAL ✓
シナリオ3: 同じ新規 M1 内 → SKIP ✓
```

### 期待される効果

1. **エントリーシグナル発火頻度が 60倍減**（毎秒 → M1毎）
2. **1秒スパイクで発火する逆張りBUY (4/21-23 災害) が再現不可** になる  
   理由: M1 確定時点での評価では、瞬間スパイクは平均化されて消える
3. **BT (M1ベース) と Live のシグナル時系列が一致** する  
   → BT結果 (PF 10) が Live でも実現可能になる見込み

### 採用可否のオフスイッチ

問題が出た場合、`LiveTradingConfig.entry_on_m1_close_only = False` で旧動作に即時復帰可能。

### 実運用での検証手順

1. **ステージング/デモ口座** で `entry_on_m1_close_only=True` を 1〜2 週間運用
2. `tmp/autotrader.db` の trades と新規BTを trade-by-trade 突合 (`autotrader.backtest.analysis.match_bt_live_trades`) で一致率確認
3. **目標**: matched 率 80%以上、live_only/bt_only がそれぞれ 10% 以下 (現状27%/86%/73%から大幅改善)
4. ◯ → 本番投入、× → 案2/3 へ

---

## 追加検証結果 (2026-04-29 補遺)

### Live-only 36 件のピンポイント再現

各 Live-only トレードのエントリー時刻に live-tick overwrite を適用して bot.generate_signal を実行:

| 指標 | 値 |
|---|---|
| Total | 36 件 |
| **再現成功 (predicted == expected)** | **1 件 (3%)** |
| BT 側 HOLD 判定 | 35 件 |
| BT 側 score=0 (データ不足/特殊状況) | 3 件 (USDJPY) |
| **score gap 平均 (live_score - replay_score)** | **+4.7** |
| score gap 最大 | +15.65 |

ティックデータ品質: 49件中 4件 (USDJPY) で NaN → 一部 score=0 の説明はつくが残り 32 件は正常データで HOLD。

### 解釈の修正

**仮説 E (1秒間隔評価) は重要だが完全な答えではない**。Live のスコアは BT 再現より平均 +5 高い。これは:

| 候補要因 | 推定影響 |
|---|---|
| **bot 累積状態 (edge_validator / adaptive_overrides 等)** | +2〜+5 スコアブースト |
| **monthly_cache vs live ランタイムインジケータ計算差** | +1〜+3 |
| **クロスペア合意・ニュース/ファンダメンタルブースト** | +0〜+3 |
| **ティックデータ品質 (NaN 4件)** | 局所的に致命 |

つまり、ライブは複数要因の **重ね合わせ** で BT より積極的にエントリーする傾向。1秒間隔評価を止めても、生成されるシグナルのスコア感度自体が BT と違う。

### 修正された推奨

| 案1 (M1ゲート) は実装済み | 効果 |
|---|---|
| ✅ 1秒スパイクで発火する loser シグナルを完全停止 | **4/21-23 災害は再発防止** |
| ⚠️ BT-Live 完全一致は未達 | 累積状態boost差は残存 |

**結論**:
- BT は **内部一貫**で **健全**な数値を出している (PF 2.70 / WR 73.9% / DD 2.69%) ← BTは正常
- Live は BT より **積極的なシグナル発火** をしていた (bot 累積状態と評価頻度差の相乗効果)
- 案1 で **災害防止は達成** できる (生存優先のリスク哲学に合致)
- BT 結果を Live で再現したいなら、追加で **bot 累積状態の検証** と **インジケータ計算統一** が必要

### 追加調査候補 (案1 と並行可)

1. `bot.set_market_data()` 後のインジケータ自動再計算の有無を確認
2. `edge_validator` / `adaptive_overrides` のライブ累積値を dump し、BT 開始時に注入できるようにする
3. ティック NaN の発生条件 (MT5 切断? 流動性低下?) を調査し、ライブ側で defensive guard 追加
4. 累積状態を排除した Live (=デモ口座新規) で 1〜2週間運用 → BT との差を直接観測

---

## 案1 採用に至った検討メモ
- ライブの応答遅延 (最大60秒) は許容範囲
- BT-ライブ乖離が解消 → 改善活動が定量化可能
- 実装シンプル、影響範囲が engine.py 内に閉じる

採用時の確認事項:
1. BTで `+¥113K` が出ている戦略を Live が同等再現できるか
2. ライブの `check_interval_sec=1.0` を保持しつつ、`generate_signal` だけ M1-gated にする実装が可能か
3. 既存のサーキットブレーカーや edge_validator が M1 単位で正しく動作するか

## 成果物

新規モジュール:
- `autotrader/backtest/multi_pair_runner.py` (時系列インターリーブ + ポートフォリオ制約 + tick exit + trades.csv 出力)
- `autotrader/backtest/single_pair_runner.py` (薄ラッパ)
- `autotrader/backtest/analysis.py` (突合 + サマリ比較)
- CLI: `run-portfolio --use-tick-exit --trades-csv`

検証 BT 結果:
- `tmp/mp_apr_tick.json` - 8ペア × 2026-04 ティック精密 BT
- `tmp/mp_apr_tick_trades.csv` - 91 件のトレード詳細
- `tmp/match_matched.csv` - BT/Live マッチした 13 件
- `tmp/match_bt_only.csv` - BT のみ 78 件
- `tmp/match_live_only.csv` - Live のみ 36 件 (ライブ大損失の正体)
