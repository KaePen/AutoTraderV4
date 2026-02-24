# リファクタリング計画: パッケージ構造 + バックテスト/リアルのアーキテクチャ整理

## Context

2つの課題を一括で対応する:

1. **パッケージ構造の簡素化**: `src/autotrader/` → `autotrader/`（プロジェクト直下）
2. **アーキテクチャ違反の修正**: `.claude/rules/core.md` に基づく backtest/live 間の依存方向修正

---

## Phase 0: src/ 階層の除去 (PR 1本) ★最初に実施

**目的**: `src/autotrader/` → `autotrader/` に移動し、不要な階層を除去

### 変更内容

| 操作 | 対象 |
|------|------|
| 移動 | `src/autotrader/` → `autotrader/` (プロジェクトルート直下) |
| 削除 | `src/` ディレクトリ |
| 修正 | `pyproject.toml` |
| 修正 | `CLAUDE.md`, `.claude/rules/core.md`, `README.md` のパス記載 |

### pyproject.toml の変更箇所

```toml
# Before
[tool.hatch.build.targets.wheel]
packages = ["src/autotrader"]

[tool.pytest.ini_options]
pythonpath = ["src"]
addopts = "-v --cov=src/autotrader --cov-report=term-missing"

# After
[tool.hatch.build.targets.wheel]
packages = ["autotrader"]

[tool.pytest.ini_options]
pythonpath = ["."]
addopts = "-v --cov=autotrader --cov-report=term-missing"
```

### importは変更なし
- `from autotrader.backtest import ...` → そのまま
- パッケージ名 `autotrader` は不変

### 検証
- `python -m pytest tests/ -x -q` PASS
- `python -c "from autotrader.backtest import BacktestRunner"` 成功
- `git diff --stat` でファイル移動のみ確認

---

## Phase 1: SignalStepRecord を core/ に移動 (PR 1本)

**目的**: decision/ → backtest/ の逆依存を解消

### 変更内容

| 操作 | ファイル |
|------|---------|
| 新規 | `autotrader/core/diagnostics.py` - SignalStepRecord を定義 |
| 修正 | `autotrader/backtest/trade_flow_analyzer.py` - 定義削除、core から再エクスポート |
| 修正 | `autotrader/decision/unified/trade_bot.py` - 5箇所のimport先変更 |

### 詳細
- `trade_bot.py` の L405, L476, L568, L743, L804 の条件付きimportを `core.diagnostics` に変更
- `trade_flow_analyzer.py` は後方互換の再エクスポートを維持

### 検証
- `python -m pytest tests/ -x -q` PASS

---

## Phase 2: IndicatorCalculator を calculator/ に移動 (PR 1本)

**目的**: live/ → backtest/ の逆依存を解消（最重要）

### 変更内容

| 操作 | ファイル |
|------|---------|
| 新規 | `autotrader/calculator/technical/batch.py` - TechnicalIndicatorBatch |
| 修正 | `autotrader/calculator/technical/__init__.py` - エクスポート追加 |
| 修正 | `autotrader/backtest/indicators.py` - クラス定義削除、再エクスポートshim |
| 修正 | `autotrader/backtest/__init__.py` - importパス更新 |
| 修正 | `autotrader/live/engine.py` L41 - importを calculator/ に変更 |

### 詳細
- **改名**: `IndicatorCalculator` → `TechnicalIndicatorBatch`（core/interfaces の同名抽象クラスとの衝突回避）
- `MultiTimeframeDataLoader` は backtest/indicators.py に残す（データI/O担当）
- backtest側は `TechnicalIndicatorBatch as IndicatorCalculator` で後方互換維持

### 検証
- `python -m pytest tests/ -x -q` PASS
- live/engine.py が backtest/ をインポートしていないこと確認

---

## Phase 3: backtest/filters/ を constraint/filters/ に移動 (PR 1本)

**目的**: トレード制約ロジックを正しいモジュールに配置

### 変更内容

