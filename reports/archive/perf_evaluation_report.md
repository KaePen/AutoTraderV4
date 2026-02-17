# AutoTraderV4 パフォーマンス評価レポート

## 1. エグゼクティブサマリー

### 現状評価

| 指標 | 値 | 評価 |
|------|-----|------|
| 勝率 | 56.04% | 目標60%に未達 |
| PF | 1.01 | 辛うじて黒字 |
| 年間収益率 | +0.23% | 実質トントン |
| 月間トレード数 | 244 | 過多（オーバートレード傾向） |
| 総トレード(2023年) | 2,930 | -- |

### 過去の最適化レポートとの乖離

過去レポートでは「勝率60.8%、年率19.7%」等の好成績が報告されているが、
現在のMEMORY.md記載の直近結果（PF:1.01、収益率+0.23%）とは大きく乖離している。
これは以下の可能性を示唆:

1. 過去の最適化はHighWinRateGenerator（H1単一TF）による結果
2. 現在の結果はマルチTF統合エンジン（ParallelMultiTFBacktestEngine）による結果
3. TP/SL比率問題の修正（2026-02-05）で整合性は改善したが、収益性は低下
4. マルチTF統合がオーバートレード（月244回）を引き起こしている

### 重要な発見

- **3種類のバックテストエンジンが並存**しており、どれを使うかで結果が大きく異なる
- マルチTFエンジンではTP=SL（勝率重視）設定だが、スプレッド/スリッページを考慮するとTP/SL比率が実質1.0未満になる
- イベントキュー構築時にDataFrame全行をiterrowsでdict化しており、大規模データで重大なボトルネック
- 並列処理(ProcessPoolExecutor)はイベント数<=2では不使用で、ほとんどのケースで逐次処理

---

## 2. バックテスト環境分析

### 2.1 実行方法

| スクリプト | 用途 | エンジン |
|-----------|------|---------|
| `scripts/run_backtest.py` | メインバックテスト | BacktestRunner + BacktestEngine |
| `scripts/quick_backtest.py` | 軽量テスト | TradeSimulator直接利用 |
| `scripts/run_fast_backtest.py` | 並列高速テスト | FastBacktestEngine |
| `scripts/optimize_strategy.py` | パラメータ最適化 | BacktestRunner |
| `scripts/diagnose_backtest.py` | 診断 | -- |

### 2.2 バックテストエンジン構成

```
BacktestRunner (runner.py)
 ├── BacktestEngine (engine.py) -- H1単一TF、HighWinRateGenerator
 ├── ParallelMultiTFBacktestEngine (engine.py) -- マルチTF統合
 │    ├── TimelineEventQueue (events.py) -- 全TFイベント時系列キュー
 │    ├── PriorityBasedEvaluator (parallel.py) -- 優先度ベース評価
 │    └── TradeSimulator (simulator.py) -- 注文執行
 └── FastBacktestEngine (fast_backtest.py) -- チャンク並列処理
```

### 2.3 データ環境

| 項目 | 値 |
|------|-----|
| 通貨ペア | USDJPY |
| データ範囲 | 2010-01-04 ~ 2025-12-30 |
| M1データ行数 | 約593万行 (358MB) |
| H1データ行数 | 約618万行 (6.2MB) |
| 利用可能時間足 | M1, M2, M3, M5, M10, M12, M15, M20, M30, H1, H2, H3, H4, H6, H8, H12, D1 |
| データ形式 | CSV (Polarsで読み込み → Pandasに変換) |
| キャッシュ | Parquet形式 (`data/cache/precomputed/`) |

### 2.4 コスト設定

| パラメータ | 値 | 影響 |
|-----------|-----|------|
| スプレッド | 1.5 pips | エントリー時加算 |
| スリッページ | 0.5 pips | SL/TP決済時に不利方向に適用 |
| 手数料 | 0.0 円/lot | なし |
| pip_value | 100円 | 1pipの価値 |

---

## 3. パフォーマンスボトルネック分析

### 3.1 データ読み込み (重要度: 中)

**現状:**
- CSVデータはPolars(`pl.read_csv`)で高速読み込み後、Pandasに変換
- キャッシュ機能あり（Parquet形式）
- `DataLoader._load_csv`は適切にフィルタリング

