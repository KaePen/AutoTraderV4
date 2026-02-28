# AutoTraderV4 エージェントチーム ワークフロー

## 基本原則

- main ブランチへの直接コミットは禁止
- コード変更は `isolation: "worktree"` 付きエージェント経由で行う
- 変更は PR ベースでマージする（リポジトリ: `KaePen/AutoTraderV4`）

## エージェント分類

### 読み取り専用（worktree 不要）

メインディレクトリで実行可能な作業:
- バックテスト実行（`scripts/run_backtest.py`）
- コード分析・ボトルネック調査
- コードレビュー・アーキテクチャ調査
- テスト実行

### コード変更（worktree 必須）

Agent ツールで `isolation: "worktree"` を指定:

```python
Agent(
    name="implementer",
    subagent_type="general-purpose",
    isolation="worktree",
    prompt="機能Xを実装し、PRを作成...",
)
```

## チーム構成

```
1. TeamCreate でチーム作成
2. 読み取りエージェントで調査（並列実行）
3. チームリーダーが結果を集約・タスク分割
4. worktree エージェントで実装（並列実行）
5. PR レビュー・マージ → worktree 掃除
```

### タスク分割の原則

- 同じファイルを複数エージェントが触らないよう設計
- `TaskUpdate.addBlockedBy` で依存関係を明示

## PR マージ・掃除

`scripts/pr_watcher.py` が以下を全て自動処理する。エージェントによる手動掃除は不要:
- PR のマージ（コンフリクト時は Claude による自動解決）
- worktree 削除（アクティブ worktree は保護）
- ローカル・リモートブランチ削除
- `worktree prune` / `fetch --prune`
- 孤立 tmp/ ディレクトリの掃除

エージェントの責務は **PR 作成まで**。マージ以降は `pr_watcher.py` に委任する。

## tmp/ ディレクトリ

- `tmp/` は worktree 専用（`.gitignore` 登録済み）
- `pr_watcher.py` がマージ後に自動削除

## 禁止事項

- メインディレクトリ (`D:\Projects\AutoTraderV4`) でのファイル直接編集・コミット
- `git push origin main` への直接プッシュ
- `git commit --amend` による公開済みコミットの書き換え
- `--no-verify` によるフックスキップ
- `git push --force` を main/master に実行
