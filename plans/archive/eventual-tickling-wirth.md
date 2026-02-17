# バックテストシステム統合・並列化・マルチタイムフレーム改善計画

## 概要

CLIとWebUIのバックテスト実行を統一モジュール化し、複数タイムフレームでの並列トレード機会検出を実装、マルチCPU処理で高速化する。

## 現状の課題

1. **モジュール分離不十分**: CLIとWebUIで実行パスが異なる（同期vs非同期）
2. **単一モードトレード**: ベースTFでイテレーションし、合意形成で1つのシグナルのみ生成
3. **逐次処理**: 年単位・行単位で完全に逐次実行、並列処理なし

## 改善内容

### 1. CLI/WebUI統合モジュール化

**新規ファイル作成:**
- `src/autotrader/backtest/executor.py` - 統一実行エンジン
- `src/autotrader/backtest/adapters/cli.py` - CLI入出力アダプター
- `src/autotrader/backtest/adapters/webui.py` - WebUI入出力アダプター

**アーキテクチャ:**
```
[CLI Args] ──→ CLIAdapter.from_args() ──┐
                                        ├──→ ExecutorConfig ──→ BacktestExecutor.run()
[API Request] → WebUIAdapter.from_request()┘                              │
                                                                          ↓
                                                                   BacktestResult
                                                                          │
                         ┌────────────────────────────────────────────────┴┐
                         ↓                                                 ↓
               CLIAdapter.print_results()                    WebUIAdapter.to_response()
```

**主要クラス:**
```python
@dataclass
class ExecutorConfig:
    """統一実行設定"""
    start_year: int
    end_year: int
    preset: PresetType
    consensus: ConsensusType
    min_alignment: int
    initial_balance: float
    volume: float
    symbol: str
    data_dir: str
    use_short_timeframe: bool
    use_multi_mode: bool = False      # 新: マルチモードトレード
    parallel_years: bool = True       # 新: 年並列処理
    max_workers: int | None = None    # 新: 並列ワーカー数

class BacktestExecutor:
    """統一バックテスト実行エンジン"""
    def run(self) -> BacktestResult: ...
    def run_parallel(self) -> BacktestResult: ...
    async def run_async(self) -> BacktestResult: ...
```

### 2. マルチタイムフレーム並列機会検出

**新規ファイル作成:**
- `src/autotrader/decision/unified/multi_mode_controller.py` - マルチモード制御
- `src/autotrader/decision/unified/mode_monitor.py` - 個別モード監視
- `src/autotrader/decision/unified/position_aggregator.py` - ポジション統合管理

**3つのトレードモード並列監視:**

| モード | Primary TF | Entry TF | Confirm TFs | 保有期間 | SL/TP |
|--------|-----------|----------|-------------|---------|-------|
| SCALPING | M5 | M1 | M15 | 最大90分 | 10-20/10-30 pips |
| DAY_TRADE | M15 | M5 | H1, H4 | 最大8時間 | 20-40/40-100 pips |
| SWING | H4 | H1 | D1 | 最大2日 | 50-100/100-400 pips |

**処理フロー:**
```
各キャンドル時点:
  ├─ [並列] SCALPING Monitor評価 → ModeSignal | None
  ├─ [並列] DAY_TRADE Monitor評価 → ModeSignal | None
  └─ [並列] SWING Monitor評価    → ModeSignal | None
          │
          ↓
  PositionAggregator
  ├─ グローバルリスク制限チェック（合計ポジション数、総リスク）
  ├─ モード間コンフリクト解決（同方向許可、逆方向は強シグナル優先）
  └─ 各モード独立ポジション管理
```

**主要クラス:**
```python
@dataclass
class ModeSignal:
    """モード別シグナル"""
    mode: TradingStrategyMode
    direction: SignalType
    confidence: float
    sl_pips: float
    tp_pips: float
    holding_period_bars: int

class MultiModeController:
    """マルチモード並列制御"""
    def __init__(
        self,
        modes: list[TradingStrategyMode] = [SCALPING, DAY_TRADE, SWING],
        max_total_positions: int = 3,
        max_per_mode: int = 1,
    ): ...

    def evaluate_all_modes(
        self,
        current_time: pd.Timestamp,
        market_data: dict[str, pd.DataFrame],
    ) -> list[ModeSignal]: ...
```

### 3. マルチCPU並列処理

**新規ファイル作成:**
- `src/autotrader/backtest/parallel.py` - 並列実行エンジン

**並列化レベル:**