**ボトルネック:**
- 初回読み込み時のCSV→Pandas変換コスト（M1: 358MB, 593万行）
- `MultiTimeframeDataLoader.load_all_standard`で全時間足を順次読み込み

**改善提案:**
- Parquetキャッシュを初回生成すれば以降は高速（現在実装済み）
- 並列データ読み込み(`ParallelDataLoader`)は実装済みだが、使用箇所が限定的

### 3.2 イベントキュー構築 (重要度: 高)

**現状: `events.py` TimelineEventQueue._build_queue`**

```python
for _, row in df.iterrows():  # 全行をPythonループ
    row_data = {}
    for col in df.columns:    # 列ごとにdict化
        val = row.get(col)
        ...
        row_data[col] = float(val)
    heapq.heappush(self._events, event)
```

**問題:**
- `df.iterrows()`はPandasで最も遅いイテレーション手法（ベクトル化されない）
- 全時間足の全行をPythonレベルでdict化 → O(rows * columns) のPython操作
- M15×1年で約35,000行、H1で約8,700行、全TF合計で数万～数十万行
- heapqへの挿入もO(n log n)

**推定影響:** マルチTFバックテスト1年あたり **10-30秒のオーバーヘッド**

**改善提案:**
- `df.to_dict(orient="records")`でバッチ変換（5-10倍高速化）
- NumPyアレイベースのイベント表現に変更
- heapqの代わりにソート済みリストのマージ（TFごとにソート済みなので）

### 3.3 インジケーター計算 (重要度: 中)

**現状: `indicators.py` IndicatorCalculator.calculate_single**

- pandas_taライブラリでSMA, RSI, MACD, Stoch, ATR, ADX計算
- DivergenceDetectorによるダイバージェンス検出
- 全時間足を**逐次**計算（`calculate_all_timeframes`）

**問題:**
- 各時間足を順次処理（並列化なし）
- `calculate_single`内で`import pandas_ta as ta`（遅延import）

**改善提案:**
- ThreadPoolExecutorで時間足別に並列計算（I/OバウンドではないがGILリリースするCベースの計算が多い）
- TA-Libへの移行（pandas_taより5-10倍高速）

### 3.4 シグナル評価 (重要度: 高)

**現状: `parallel.py` evaluate_timeframe_signal**

- 各イベントバッチ（同時刻に確定する複数TF）を評価
- イベント数<=2: 逐次評価（ProcessPoolExecutorのオーバーヘッド回避）
- イベント数>=3: ProcessPoolExecutorで並列

**問題:**
- マルチTFバックテストでは、同時刻に確定するTFは通常1-2個（H1確定時にM5, M15も確定するケースは限定的）
- つまり、**大半のイベントバッチはサイズ1で、並列処理は実質未使用**
- ProcessPoolExecutor生成コスト（`with`文で毎回生成）が無駄

**改善提案:**
- ProcessPoolExecutorをインスタンス変数として再利用
- バッチサイズに関わらず逐次処理で十分（プロセス間通信コスト > 計算コスト）
- ベクトル化評価の検討（Pandas/NumPyベースでバッチ評価）

### 3.5 トレードシミュレーション (重要度: 低)

**現状: `simulator.py` TradeSimulator.process_candle**

- 1足ごとにSL/TPチェック → シグナル処理 → エクイティ更新
- 効率的なPythonコード
- Position数が少ない（max_positions=1）のでループは軽量

**改善提案:**
- 現状で十分効率的。改善の必要なし

### 3.6 メモリ使用量 (重要度: 中)

**問題箇所:**
1. `TimelineEventQueue._build_queue`: 全TFの全行をCandleEventオブジェクトとして保持
   - 各CandleEventにrow_data(dict)を持つ → 1イベントあたり約500B-1KB
   - 5TF×1年で約50,000イベント → **約25-50MB/年**

2. `FastBacktestEngine.run`: チャンクごとにDataFrameをdict化してサブプロセスに送信
   - `df.to_dict(orient="list")` + `market_data_dicts`で大量のdictコピー
   - pickle化のコスト

**改善提案:**
- CandleEventのrow_dataに必要な列のみ保持（全列ではなく）
- SharedMemoryまたはメモリマップドファイルでプロセス間データ共有

### 3.7 ボトルネック総合ランキング

| 順位 | 箇所 | 推定影響 | 改善難易度 |
|------|------|---------|-----------|
| 1 | イベントキュー構築 (`iterrows` + dict化) | 10-30秒/年 | 低 |
| 2 | シグナル評価の非効率な並列化 | 5-15秒/年 | 低 |
| 3 | インジケーター計算（逐次） | 5-10秒/年 | 中 |
| 4 | メモリ効率（CandleEvent肥大化） | メモリ25-50MB/年 | 中 |
| 5 | FastBacktestのプロセス間データ転送 | チャンクあたり1-3秒 | 高 |

---

## 4. トレード結果の統計分析

### 4.1 現在の結果（MEMORY.md記載、2023年データ）

| 指標 | 値 |
|------|-----|
| 総トレード | 2,930 |
| 月間トレード | 244 |
| 勝率 | 56.04% |
| PF | 1.01 |
| 年間収益率 | +0.23% |

### 4.2 コードベースからのTP/SL分析

`evaluate_timeframe_signal`でのSL/TP計算:

```python
sl_pips = max(10.0, min(atr_pips * base_mult, 50.0))
tp_pips = sl_pips  # 勝率重視: TP = SL
```

**TP = SL（比率1.0）に固定されている。**

スプレッド1.5pips + スリッページ0.5pips×2 = 実質コスト2.5pips/トレード

| SL/TPサイズ | 実効TP/SL比率 | 必要勝率(BE) | 現在勝率との差 |
|-------------|--------------|-------------|--------------|
| 10 pips | (10-2.5)/(10+2.5) = 0.60 | 62.5% | -6.5% (赤字) |
| 20 pips | (20-2.5)/(20+2.5) = 0.78 | 56.2% | -0.2% (ほぼBE) |
| 30 pips | (30-2.5)/(30+2.5) = 0.85 | 54.2% | +1.8% |
| 50 pips | (50-2.5)/(50+2.5) = 0.90 | 52.4% | +3.6% |

**問題: SL=10pips（下限値）のトレードでは勝率62.5%以上が必要だが、現在の56%では赤字になる。**

小さいSL/TPのトレードが多いほど、スプレッド/スリッページの相対的影響が大きくなり全体収益を圧迫。

### 4.3 TF別エントリー閾値分析

MIN_SCORESの変換（`score * 1.5`）:

| TF | 元min_score | 実効min_score | エントリー難易度 |
|----|-----------|-------------|----------------|
| M1 | 2.0 | 3.0 | 低（エントリーされやすい） |
| M5 | 2.25 | 3.375 | 低 |
| M15 | 2.7 | 4.05 | 中 |
| H1 | 3.0 | 4.5 | 中 |
| H4 | 3.3 | 4.95 | 高 |
| D1 | 3.75 | 5.625 | 高 |

最大スコアは約17点（RSI3+MACD3+Trend2+ADX2+Stoch2+Div3+HTF2）なので、
M1/M5は3-4点でエントリー可能 → **低品質シグナルが大量発生する構造**

### 4.4 月間244トレードの内訳推定

マルチTFエンジンでの月間244トレード（1日約11トレード）:

- 低時間足（M5/M15）からのエントリーが大半を占めると推定
- エントリー閾値が低い + TP=SL + スプレッド負けの構造
- 勝率56%でTP=SLでは、スプレッドコストを回収できない

### 4.5 過去最適化結果との比較

| 指標 | HighWinRate(H1単一) | マルチTF統合(現在) | 差分 |
|------|-------------------|-----------------|------|
| 勝率 | 60.8% | 56.04% | -4.8pt |
| PF | 1.10 | 1.01 | -0.09 |
| 年率 | 19.7% | 0.23% | -19.5pt |
| 月間トレード | ~28 | 244 | +216 |
| SL/TP設定 | ATR×2.5/1.8 | TP=SL(勝率重視) | -- |

**H1単一TFのHighWinRateGeneratorの方が明確に優れている理由:**
1. エントリー閾値が高い（min_score=4、上位足一致必須）
2. TP/SL比率がRR>1（TP=1.8×ATR、SL=2.5×ATR → RR=0.72だがSLが広いためスプレッド影響が小さい）
3. トレード数が少なく、取引コストの累積が小さい
4. 上位足トレンド一致＋MACDモメンタム一致の多重フィルター

---

## 5. 改善提案（優先度付き）

### 優先度1（即座に改善すべき）

#### 5.1.1 TP/SL比率の修正

**問題:** `evaluate_timeframe_signal`で`tp_pips = sl_pips`（TP=SL比率1.0）に固定。
スプレッド/スリッページを考慮すると実効RR<1.0で、勝率56%では赤字。

**提案:**
```python
# 現在
tp_pips = sl_pips  # 勝率重視: TP = SL

