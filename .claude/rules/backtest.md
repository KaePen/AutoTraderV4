# バックテスト実行ルール（厳守）

## 実行方法の分類

### A. パラメータ変更のみ（コード変更なし）

**必ず** `backtest_queue_runner.py` 経由で実行する。直接実行は禁止。

### B. コード変更あり

1. worktree でコード変更
2. **1回のみ** 直接テスト実行を許可（動作確認用）
3. コミット → PR 作成
4. `gh pr merge` でマージ完了を確認
5. 以降の検証は全て **キューランナー経由**

## キュー形式

キューファイル: `D:\Projects\AutoTraderV4_data\backtest_queue.json`

### シングルペアジョブ

```json
{
  "jobs": [
    {
      "id": "QI4-T1-USDJPY",
      "symbol": "USDJPY",
      "years": "2020-2025",
      "description": "B1ボリュームフィルタ検証",
      "overrides": {
        "bot": {
          "volume_filter_enabled": true,
          "volume_filter_threshold": 1.0,
          "volume_filter_penalty": 0.3
        },
        "pm": {
          "trailing_stage2_enabled": false
        }
      }
    }
  ]
}
```

### フィールド一覧

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `id` | `str` | ○ | ジョブID（結果ファイル名に使用） |
| `symbol` | `str` | △ | 通貨ペア（シングルジョブ時。デフォルト: `"USDJPY"`） |
| `years` | `str` | △ | `"YYYY"` or `"YYYY-YYYY"` 形式（デフォルト: `"2023-2025"`） |
| `description` | `str` | - | ジョブ説明 |
| `type` | `str` | - | `"single"` / `"multi_pair"`（デフォルト: `"single"`） |
| `symbols` | `list[str]` | △ | マルチペアジョブ時の通貨ペアリスト |
| `overrides` | `dict` | - | パラメータ上書き（下記参照） |
| `multi_pair_config` | `dict` | △ | マルチペア設定（下記参照） |
| `code_dir` | `str` | - | worktreeパス指定時に使用 |

### overrides の構造

3つのサブキーでパラメータを上書きする:

```json
"overrides": {
  "bot": { "UnifiedBotConfigのフィールド名": "値" },
  "pm":  { "PositionManagerConfigのフィールド名": "値" },
  "backtest": {
    "initial_balance": 1000000,
    "spread_multiplier": 1.0
  }
}
```

適用順序: プリセット値 → `get_symbol_overrides()` → `overrides.bot` で上書き。
`UnifiedBotConfig` に存在しないキーは自動除外される。

### マルチペアジョブ

```json
{
  "jobs": [
    {
      "id": "multi-6jpy-R1",
      "type": "multi_pair",
      "symbols": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY"],
      "years": "2023-2025",
      "description": "6 JPYペア統合テスト",
      "multi_pair_config": {
        "name": "6JPY-R1",
        "global_max_positions": 6,
        "per_pair_max_positions": 1,
        "global_max_exposure_lot": 10.0,
        "base_risk_pct": 0.015,
        "consensus_threshold": 9.5,
        "spread_multiplier": 1.0,
        "test_name": "R1"
      }
    }
  ]
}
```

`test_name` 指定時は `TEST_MATRIX` から `MultiPairConfig` を取得。省略時はフィールドから直接構築。

### worktree内での検証バックテスト

ジョブの `code_dir` フィールドでworktreeパスを指定する:

```json
{
  "jobs": [
    {
      "id": "verify-refactor",
      "symbol": "USDJPY",
      "years": "2023",
      "code_dir": "D:/Projects/AutoTraderV4/.claude/worktrees/my-branch"
    }
  ]
}
```

## 月並列スケジューラ

### 実行単位

- **1ヶ月 = 1CPU** のアトミック単位（`MonthTask`）
- 年×月 の全組み合わせをタスクキューに投入し、`--cpu-threads` 数だけ並列実行
- `max_year_workers` は後方互換フィールドとして残っているが**無視される**

### Precompute（事前計算）

月タスク実行前に全TF×全年のインジケータを事前計算（`PrecomputeEngine`）。
キャッシュは `.indicator_cache/` に Parquet 形式で保存される。

### チェックポイント・再開

- 月完了時に `month_results/{result_id}/{year}_{month:02d}.json` を保存
- ランナー再起動時、完了済み月をスキャンしてスキップ（途中再開可能）
- 年の全12月完了 → 年集約ファイルを即時保存
- 全年完了 → ジョブ集約結果を `backtest_results/{result_id}.json` に出力

## キューランナー

常駐プロセスとして別ターミナルで起動済み:

```bash
uv run python scripts/backtest_queue_runner.py --cpu-threads 12
```

### 対話コマンド

| コマンド | 動作 |
|---------|------|
| `stop` | 全実行中タスク停止 + `completed_ids` クリア（キュー先頭にリセット） |
| `pause` | 新規タスク取得を一時停止（実行中タスクは継続） |
| `resume` | 一時停止解除 |
| `status` | 稼働状態・CPU使用数・各ジョブ進捗（月数/パーセント）を表示 |
| `cpu N` | CPUスレッド数を動的変更 |
| `quit` | 全タスク停止してランナー終了 |

Web UI経由でも `runner_commands.json` にコマンド書き込みで同じ操作が可能。

## 結果出力

| パス | 内容 |
|------|------|
| `backtest_results/{result_id}.json` | ジョブ集約結果（全年統合） |
| `month_results/{result_id}/` | 月別チェックポイント |
| `backtest_queue_state.json` | キュー実行状態 |
| `worker_progress/` | ワーカー進捗 |

`result_id` は `"{counter:03d}_{job_id}"` 形式（例: `074_QI2-T1-USDJPY`）。

## コミットルール

- main への直接コミット禁止
- worktree + PR ベースのみ
- PR 作成後の追加コミット禁止（新 PR で対応）

## 禁止事項

- `run_backtest.py` の直接実行（B.2の動作確認を除く）
- `run_multi_pair_backtest.py` の直接実行（B.2の動作確認を除く）
- `python -m autotrader.backtest` 等の直接実行
- `years` に配列 `[2023]` を使用（文字列 `"2023"` を使うこと）

## 例外

- テスト（pytest）内でのバックテスト実行は許可

## データディレクトリ

- データパス: `D:\Projects\AutoTraderV4_data\data\`（`get_data_dir()` で自動検出）
- `--data-dir` は省略する（自動検出に任せる）
- worktree 内の `data/` は存在しない（git管理外）
