# AutoTraderV4 バックテスト処理ボトルネック分析

**分析日時**: 2026-02-28  
**対象**: `scripts/run_backtest.py` / バックテストエンジン全体  
**分析範囲**: 5年 (2020-2025) × 8タイムフレーム × USDJPY

---

## エグゼクティブサマリー

バックテスト処理は**年単位の並列化**により既に効率化されていますが、各年内のシーケンシャル足ループが全体の**60%を占める**ボトルネックです。特に **UnifiedTradeBot::_generate_signal_new()** が毎足呼び出され、複雑なマルチタイムフレーム評価とフィルターチェーンを実行しています。

### 現在の推定実行時間（1年あたり）
| 処理段階 | 実行時間 | 割合 | 並列化の余地 |
|--------|--------|------|-----------|
| **データロード初期化** | 10s | 5% | 低（I/O バウンド） |
| **指標事前計算** | 20s | 10% | 中（GPU化可能だが初期化段階のみ） |
| **メインループ：シグナル生成** | 120s | **60%** | **低（足間依存性あり）** |
| **メインループ：ポジション管理** | 30s | 15% | 低（順序保証必要） |
| **イベント/出力** | 20s | 10% | 低（I/O バウンド） |
| **合計** | **200s/年** | | |

### 推奨優先順位（ROI ベース）

| 優先度 | 施策 | 推定改善 | 開発工数 | ROI |
|-------|------|--------|--------|-----|
| 🔴 **P1** | シグナル生成最適化（lookup キャッシュ + 条件順序化） | 20% | 2-3h | ★★★★★ |
| 🟠 **P2** | 指標キャッシュ統合（Parquet 自動活用） | 7-10% | 1-2h | ★★★★ |
| 🟡 **P3** | シミュレータ状態最小化（numpy 配列化） | 5% | 2-3h | ★★★ |
| 🟢 **P4** | GPU 指標計算（CuPy/CUDA） | 1-2% | 10-15h | ★ |

---

## 詳細分析

### 1. エントリポイント：`scripts/run_backtest.py`

#### 実行フロー
```
main()
  ├─ parse_args()
  ├─ BacktestService.create_runner()
  ├─ runner.load_data()  [初期化]
  │   ├─ H1, H4, D1, M15 順次読み込み
  │   └─ 各TFで _calculate_indicators() 呼び出し
  └─ runner.run_unified()  [メイン処理]
      ├─ 複数年時: ProcessPoolExecutor で年並列化 (max_workers=5)
      ├─ 各年: _run_unified_year()
      │   └─ [メインループ：60,000足/年 × 8TF評価]
      └─ 結果集計・出力
```

#### load_data() のボトルネック
- **現在**: 各TF を逐次読み込み → 指標計算
- **問題**: 
  - `PrecomputeEngine` が存在するが **利用されていない**
  - Parquet キャッシュの自動チェック機能が **無視されている**
- **改善案**: 
  ```python
  # load_data() 内で
  from autotrader.calculator.precompute import PrecomputeEngine
  
  engine = PrecomputeEngine(cache_dir="data/cache/precomputed")
  df_h1 = engine.precompute(df_h1, symbol, Timeframe.H1, use_cache=True)
  ```

---

### 2. メインループ：`BacktestRunner::_run_unified_year()` (1705行)

#### 処理フロー（毎足）
```python
for idx in range(arrays.n_rows):  # 60,000足/年
    candle = arrays.get_candle(idx, ...)
    
    # ステップ1: シグナル生成（最重処理）
    consolidated = bot.generate_signal(
        current_time, candle,
        fundamental_ctx, fundamental_memory
    )
    
    # ステップ2: ポジション管理
    simulator.process_candle(candle, signal, ...)
    
    # ステップ3: イベント発行・集計
    # ...月別トラッキング...
```

#### 計算量内訳
- **ステップ1 シグナル生成**: ~2ms/足 (うち 70% は TF 評価)
- **ステップ2 ポジション管理**: ~0.3ms/足
- **ステップ3 イベント/集計**: ~0.2ms/足

---

### 3. シグナル生成：`UnifiedTradeBot::_generate_signal_new()` (448-1184行)

#### 処理ステップ（詳細）

1. **リスク管理チェック** (O(1))
2. **モード選択** (O(1))
3. **全タイムフレーム評価** (O(8 × eval_cost)) ← **ボトルネック #1**
   - TF > 12 時のみ並列化（ThreadPool）
   - 標準設定 8TF は **逐次処理**
