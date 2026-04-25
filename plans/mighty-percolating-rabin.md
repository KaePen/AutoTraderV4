# BT-リアル乖離修正: ティックエントリーシミュレーション + 経済指標ホールド

## Context

BT-リアル乖離の主要原因2点:

1. **ティック約定 vs バーOpen約定**: リアルでは `TickEntryOptimizer` がスプレッド/モメンタム/リトレースを評価し最適点で約定。BTでは「次M15足のOpen」で機械的に約定 → 最大21pip差。
2. **経済指標ホールド未稼働**: BTインフラは存在するが、**`fundamental_csv` がデフォルト None** のため標準実行で経済イベントフィルタが動いていない。さらに `fundamental_utils.py` のパス探索が `data/{SYMBOL}/events/` だが、実データは `data/fundamental/events/` にある。

---

## 改善1: M1ベース TickEntrySimulator

### 方針

M1 OHLCV + SPREAD データ（全ペア 2010-2025）を使い、M15シグナル発火後の15分ウィンドウ内でリアルの `TickEntryOptimizer` と同等のスコアリングを行い、最適エントリー価格を決定する。

### フロー

```
M15シグナル発火 → pending_signal にセット（従来通り）
  ↓
次足 _execute_pending_entry() 内:
  tick_simulator 有効 & M1データあり?
    → Yes: M1足ウィンドウ（signal_time ~ signal_time+15min）をスキャン
           各M1足を擬似ティック化 → spread/momentum/retrace スコア評価
           composite >= threshold → そのM1足の最適価格で約定
           全M1足で不成立 → タイムアウト（最終M1の close で約定）
    → No:  従来通り candle.open で約定（後方互換）
```

### M1→擬似ティック変換

各M1足から4つの擬似ティックを生成:
```python
# BUY シグナル想定: O→L→H→C の順（不利→有利の動きを再現）
# SELL シグナル想定: O→H→L→C の順
pseudo_ticks = [
    {"bid": open - half_spread, "ask": open + half_spread},
    {"bid": low - half_spread,  "ask": low + half_spread},   # or high
    {"bid": high - half_spread, "ask": high + half_spread},   # or low
    {"bid": close - half_spread,"ask": close + half_spread},
]
```
SPREAD列（point単位）をそのまま使用。

### 新規ファイル

**`autotrader/backtest/tick_simulator.py`**
```python
@dataclass(frozen=True)
class TickSimConfig:
    """ティックシミュレーション設定"""
    enabled: bool = False
    window_minutes: int = 15
    composite_threshold: float = 0.65
    spread_weight: float = 0.4
    momentum_weight: float = 0.4
    retracement_weight: float = 0.2
    timeout_execute: bool = True  # タイムアウト時に強制約定
    spread_threshold_pips: float = 2.0

class BacktestTickSimulator:
    """M1データを使ったエントリー最適化シミュレーター"""

    def find_optimal_entry(
        self, signal: Signal, m1_window: pd.DataFrame, symbol: str,
    ) -> TickSimResult | None:
        """M1ウィンドウ内の最適エントリーポイントを探索"""
```

スコアリングロジックは `TickEntryOptimizer` の以下メソッドと同等:
- `_evaluate_spread()` (`tick_entry_optimizer.py:369-426`) → M1足のSPREAD列で評価
- `_evaluate_momentum()` (`tick_entry_optimizer.py:428-483`) → M1 Close推移の方向一致率
- `_evaluate_retracement()` (`tick_entry_optimizer.py:485-542`) → M1足の逆行→回復パターン

### 変更ファイル

| ファイル | 変更内容 |
|---------|---------|
| `backtest/simulator.py` | `SimulatorConfig` に `tick_sim_config: TickSimConfig` 追加。`_execute_pending_entry()` で tick_simulator 呼び出し |
| `backtest/year_runner.py` | simulator に M1 DataFrame 参照を渡す |
| `backtest/runner.py` | M1データのロード（`_m1_df` は既存プロパティ）、TickSimConfig の伝搬 |

### overrides での有効化

```json
{
  "overrides": {
    "backtest": {
      "tick_sim_enabled": true
    }
  }
}
```

---

## 改善2: 経済指標ホールドの自動有効化

### 根本原因

1. **`fundamental_csv` がデフォルト None**: `BacktestExecutorConfig.fundamental_csv = None`（executor.py:65）。明示的にパスを渡さないと指標フィルタが動かない。
2. **パス不一致**: `fundamental_utils.py` は `data/{SYMBOL}/events/csv/events_YYYY.csv` を探すが、実データは `data/fundamental/events/events_YYYY.csv`（シンボル共通）にある。

### 修正方針

**自動発見を追加**: `runner.py` の `run_backtest()` で、`fundamental_csv` が未指定の場合に `data/fundamental/events/events_YYYY.csv` を自動探索してロードする。

### 変更内容

**`autotrader/backtest/fundamental_utils.py`**:
- `create_fundamental_provider()` にフォールバックパス追加:
  ```
  1. data/{SYMBOL}/events/cache/events_YYYY.parquet  （既存・シンボル固有）
  2. data/{SYMBOL}/events/csv/events_YYYY.csv         （既存・シンボル固有）
  3. data/fundamental/events/events_YYYY.csv           （新規・共通フォールバック）
  ```

**`autotrader/backtest/runner.py`** (`run_backtest()` 内):
- `fundamental_csv_list` が空 かつ `fundamental_csv` が None の場合:
  - `create_fundamental_provider()` を呼び出して自動発見
  - 見つかれば `fundamental_provider` にセット

**`autotrader/backtest/year_runner.py`**:
- PRE_EVENT スキップ時のカウンター追加
- 結果dict に `events_skipped_count` を含める

### 既存動作への影響

- `fundamental_csv` を明示的に渡す既存フローは変更なし
- 自動発見はフォールバックとしてのみ動作
- イベントデータが見つからない場合は従来通り None（フィルタ無し）

---

## 実装順序

1. **Phase 1**: 経済指標ホールド修正（小規模・リスク低）
   - `fundamental_utils.py` パス修正
   - `runner.py` 自動発見追加
   - テスト実行で `events_skipped_count > 0` を確認

2. **Phase 2**: TickEntrySimulator（新規モジュール）
   - `tick_simulator.py` 新規作成
   - `simulator.py` の pending entry 拡張
   - `year_runner.py` / `runner.py` のM1データ連携
   - ユニットテスト + 統合テスト

3. **Phase 3**: 検証バックテスト
   - USDJPY 2024 で tick_sim ON/OFF 比較
   - キューランナー経由で実行

---

## 検証方法

### 経済指標ホールド
```bash
# ユニットテスト
pytest tests/unit/backtest/ -k "fundamental" -v

# 統合テスト: USDJPY 2024 で events_skipped_count を確認
# キューランナーに投入
```

### TickEntrySimulator
```bash
# ユニットテスト
pytest tests/unit/backtest/test_tick_simulator.py -v

# 回帰テスト: tick_sim=OFF で既存結果と一致
# 比較テスト: tick_sim=ON でエントリー価格がM1範囲内
```
