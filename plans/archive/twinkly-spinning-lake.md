# ロット計算改善：シグナル強度比例化 + 年単位並列バックテスト

## Context

ユーザーから2点の改善要求（初回）＋方針修正：

1. **シグナル強度比例ロット**：現在の3段階ステップ関数（0.5x/線形/1.2x）をコンセンサススコアと比例する連続関数に変更
2. **バックテストの並列化**：旧方針（タイムフレーム並列）から**年単位並列**に変更

### 方針変更の背景

- バックテスト = リアルトレードの試験の場 → **実際のトレードロジック・資金管理をそのまま使う必要がある**
- 旧「パラレルモード」（`ParallelMultiTFBacktestEngine`）はタイムフレームを並列評価する独自エンジンであり、リアルロジックとは乖離していた
- 新方針：**年ごとに独立して実行**（各年が `UnifiedTradeBot` + 実際の `PositionSizer` を使用）し、複数年を並列で処理する

---

## 現状整理

### ロット計算（問題点）
- `trade_bot.py`: `confidence = min(score/threshold, 1.0)` → **1.0で上限打ち切り**
- `position_sizer.py`: `_calculate_confidence_adjust` が3段階ステップ関数
  - `confidence >= 0.7` → 1.2x、`confidence <= 0.5` → 0.5x、線形補間
- **問題**：しきい値以上のスコアが全て 1.2x → 強いシグナルほど多くロットを持てない

### バックテスト並列化（現状と問題）
- `runner.py` の `run_unified()`: `bot = UnifiedTradeBot(...)` を作成し、年ループで**同一インスタンスを再利用**（`bot.state` が累積）
- 並列化には各年で **fresh な `UnifiedTradeBot` インスタンス** が必要
- シミュレーター (`TradeSimulator`) は既に年ごとに新規作成されている → OK
- `initial_balance` は既に各年独立して設定されている → OK（累積はなし）

---

## 変更設計

### Change 1: シグナル強度比例ロット

**`src/autotrader/decision/unified/trade_bot.py`**（line ~773）

```python
# Before
confidence = min(consensus.score / consensus.threshold, 1.0)

# After（上限削除、しきい値超えの強度を反映）
confidence = consensus.score / consensus.threshold
```

**`src/autotrader/decision/unified/position_sizer.py`**

`_calculate_confidence_adjust` を区分線形関数に置き換え：

```
confidence = score/threshold（上限なし）

しきい値未満（confidence < 1.0）:
  lot_mult = 0.3 + confidence * 0.7
  → confidence=0.0: 0.3x, confidence=0.5: 0.65x, confidence=1.0: 1.0x

しきい値以上（confidence >= 1.0）:
  lot_mult = 1.0 + min(confidence - 1.0, 1.0) * 0.5
  → confidence=1.0: 1.0x, confidence=1.5: 1.25x, confidence=2.0: 1.5x（上限）
```

| シグナル強度 | confidence | ロット係数 |
|---|---|---|
| しきい値の50% | 0.5 | 0.65x |
| ちょうどしきい値 | 1.0 | 1.0x（基準） |
| しきい値の150% | 1.5 | 1.25x |
| しきい値の200% | 2.0 | 1.5x（上限） |

`PositionSizerConfig` の `confidence_high_threshold` / `confidence_low_threshold` は後方互換で残す（内部では未使用）。

---

### Change 2: 年単位並列バックテスト

**実行イメージ:**
```
Year 2023: UnifiedTradeBot [M1+M5+M15+M30+H1+H4+H8+D1評価] → 1,000,000 JPYスタートで通年トレード
Year 2024: UnifiedTradeBot [同上]                             → 1,000,000 JPYスタートで通年トレード  ← 並列
Year 2025: UnifiedTradeBot [同上]                             → 1,000,000 JPYスタートで通年トレード  ← 並列
```

**`src/autotrader/backtest/runner.py`**