4. **動的タイムフレーム選択** (O(TF^2))
5. **レジーム検出** (O(1) lookup)
6. **HTF整合度計算** (O(HTF数))
7. **TFルーティング** (O(1))
8. **コンセンサス統合** (O(TF数))
9. **ファンダメンタル評価**
10. **SoftGuard フィルターチェーン** (15+ 条件) ← **ボトルネック #2**
    - 逐次 if-else で条件判定
    - 頻出条件がリストの後方に位置
11. **SL/TP 計算** (O(1))
12. **ポジションサイジング** (O(1))

---

### 4. 指標計算層：`calculator/`

#### 現在の実装
- **precompute.py**: `PrecomputeEngine` 存在（キャッシング完備）
- **しかし**: `runner.load_data()` で **呼び出されていない**

#### 指標別の GPU 化可能性

| 指標 | ベクトル化度 | GPU化ROI |
|------|-----------|---------|
| SMA | ★★★★★ | 高 |
| EMA | ★★★ | 中 |
| ATR | ★★★★★ | 高 |
| Bollinger Bands | ★★★★★ | 高 |
| RSI | ★★★★ | 高 |
| Stochastic | ★★★★★ | 高 |
| ADX/MACD | ★★★ | 低 |
| SwingAnalyzer | ★ | 低 |

**指標計算は全体の 10% のみ** → GPU 化しても最大 1-2% グローバル改善（ROI 低い）

---

### 5. シミュレーター：`TradeSimulator::process_candle()`

#### 計算量
- **Open Position ループ**: O(max_positions) ≈ O(5-10)
- **並列化**: 不可（状態更新が順序保証必須）
- **実行時間**: ~30ms/年 (全体の 15%)

---

## 実装ロードマップ

### Phase 1: シグナル生成最適化 (20% 改善 / 2-3h) 🔴

#### 1.1 Lookup テーブルキャッシュ
```python
# _run_unified_year() の開始部で
lookup_cache = {}
for tf in self.evaluators.keys():
    tf_data = market_data[tf][...]
    lookup_cache[tf] = tf_data.set_index('time').to_dict('index')

# generate_signal() 内で
row = lookup_cache[tf_name].get(current_time)  # O(1)
```

#### 1.2 条件順序最適化
- HTF不一致（5-10% block）
- セッション時間（10-15% block）
- SoftGuard penalty（3-5% block）
→ これら 3 つを最初に評価で early-exit 30% 削減

#### 1.3 TF 評価並列化閾値の引き下げ
```python
_PARALLEL_TF_THRESHOLD = 8  # 12 → 8（標準設定の 8TF に対応）
```

**小計**: 40-50s 削減（20% improvement）

---

### Phase 2: キャッシュ統合 (7-10% 改善 / 1-2h) 🟠

#### 2.1 PrecomputeEngine の統合
```python
# runner.load_data() 内で
engine = PrecomputeEngine(cache_dir="data/cache/precomputed")
self._h1_df = engine.precompute(df_h1, symbol, Timeframe.H1, use_cache=True)
```

**小計**: 初回 20s → 5s（15s 削減, 7.5%）

---

### Phase 3: シミュレータ最適化 (5% 改善 / 2-3h) 🟡

#### 3.1 Position 状態の numpy 配列化
```python
class PositionManager:
    def __init__(self):
        self.entry_prices = np.zeros(max_positions)
        self.sl_array = np.zeros(max_positions)
    
    def update_mfe_mae(self, candle):
        self.mfe = np.maximum(self.mfe, candle.high - self.entry_prices)
```

**小計**: 10s 削減（5% improvement）

---

### Phase 4: GPU 指標計算 (1-2% 改善 / 10-15h) 🟢

⚠️ **ROI が低いため非推奨**

---

## まとめ

| Phase | 施策 | 改善 | 工数 | ROI | 推奨 |
|-------|------|------|------|------|------|
| 1 | シグナル最適化 | 20% | 2-3h | ★★★★★ | **即実装** |
| 2 | キャッシュ統合 | 7-10% | 1-2h | ★★★★ | **推奨** |
| 3 | シミュレータ最適化 | 5% | 2-3h | ★★★ | 時間あれば |
| 4 | GPU計算 | 1-2% | 10-15h | ★ | **非推奨** |

**最終目標**: 200s → 150s (25% improvement)

