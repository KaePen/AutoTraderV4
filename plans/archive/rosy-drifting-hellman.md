# バックテスト高速化計画

## Context

バックテストの実行速度が非常に遅い。既存の`FastBacktestEngine`（並列チャンク処理）は存在するが、以下の重大なボトルネックにより十分な速度が出ていない:

1. **`iterrows()`** - Pandasの最遅イテレーション方法が7箇所で使用
2. **DataFrame→dict直列化** - プロセス間データ転送にpickle用dict変換（重い）
3. **O(N)検索** - `_get_current_row()`が毎回DataFrame全体をスキャン
4. **インジケータ再計算** - 各チャンクワーカーがウォームアップデータなしで実行

## 方針

既存の`FastBacktestEngine`を改修し、以下の最適化を段階的に適用する。インジケータは全期間一括で事前計算し、事前計算済みデータを各チャンクワーカーに効率的に渡す。

## 実装ステップ

### Step 1: CandleArraysヘルパー作成 + iterrows()置換

**対象ファイル**: `src/autotrader/backtest/candle_arrays.py`（新規）

`iterrows()`を排除するためのnumpy配列ベースCandleアクセサを作成。

```python
@dataclass(frozen=True)
class CandleArrays:
    times: np.ndarray       # datetime64
    opens: np.ndarray       # float64
    highs: np.ndarray       # float64
    lows: np.ndarray        # float64
    closes: np.ndarray      # float64
    volumes: np.ndarray     # float64
    n_rows: int

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> CandleArrays: ...
    def get_candle(self, idx: int, symbol: str, timeframe: Timeframe) -> Candle: ...
```

**iterrows()置換対象** (7箇所):

| ファイル | 行 | 用途 |
|---------|---:|------|
| `fast_backtest.py` | 199 | チャンクワーカーループ |
| `engine.py` | 280 | BacktestEngine.run |
| `engine.py` | 895 | UnifiedBacktestEngine.run_year |
| `runner.py` | 408 | _run_year |
| `runner.py` | 981 | _run_unified_year |
| `optimizer.py` | 478 | 最適化ループ |
| `events.py` | 205 | TimelineEventQueue構築 |

**想定改善**: ループ速度 3-10倍向上

### Step 2: _get_current_row() O(N)→O(1)最適化

**対象ファイル**: `src/autotrader/decision/unified/trade_bot.py` (836行目)

現在の実装:
```python
mask = df.index <= current_time  # O(N) 全行スキャン
return df.loc[mask].iloc[-1]
```

これが1キャンドルあたり複数回（各タイムフレーム×複数呼び出し箇所）実行される。

**修正**: インデックス追跡による前方スキャン（O(1)償却）
```python
def _get_current_row(self, timeframe, current_time):
    last_idx = self._current_indices.get(timeframe, 0)
    time_values = self._time_arrays[timeframe]  # 事前キャッシュ
    n = len(time_values)
    while last_idx + 1 < n and time_values[last_idx + 1] <= current_time:
        last_idx += 1
    self._current_indices[timeframe] = last_idx
    return df.iloc[last_idx] if time_values[last_idx] <= current_time else None
```

- `set_market_data()`時に`_time_arrays`と`_current_indices`を初期化
- `current_time`は常に前進するため、前回位置から0-2ステップで到達

**想定改善**: シグナル生成速度 5-20倍向上（最大のボトルネック）

### Step 3: Parquetファイルパス渡しによる直列化排除

**対象ファイル**: `src/autotrader/backtest/fast_backtest.py`

現在の問題:
```python
# 各チャンクでDataFrame→dict→pickle→dict→DataFrame
df_dict = chunk_df.to_dict(orient="list")  # 重い
# 時刻のisoformat変換も各要素に対して実行
```

**修正**: 事前計算済みParquetファイルのパスをワーカーに渡す

```python
def _prepare_chunk_files(self, df, market_data, chunks) -> list[dict]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="autotrader_bt_"))
    chunk_files = []
    for chunk_id, (chunk_start, chunk_end) in enumerate(chunks):
        warmup_start = chunk_start - timedelta(hours=warmup_bars * tf_minutes / 60)
        data_end = chunk_end + timedelta(days=30)
        # 基準データをParquetに保存
        chunk_df = df[(df["time"] >= warmup_start) & (df["time"] < data_end)]
        base_path = tmp_dir / f"chunk_{chunk_id}_base.parquet"
        chunk_df.to_parquet(base_path, index=False)
        # 市場データも同様
        tf_paths = {}
        for tf_str, tf_df in market_data.items():
            tf_path = tmp_dir / f"chunk_{chunk_id}_{tf_str}.parquet"
            filtered.to_parquet(tf_path, index=False)
            tf_paths[tf_str] = str(tf_path)
        chunk_files.append({"base": str(base_path), "tfs": tf_paths})
    return chunk_files
```

