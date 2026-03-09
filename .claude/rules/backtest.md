# バックテスト実行ルール（厳守）

## 実行方法の分類

### A. パラメータ変更のみ（コード変更なし）

**必ず** `backtest_queue_runner.py` 経由で実行する。直接実行は禁止。

### B. コード変更あり

1. worktree でコード変更
2. **1回のみ** 直接テスト実行を許可（動作確認用）
3. コミット → PR 作成
4. pr_watcher.py によるマージ完了を確認
5. 以降の検証は全て **キューランナー経由**

## キュー形式

キューファイル: `D:\Projects\AutoTraderV4_data\backtest_queue.json`

### シングルペア

```json
{
  "jobs": [
    {
      "id": "usdjpy-verify",
      "symbol": "USDJPY",
      "years": [2023],
      "timeframes": ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"],
      "max_year_workers": 1
    }
  ]
}
```

### マルチペア

```json
{
  "jobs": [
    {
      "id": "multi-pair-test",
      "type": "multi_pair",
      "name": "テスト名",
      "multi_pair_config": {
        "pairs": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY"],
        "global_max_positions": 6,
        "per_pair_max_positions": 1,
        "global_max_exposure_lot": 10.0,
        "base_risk_pct": 0.015,
        "consensus_threshold": 9.5,
        "tests": ["R1"]
      }
    }
  ]
}
```

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

## キューランナー

常駐プロセスとして別ターミナルで起動済み:

```bash
uv run python scripts/backtest_queue_runner.py --cpu-threads 12
```

対話コマンド: `stop`, `pause`, `resume`, `status`, `cpu N`, `quit`

## コミットルール

- main への直接コミット禁止
- worktree + PR ベースのみ
- PR 作成後の追加コミット禁止（新 PR で対応）

## 禁止事項

- `run_backtest.py` の直接実行（B.2の動作確認を除く）
- `run_multi_pair_backtest.py` の直接実行（B.2の動作確認を除く）
- `python -m autotrader.backtest` 等の直接実行

## 例外

- テスト（pytest）内でのバックテスト実行は許可

## データディレクトリ

- データパス: `D:\Projects\AutoTraderV4_data\data\`（`get_data_dir()` で自動検出）
- `--data-dir` は省略する（自動検出に任せる）
- worktree 内の `data/` は存在しない（git管理外）