| レベル | 方式 | 期待速度向上 | 優先度 |
|--------|------|------------|--------|
| 年単位 | ProcessPoolExecutor | 3-5x | P1 |
| データロード | ThreadPoolExecutor | 2x | P2 |
| 戦略比較 | ProcessPoolExecutor | Nx (N=戦略数) | P2 |
| ウォークフォワード | ProcessPoolExecutor | 2x | P3 |

**年単位並列処理:**
```python
class ParallelYearExecutor:
    """年単位並列実行"""

    def execute(
        self,
        years: list[int],
        config: ExecutorConfig,
    ) -> list[YearResult]:
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._process_year, year, config): year
                for year in years
            }
            results = []
            for future in as_completed(futures):
                results.append(future.result())
        return sorted(results, key=lambda r: r.year)
```

**データロード並列化:**
```python
class ParallelDataLoader:
    """タイムフレーム並列ロード"""

    def load_all_timeframes(
        self,
        timeframes: list[str],
    ) -> dict[str, pd.DataFrame]:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(self._load_tf, tf): tf
                for tf in timeframes
            }
            return {
                futures[f]: f.result()
                for f in as_completed(futures)
            }
```

## 修正対象ファイル

### 新規作成
- `src/autotrader/backtest/executor.py`
- `src/autotrader/backtest/parallel.py`
- `src/autotrader/backtest/adapters/__init__.py`
- `src/autotrader/backtest/adapters/cli.py`
- `src/autotrader/backtest/adapters/webui.py`
- `src/autotrader/decision/unified/multi_mode_controller.py`
- `src/autotrader/decision/unified/mode_monitor.py`
- `src/autotrader/decision/unified/position_aggregator.py`

### 既存修正
- `src/autotrader/backtest/service.py` - Executorを使用するよう変更
- `src/autotrader/backtest/runner.py` - マルチモード対応追加
- `src/autotrader/web/routers/backtest.py` - WebUIAdapterを使用
- `scripts/run_backtest.py` - CLIAdapterを使用

## 実装順序

### Phase 1: 統合モジュール基盤 (2-3日)
1. `ExecutorConfig`と`BacktestExecutor`作成
2. `CLIAdapter`と`WebUIAdapter`作成
3. 既存コードとの結合テスト
4. `scripts/run_backtest.py`をアダプター使用に移行

### Phase 2: マルチCPU並列処理 (2-3日)
1. `ParallelYearExecutor`実装
2. `ParallelDataLoader`実装
3. `BacktestExecutor.run_parallel()`実装
4. パフォーマンステスト（3-5x速度向上確認）

### Phase 3: マルチモードトレード (3-4日)
1. `ModeMonitor`クラス実装（各モード独立評価）
2. `MultiModeController`実装（並列評価・統合）
3. `PositionAggregator`実装（リスク管理）
4. `BacktestRunner`にマルチモード実行パス追加

### Phase 4: 統合・WebUI対応 (1-2日)
1. WebUIルーターを`WebUIAdapter`使用に変更
2. リアルタイム進捗のマルチモード対応
3. エンドツーエンドテスト

## 検証方法

### 単体テスト
```bash
# 新モジュールテスト
pytest tests/unit/backtest/test_executor.py
pytest tests/unit/backtest/test_parallel.py
pytest tests/unit/decision/test_multi_mode_controller.py
```

### 結果一致テスト
```bash
# 並列/逐次で同一結果確認
python scripts/run_backtest.py --years 2020-2024 --no-parallel > sequential.txt
python scripts/run_backtest.py --years 2020-2024 --parallel > parallel.txt
diff sequential.txt parallel.txt
```

### パフォーマンステスト
```bash
# 速度向上確認
time python scripts/run_backtest.py --years 2020-2024 --no-parallel
time python scripts/run_backtest.py --years 2020-2024 --parallel --workers 4
```

### マルチモードテスト
```bash
# マルチモードバックテスト
python scripts/run_backtest.py --years 2023-2024 --multi-mode
# 出力: スキャルピング/デイトレード/スイング別の取引統計
```

## リスクと対策

| リスク | 対策 |
|--------|------|
| 並列/逐次で結果不一致 | 決定論的シード設定、比較テスト必須 |
| メモリ不足（並列時） | max_workers制限、チャンクロード |
| マルチモード競合 | 明確な優先度ルール、ポジション上限 |
| pickleエラー | パスのみ渡す、spawnコンテキスト使用 |

## 後方互換性

- `use_multi_mode=False`でレガシー動作維持
- `parallel_years=False`で逐次実行可能
- 既存の`BacktestService` APIは内部でExecutorを呼び出す形で維持