ワーカー側:
```python
def _process_chunk_worker(chunk_id, chunk_start, chunk_end, base_path, tf_paths, ...):
    df = pd.read_parquet(base_path)  # 高速
    market_data = {tf: pd.read_parquet(p) for tf, p in tf_paths.items()}
```

**想定改善**: プロセス間データ転送 3-5倍高速化

### Step 4: ウォームアップ期間の導入

**対象ファイル**: `src/autotrader/backtest/fast_backtest.py`

現在の問題: ワーカーが`chunk_start`以前のデータを切り捨て（164行目）ており、シグナル生成器の内部状態が未構築のまま取引開始。

**修正**: 50バー分のウォームアップデータを含め、ウォームアップ中はシグナル生成のみ実行（エントリー不可）

```
|--- warmup (50本) ---|--- entry window (3ヶ月) ---|--- exit tail (30日) ---|
```

```python
for i in range(arrays.n_rows):
    candle_time = arrays.get_time(i)
    candle = arrays.get_candle(i, symbol, base_tf)

    if candle_time < chunk_start:
        # ウォームアップ: シグナル生成で内部状態構築
        bot.generate_signal(pd.Timestamp(candle_time), candle)
        continue

    if candle_time >= chunk_end:
        # exit tail: 既存ポジションのSL/TPのみ処理
        if not has_open_position:
            break
        simulator.process_candle(candle, None)
        ...
    else:
        # エントリーウィンドウ: 通常処理
        ...
```

**50バーの根拠**: インジケータは全期間で事前計算済み（200バー不要）。50バーはシグナル生成器の状態安定化に十分。

### Step 5: TradeSimulator最適化

**対象ファイル**: `src/autotrader/backtest/simulator.py`

微細最適化（ホットパス改善）:

1. **pip変換の事前計算**: `__init__`で`spread_pips * 0.01`等を計算しキャッシュ
2. **daily_pnl記録の最適化**: 日付変更時のみ記録（毎キャンドルでstrftime不要）
3. **単一ポジションの高速パス**: `max_positions=1`時のインライン処理

### Step 6: テスト・検証

1. **ベースライン計測**: 現在の`--fast`モードで1年分の実行時間を計測
2. **Step 1-2適用後**: 同じデータで実行時間を計測（iterrows + O(N)検索解消）
3. **Step 3-4適用後**: 並列実行の全体時間を計測
4. **結果比較**: 逐次実行と並列実行のトレード結果を比較
   - トレード数差異 < 5%
   - 勝率差異 < 1%
   - PF差異 < 0.05

## 修正対象ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/backtest/candle_arrays.py` | **新規** CandleArraysヘルパー |
| `src/autotrader/backtest/fast_backtest.py` | Parquetパス渡し、ウォームアップ、iterrows排除 |
| `src/autotrader/backtest/engine.py` | iterrows排除 (2箇所) |
| `src/autotrader/backtest/runner.py` | iterrows排除 (2箇所) |
| `src/autotrader/backtest/events.py` | iterrows排除 (1箇所) |
| `src/autotrader/backtest/optimizer.py` | iterrows排除 (1箇所) |
| `src/autotrader/backtest/simulator.py` | ホットパス最適化 |
| `src/autotrader/decision/unified/trade_bot.py` | O(1)インデックス追跡 |

## 想定効果

| 最適化 | 対象 | 改善見込み |
|--------|------|-----------|
| iterrows→numpy配列 | ループ全体 | 3-10x |
| O(N)→O(1)検索 | シグナル生成 | 5-20x |
| Parquetパス渡し | データ転送 | 3-5x |
| ウォームアップ導入 | 結果精度 | 精度向上 |
| Simulator最適化 | シミュレーション | 1.2-1.5x |
| **総合** | **全体** | **5-15x** |

## 実行順序

Step 1 → Step 2 → Step 3 → Step 4 → Step 5 → Step 6

Step 1,2は独立して適用可能（非並列の通常バックテストも高速化）。Step 3,4はFastBacktestEngine固有の改善。
