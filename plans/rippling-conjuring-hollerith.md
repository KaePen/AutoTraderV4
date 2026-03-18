# AutoTraderV4_data ディレクトリ構造の整理

## Context

`AutoTraderV4_data` ルート直下に5つ以上のJSON状態ファイルが散乱し、
バックテスト関連の出力ディレクトリ（results, month_results, logs, worker_progress）も
フラットに並んでいて見通しが悪い。
マーケットデータ（`data/`）とバックテスト出力とランタイム状態を論理的にグループ化する。

## 現状 → 新構造

```
AutoTraderV4_data/                     AutoTraderV4_data/
├── backtest_queue.json                ├── data/              (変更なし・29GB)
├── backtest_queue_state.json          ├── backtest/          (BT出力を集約)
├── runner_state.json                  │   ├── results/       (旧 backtest_results/)
├── runner_commands.json               │   ├── month_results/ (旧 month_results/)
├── bt_webui_commands.json             │   ├── logs/          (旧 logs/)
├── live_webui_commands.json           │   └── worker_progress/
├── supervisor_state.json              └── state/             (ランタイムJSON集約)
├── supervisor_events.json                 ├── backtest_queue.json
├── data/                                  ├── backtest_queue_state.json
├── backtest_results/                      ├── runner_state.json
├── month_results/                         ├── runner_commands.json
├── logs/                                  ├── bt_webui_commands.json
└── worker_progress/                       ├── live_webui_commands.json
                                           ├── supervisor_state.json
                                           └── supervisor_events.json
```

## 実装ステップ

### Step 1: `paths.py` にパスゲッター集約

**ファイル:** `autotrader/config/paths.py`

既存の `get_data_dir()`, `get_log_dir()` に加え、全パスのゲッターを追加:

```python
_DATA_ROOT = Path("D:/Projects/AutoTraderV4_data")

def get_data_root() -> Path:
    env = os.environ.get("AUTOTRADER_DATA_ROOT")
    return Path(env) if env else _DATA_ROOT

# --- バックテスト出力 ---
def get_backtest_dir() -> Path:
    return get_data_root() / "backtest"

def get_results_dir() -> Path:
    return get_backtest_dir() / "results"

def get_month_results_dir() -> Path:
    return get_backtest_dir() / "month_results"

def get_worker_progress_dir() -> Path:
    return get_backtest_dir() / "worker_progress"

# get_log_dir() を backtest/logs に変更

# --- ランタイム状態 ---
def get_state_dir() -> Path:
    return get_data_root() / "state"

def get_queue_file() -> Path:
    return get_state_dir() / "backtest_queue.json"

def get_queue_state_file() -> Path:
    return get_state_dir() / "backtest_queue_state.json"

def get_runner_state_file() -> Path:
    return get_state_dir() / "runner_state.json"

def get_runner_cmd_file() -> Path:
    return get_state_dir() / "runner_commands.json"

def get_bt_webui_cmd_file() -> Path:
    return get_state_dir() / "bt_webui_commands.json"

def get_live_webui_cmd_file() -> Path:
    return get_state_dir() / "live_webui_commands.json"

def get_supervisor_state_file() -> Path:
    return get_state_dir() / "supervisor_state.json"

def get_supervisor_events_file() -> Path:
    return get_state_dir() / "supervisor_events.json"
```

**移行互換:** 各ゲッターは新パス → 旧パスの順でfallback:
```python
def get_results_dir() -> Path:
    new = get_backtest_dir() / "results"
    if new.exists():
        return new
    old = get_data_root() / "backtest_results"
    if old.exists():
        return old
    return new
```

### Step 2: 各コンシューマのハードコードパス置換

| ファイル | 変更内容 |
|---------|---------|
| `scripts/backtest_queue_runner.py` L74-85, L1665 | 8定数 + worker_progress → paths.pyゲッターに置換 |
| `scripts/backtest_web_ui.py` L27-33 | 6定数 → paths.pyゲッターに置換 |
| `scripts/process_supervisor.py` L54,57-58 | DATA_DIR, STATE_FILE, EVENTS_FILE → ゲッター。L103/119/132の`cmd_file`文字列はファイル名のみなので`get_state_dir() / config.cmd_file`に変更 |
| `scripts/analyze_whatif.py` L13-14 | `_RESULTS_DIR` → `get_results_dir()` |
| `scripts/run_multi_pair_backtest.py` L1084 | ハードコードworker_progress → `get_worker_progress_dir()` |
| `autotrader/web/__main__.py` L24-25 | `_DATA_ROOT` + live_webui_commands → `get_live_webui_cmd_file()` |

### Step 3: マイグレーションスクリプト

**ファイル:** `scripts/migrate_data_dirs.py` (新規・使い捨て)

- `state/` と `backtest/` ディレクトリ作成
- ルートのJSON → `state/` へ移動
- `backtest_results/` → `backtest/results/` へ移動
- `month_results/` → `backtest/month_results/` へ移動
- `logs/` → `backtest/logs/` へ移動
- `worker_progress/` → `backtest/worker_progress/` へ移動
- 同一ドライブなので `shutil.move` は即時（rename操作）
- **前提:** ランナー・WebUI・supervisorが停止していること

### Step 4: ドキュメント更新

- `.claude/rules/backtest.md` のパス記述を更新
- メモリファイル（MEMORY.md）のパス参照を更新

## 変更ファイル一覧

| ファイル | 種別 |
|---------|------|
| `autotrader/config/paths.py` | 大幅拡張（+60行） |
| `scripts/backtest_queue_runner.py` | 定数置換（~15行） |
| `scripts/backtest_web_ui.py` | 定数置換（~8行） |
| `scripts/process_supervisor.py` | 定数置換（~6行） |
| `scripts/analyze_whatif.py` | 定数置換（~3行） |
| `scripts/run_multi_pair_backtest.py` | パス置換（~3行） |
| `autotrader/web/__main__.py` | パス置換（~3行） |
| `scripts/migrate_data_dirs.py` | 新規（~60行） |
| `.claude/rules/backtest.md` | ドキュメント更新 |

## 検証方法

1. コード変更後、マイグレーション実行前: 旧パスfallbackにより既存動作が維持されることを確認
2. マイグレーションスクリプト実行
3. `uv run python scripts/backtest_queue_runner.py --cpu-threads 1` で起動確認
4. BT WebUI (`scripts/backtest_web_ui.py`) で結果一覧表示確認
5. テスト: `uv run pytest tests/ -x -q` でリグレッションなし確認
