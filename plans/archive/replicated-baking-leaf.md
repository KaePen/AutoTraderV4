# TickEntryOptimizer自動有効化 [完了]

## Context

ティックエントリー最適化は実装済みだが、`TickEntryConfig.enabled` のデフォルトが `False` のため、
明示的に `TickEntryConfig(enabled=True)` を渡さない限り利用されない。

ユーザーの意図: **SCALPINGモード時に自動的に有効化されるべき**。手動で設定する必要はない。

## 実施済み変更（3ファイル、各1行）

1. `src/autotrader/live/tick_entry_config.py:34` — `enabled: bool = False` → `True`
2. `tests/unit/live/test_tick_entry_optimizer.py:524` — アサーション `False` → `True`
3. `tests/unit/live/test_engine_tick_optimizer.py:132` — アサーション `False` → `True`

## 検証結果

- 全34テストPASS（`test_tick_entry_optimizer.py` + `test_engine_tick_optimizer.py`）
- バックテストへの影響: **なし**（`src/autotrader/backtest/` にTickEntry参照ゼロ）
- TickEntryOptimizerはライブ取引専用（`LiveTradingEngine._tick()` 内のみ動作）
