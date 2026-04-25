# MFE到達時間分析 — D1シグナルの「賞味期限」を測定する

## Context

現行システムはD1（日足）コンセンサスでエントリー判断するが、SL/TP（20-40pips）やStagnation exit（90分）はスキャル〜デイトレ級。ユーザーは「もっと小さく素早く利確（20pips等）したほうが良いのでは？」と提案。

判断材料として**MFE（最大含み益）がエントリーから何分後に到達するか**を分析し、D1シグナルの方向予測の有効期間を定量化する。

- 数分〜1時間以内にMFEピーク → スキャル的TP（10-20pips）が最適
- 数時間後にMFEピーク → 現行デイトレ設定が妥当
- 数日後にMFEピーク → スイング的TPに拡大すべき

## データ

- **既存BT結果**: `~/projects/AutoTraderV4_data/backtest/results/memory_v4.3.1_multiBT(2020-2022)/`
  - 42ヶ月分のJSON（`2020_01.json`〜`2022_12.json`）
  - 各トレード: `entry_time`, `exit_time`, `entry_price`, `direction`, `mfe_pips`, `mae_pips`, `sl_pips`, `symbol`, `regime`, `exit_reason`
  - **`time_to_mfe_minutes`は未収録** → M1データから事後計算が必要
- **M1ローソク足**: `~/projects/AutoTraderV4_data/data/{SYMBOL}/chart/csv/{SYMBOL}_M1_*.csv`
  - TSV形式: `<DATE> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>`
  - 全ペア・2010年〜2025年分あり

## 実施手順

### Step 1: MFE時間分析スクリプト作成

`scripts/analyze_mfe_timing.py` を新規作成。

**処理フロー:**
1. 42ヶ月分のJSONからtrade_rowsを全件ロード
2. ペア別にM1データをメモリにロード（対象期間のみ）
3. 各トレードについて:
   - entry_time〜exit_time のM1キャンドルを抽出
   - BUY: `(candle.high - entry_price) / pip_unit` で各足の含み益最大値を計算
   - SELL: `(entry_price - candle.low) / pip_unit`
   - MFEに到達した足の時刻を特定 → `time_to_mfe_minutes = (mfe_time - entry_time).minutes`
4. 分析結果を出力

### Step 2: 分析内容

1. **MFE到達時間の分布統計**
   - 全トレード / 勝ち / 負け別の中央値・平均・四分位
   - ペア別・レジーム別の中央値

2. **時間帯別の到達MFE**
   - 0-15分 / 15-60分 / 1-4時間 / 4-8時間 / 8時間+ での平均MFE(pips)
   - 「エントリー後X分以内に到達可能な利益」のカーブ

3. **仮想TP固定シミュレーション**
   - TP = 5, 10, 15, 20, 25, 30, 40 pips で固定利確した場合の:
     - 勝率（MFE >= TP なら勝ち）
     - 平均保有時間（TPに到達するまでの時間）
     - 期待値 = 勝率×TP - (1-勝率)×SL - スプレッド

4. **Runner運用の貢献度**
   - 1R（=TP pips）超えのトレード数と追加獲得pips分布
   - Runner運用ありvsなしの総利益比較

### Step 3: 結果解釈と方向性決定

分析結果をユーザーと共有し、戦略方向を決定。

## 重要ファイル

| ファイル | 用途 |
|---------|------|
| `scripts/analyze_mfe_timing.py` | **新規作成** — MFE分析スクリプト |
| `~/projects/AutoTraderV4_data/backtest/results/memory_v4.3.1_multiBT(2020-2022)/*.json` | トレードデータ |
| `~/projects/AutoTraderV4_data/data/{SYMBOL}/chart/csv/{SYMBOL}_M1_*.csv` | M1ローソク足 |

## 検証方法

1. スクリプトが全42ヶ月のトレードを正しくロードすることを確認
2. M1データとのタイムスタンプ照合（トレードのentry_time/exit_timeがM1データ範囲内）
3. 計算されたMFE pipsが、既存結果の`mfe_pips`と一致することをスポットチェック
4. 分析結果の出力を確認し、ユーザーと議論

## 注意事項

- トレードロジックへの変更なし（読み取り専用の分析）
- M1データが大きいので、ペア別にチャンクロードする
- pip_unit: JPYペア=0.01, USDペア=0.0001