#### 2-A: `_run_unified_year()` を self-contained 化

現在 `bot` インスタンスを外部から引数で受け取り、年をまたいで同一インスタンスを**再利用**している。これを廃止し、年ごとに内部で生成する：

```python
# Before（bot.state が年を跨いで累積する問題あり）
def _run_unified_year(self, bot: UnifiedTradeBot, sim_config: SimulatorConfig, year: int, ...) -> dict:
    ...

# After（年ごとに fresh な bot を内部生成）
def _run_unified_year(self, bot_config: UnifiedBotConfig, sim_config: SimulatorConfig, year: int, ...) -> dict:
    bot = UnifiedTradeBot(bot_config)
    bot.state.initial_equity = sim_config.initial_balance
    bot.state.equity = sim_config.initial_balance
    ...
```

#### 2-B: `run_unified()` で年並列実行

```python
# run_unified() 内
from concurrent.futures import ThreadPoolExecutor, as_completed

years = list(range(start_year, end_year + 1))

if len(years) > 1 and not sequential_mode:
    # 年ごとに並列実行（verbose出力は年次サマリのみ）
    max_workers = min(len(years), os.cpu_count() or 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(self._run_unified_year, bot_config, sim_config, year, ...): year
            for year in years
        }
        for future in as_completed(futures):
            yearly_results.append(future.result())
    yearly_results.sort(key=lambda r: r["year"])
else:
    # 単年またはシーケンシャルモード
    for year in years:
        yearly_results.append(self._run_unified_year(bot_config, sim_config, year, ...))
```

#### 2-C: 並列実行時のコンソール出力制御

- 並列実行時は `ConsoleEventListener`（トレード毎の verbose 出力）をアタッチしない（スレッド間で出力が混在するため）
- 年次サマリは全年完了後にメインスレッドで `print_yearly_results()` を呼び出して表示
- `--verbose` 指定時は各年のログを `backtest_{year}.log` に書き出し（ファイル名で年分離）
- `BacktestRunnerConfig` に `sequential: bool = False` フィールドを追加（デバッグ用）

#### 2-D: `initial_balance` を 1,000,000 JPY に設定

- `scripts/run_backtest.py` で `initial_balance=1_000_000` を明示的に設定
- `BacktestRunnerConfig` のデフォルトも 1,000,000 に更新

**`scripts/run_backtest.py`**

- `--parallel` フラグは **廃止**（年並列はデフォルト動作になるため不要）
- `--sequential` フラグを追加（デバッグ用にシーケンシャル実行を強制したい場合向け）
- 旧 `_run_parallel_multi_tf()` を呼ぶパスは削除（`ParallelMultiTFBacktestEngine` コード自体は残す）

---

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `src/autotrader/decision/unified/trade_bot.py` | confidence の上限削除（line ~773） |
| `src/autotrader/decision/unified/position_sizer.py` | `_calculate_confidence_adjust` を区分線形関数に置き換え |
| `src/autotrader/backtest/runner.py` | `_run_unified_year()` を self-contained 化、`run_unified()` に年並列実行を追加 |
| `scripts/run_backtest.py` | `--parallel` フラグ廃止、`initial_balance=1_000_000` 設定、年並列デフォルト化 |

※ `engine.py` の `ParallelMultiTFBacktestEngine` は変更なし（削除は別タスク）

---

## 検証方法

1. `pytest tests/unit/ -q` — 全テストPASS確認
2. 単年: `python scripts/run_backtest.py --years 2025` — 従来通り動作することを確認
3. 多年: `python scripts/run_backtest.py --years 2023-2025` — 3年が並列実行され、年別サマリが表示されることを確認
4. ロット確認: `--verbose` 付きでログを確認し、ロット値がシグナル強度に比例して変動することを確認
5. エッジケース: SL=0 / 最小ロット下限（0.01） / 全年同一結果（シードチェック）
