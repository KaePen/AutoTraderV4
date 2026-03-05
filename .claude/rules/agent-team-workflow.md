# AutoTraderV4 Git Worktree ワークフロー（全セッション共通）

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
- 孤立 `.claude/worktrees/` ディレクトリの掃除

エージェントの責務は **PR 作成まで**。マージ以降は `pr_watcher.py` に委任する。

## .claude/worktrees/ ディレクトリ

- `EnterWorktree` ツールは `.claude/worktrees/` 配下に worktree を作成する
- `.gitignore` 登録済み
- `pr_watcher.py` がマージ後に自動削除

## 禁止事項

- メインディレクトリ (`D:\Projects\AutoTraderV4`) でのファイル直接編集・コミット
- `git push origin main` への直接プッシュ
- `git commit --amend` による公開済みコミットの書き換え
- `--no-verify` によるフックスキップ
- `git push --force` を main/master に実行

## スタンドアロンセッション（エージェントチーム未使用時）

別ターミナルで Claude Code を個別起動して作業する場合も、worktree ルールは同様に適用される。

### 作業開始手順

1. `EnterWorktree` ツールでworktreeを作成し、セッションの作業ディレクトリを切り替える
2. worktree 内でコード変更・コミット・プッシュ・PR作成を行う
3. PR 作成後、`pr_watcher.py` がマージ・掃除を自動処理

### 手動 worktree 作成（EnterWorktree が使えない場合）

```bash
BRANCH="feat/xxx"
WORKTREE="/d/Projects/AutoTraderV4/.claude/worktrees/${BRANCH//\//_}"
git -C /d/Projects/AutoTraderV4 pull origin main
git -C /d/Projects/AutoTraderV4 worktree add "$WORKTREE" -b "$BRANCH"
# 以降は $WORKTREE 配下のファイルのみ編集する
```

### ルール適用の仕組み

- `~/.claude/rules/*.md`（グローバル）と `.claude/rules/*.md`（プロジェクト）は全セッションで自動読み込み
- worktree 内にも `.claude/rules/` がコピーされるため、ルールはworktree内でも有効