# 提案: TF別にTP/SL比率を設定
tp_ratio = {"M5": 1.2, "M15": 1.3, "H1": 1.4, "H4": 1.5, "D1": 1.6}
tp_pips = sl_pips * tp_ratio.get(timeframe, 1.3)
```

**期待効果:** PF 1.01 → 1.05-1.10（スプレッド負けの解消）

#### 5.1.2 低時間足エントリーの閾値引き上げ

**問題:** M1/M5のmin_scoreが3.0-3.4と低く、低品質シグナルが大量発生。

**提案:**
```python
# 現在のmin_scores
"M1": 2.0, "M5": 2.25  # 実効: 3.0, 3.375

# 提案
"M1": 3.5, "M5": 3.5  # 実効: 5.25, 5.25
```

**期待効果:** 月間トレード数 244 → 100-150、勝率向上

#### 5.1.3 SL最小値の引き上げ

**問題:** `sl_pips = max(10.0, ...)`の下限10pipsではスプレッド負けが顕著。

**提案:** `sl_pips = max(20.0, ...)`に引き上げ

**期待効果:** 小さいトレードのスプレッド影響を軽減

### 優先度2（中期的に改善すべき）

#### 5.2.1 イベントキュー構築の高速化

**問題:** `iterrows`による低速なdict化（推定10-30秒/年のオーバーヘッド）

**提案:**
```python
# to_dictによるバッチ変換
records = df.to_dict(orient="records")
for record in records:
    event = CandleEvent(...)
    heapq.heappush(self._events, event)
