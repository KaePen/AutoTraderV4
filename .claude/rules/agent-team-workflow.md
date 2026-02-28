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

## PR マージ後の掃除（必須）

`scripts/pr_watcher.py` が自動実行するが、手動マージ時は必ず実行:

```bash
git -C /d/Projects/AutoTraderV4 worktree remove <path> --force
git -C /d/Projects/AutoTraderV4 branch -D <branch>
git -C /d/Projects/AutoTraderV4 push origin --delete <branch>
git -C /d/Projects/AutoTraderV4 worktree prune
git -C /d/Projects/AutoTraderV4 fetch --prune origin
```

## tmp/ ディレクトリ

- `tmp/` は worktree 専用（`.gitignore` 登録済み）
- PR マージ後にディレクトリごと削除される

## 禁止事項

- メインディレクトリ (`D:\Projects\AutoTraderV4`) でのファイル直接編集・コミット
- `git push origin main` への直接プッシュ
- `git commit --amend` による公開済みコミットの書き換え
- `--no-verify` によるフックスキップ
- `git push --force` を main/master に実行
- worktree やブランチを掃除せずに作業を終了する
