# バックテスト実行ルール

## 必須: キューマネージャー経由で実行

バックテストは **常に** `backtest_queue_runner.py` 経由で実行する。
`run_backtest.py` を直接実行してはいけない。

### キューマネージャーの使い方

1. キューファイル (`D:\Projects\AutoTraderV4_data\backtest_queue.json`) にジョブを追加:

```json
{
  "jobs": [
    {
      "id": "usdjpy-2023-verify",
      "symbol": "USDJPY",
      "years": [2023],
      "timeframes": ["M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_year_workers": 1
    }
  ]
}
```

2. キューランナーは常駐プロセスとして別ターミナルで起動済み:

```bash
uv run python scripts/backtest_queue_runner.py --cpu-threads 12
```

3. 対話コマンド: `stop`, `pause`, `resume`, `status`, `cpu N`, `quit`

### worktree内での検証バックテスト

ジョブの `code_dir` フィールドでworktreeパスを指定する:

```json
{
  "jobs": [
    {
      "id": "verify-refactor",
      "symbol": "USDJPY",
      "years": [2023],
      "code_dir": "D:/Projects/AutoTraderV4/.claude/worktrees/my-branch"
    }
  ]
}
```

### 禁止事項
- `run_backtest.py` の直接実行
- `python -m autotrader.backtest` 等の直接実行

### 例外
- テスト（pytest）内でのバックテスト実行は許可

## データディレクトリ
- データパス: `D:\Projects\AutoTraderV4_data\data\`（`get_data_dir()` で自動検出）
- `--data-dir` は省略する（自動検出に任せる）
- worktree 内の `data/` は存在しない（git管理外）