```

または:
```python
# ソート済みリストのマージ（heapqは不要）
all_events = []
for tf, df in market_data.items():
    tf_events = [CandleEvent(...) for record in df.to_dict("records")]
    all_events.extend(tf_events)
all_events.sort(key=lambda e: e.timestamp)
```

#### 5.2.2 ProcessPoolExecutor再利用

**問題:** `evaluate_batch`で毎回`with ProcessPoolExecutor(...)`でプール生成。

**提案:** `__init__`でExecutorを作成し、インスタンスライフサイクルに合わせる。

#### 5.2.3 インジケーター並列計算

**提案:** `calculate_all_timeframes`をThreadPoolExecutorで並列化。

### 優先度3（長期的な改善）

#### 5.3.1 バックテストエンジンの統一

3種類のエンジン（BacktestEngine, ParallelMultiTFBacktestEngine, FastBacktestEngine）を
1つの統合エンジンに集約し、設定でモードを切り替える方式に。

#### 5.3.2 TA-Libへの移行

pandas_taからTA-Libへの移行で、インジケーター計算を5-10倍高速化。

#### 5.3.3 Rustベースのバックテストコア

パフォーマンスクリティカルなイベントループをRust(PyO3)で実装。

---

## 6. 結論

### 根本的な問題

現在のシステムの最大の問題は**パフォーマンス速度ではなくトレード収益性**にある:

1. **TP=SL比率1.0** + スプレッド1.5pips + スリッページ0.5pips = 実質負けゲーム
2. **低時間足の低閾値エントリー**で低品質シグナルが大量発生
3. **月244トレード**は取引コストの累積で収益を圧迫

### 推奨アクション

1. `evaluate_timeframe_signal`のTP/SL計算をTF別比率に修正（最優先）
2. M1/M5のmin_scoreを引き上げてオーバートレード抑制
3. SL最小値を20pipsに引き上げ
4. 上記修正後にバックテスト再実行し効果測定
5. H1単一TF（HighWinRateGenerator）の設定を参考にマルチTFパラメータ調整

**処理速度の改善は二の次であり、まずはトレードロジックの収益性改善が先決。**

---

作成日: 2026-02-07