| 操作 | ファイル |
|------|---------|
| 新規 | `autotrader/constraint/filters/filter_result.py` - 統一 FilterResult |
| 移動 | `backtest/filters/event_filter.py` → `constraint/filters/event_filter.py` |
| 移動 | `backtest/filters/session_filter.py` → `constraint/filters/session_filter.py` |
| 移動 | `backtest/filters/volatility_filter.py` → `constraint/filters/volatility_filter.py` |
| 移動 | `backtest/filters/filter_manager.py` → `constraint/filters/filter_manager.py` |
| 修正 | `autotrader/constraint/filters/__init__.py` - エクスポート追加 |
| 修正 | `autotrader/backtest/filters/*.py` - 再エクスポートshim |

### 詳細
- 4ファイルそれぞれのローカル `FilterResult` を `filter_result.py` に統一
- `BacktestFilterManager` → `FilterManager` に改名
- constraint/filters/ の既存ファイル（trend_filter.py, adx_filter.py）と共存
- backtest/filters/ は再エクスポートshimとして残す

### 検証
- `python -m pytest tests/ -x -q` PASS
- `from autotrader.constraint.filters import FilterManager` 成功

---

## Phase 4: OptimizedGenerator を削除 (PR 1本)

**目的**: backtest/ から独自シグナル生成ロジックを除去

### 変更内容

| 操作 | ファイル |
|------|---------|
| 修正 | `autotrader/backtest/optimizer.py` - OptimizedGenerator, OptimizeConfig 削除 |
| 修正 | `autotrader/backtest/__init__.py` - エクスポート削除 |
| 確認 | `reports/` 配下 - 参照があれば `scripts/archive/` に移動 |

### 詳細
- ユーザー未使用のため decision/ へ移動せず**削除**
- `run_optimization()`, `run_backtest_period()` 等も OptimizedGenerator に依存する場合は削除
- reports/ 配下で参照するスクリプトは archive/ に移動

### 検証
- `python -m pytest tests/ -x -q` PASS
- `grep -r "OptimizedGenerator" autotrader/` が0件

---

## Phase 5: SimulatorConfig の from_preset() ファクトリ追加 (PR 1本)

**目的**: トレードパラメータの取得を get_preset() に一元化

### 変更内容

| 操作 | ファイル |
|------|---------|
| 修正 | `autotrader/backtest/simulator.py` - SimulatorConfig.from_preset() 追加 |
| 修正 | `autotrader/backtest/config.py` - UnifiedBacktestConfig.from_preset() 追加 |

### 詳細
- `from_preset(symbol: str)` で `get_preset()` から spread_pips, pip_value 等を自動取得
- 既存デフォルト値はフォールバックとして維持（破壊的変更なし）

### 再利用する既存関数
- `autotrader/config/trading_params.py` の `get_preset(symbol)`
- `autotrader/config/trading_params.py` の `SymbolPreset`

### 検証
- `python -m pytest tests/ -x -q` PASS
- `SimulatorConfig.from_preset("USDJPY").spread_pips` が正しい値を返す

---

## Phase 6: 後方互換shim 削除 (最終クリーンアップ)

Phase 1-5 が全てマージ・安定稼働後に実施。

| ファイル | 対応 |
|---------|------|
| `backtest/indicators.py` | IndicatorCalculator 再エクスポート削除 |
| `backtest/filters/*.py` | shim ファイル削除 |
| `backtest/trade_flow_analyzer.py` | SignalStepRecord 再エクスポート削除 |
| `backtest/__init__.py` | 非推奨エクスポート削除 |

---

## 実行順序

```
Phase 0 (src/ 除去)              ★最初
    ↓
Phase 1 (SignalStepRecord)     ─┐
Phase 2 (IndicatorCalculator)  ─┤ 全て独立・並列可能
Phase 3 (Filters)              ─┼─→ Phase 6 (shim削除)
Phase 4 (OptimizedGenerator)   ─┤
Phase 5 (Config factory)       ─┘
```

## リスク評価

| Phase | リスク | 影響範囲 |
|-------|--------|---------|
| 0 | 低 | pyproject.toml + ファイル移動のみ、importは不変 |
| 1 | 極低 | 純粋なデータクラス移動、条件付きimport |
| 2 | 低 | live/engine.py のimport変更、shim で安全 |
| 3 | 低 | フィルタは外部から未使用、内部再編のみ |
| 4 | 低 | ユーザー未使用コードの削除 |
| 5 | 中 | Config構造追加、既存動作に影響なし |
| 6 | 低 | 非推奨shim の除去のみ |
