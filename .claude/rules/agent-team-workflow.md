# AutoTraderV4 Git Worktree ワークフロー（全セッション共通）

## ブランチ戦略

```
worktree (feat/xxx, fix/xxx)
  ↓ PR → main（pr_watcherが自動マージ）
main（開発・検証）
  ↓ /deploy-live
live（本番トレード）
```

| ブランチ | 用途 | 直接コミット | PR元 |
|---------|------|------------|------|
| worktree | セッション作業用 | ○ | → main |
| main | 開発・検証のベース | ✕ | ← worktree |
| live | リアルトレード本番 | ✕ | ← main のみ |

## 1 Session = 1 Worktree ポリシー

- セッション開始時に `EnterWorktree` で1つのworktreeを作成する
- セッション内の全変更はそのworktree内で直接行う
- サブエージェントは `isolation` なしで起動し、同じworktree内で作業する
- main / live ブランチへの直接コミットは禁止

## サブエージェントの使い方

```python
# 正しい: isolation なし（同じworktree内で作業）
Agent(name="impl", prompt="ファイルX,Yを修正...")

# 禁止: ネストworktreeが作成され、gitパスが壊れる
Agent(isolation="worktree", ...)
```

## 1機能 = 1ブランチ = 1PR

- 独立した修正はブランチを切り替えて別PRにする
- 1機能が大きい場合は同一ブランチで複数コミット → 1PR
- pr_watcherがPRを即時マージするため、マージ済みPRのブランチに追加コミットしても反映されない

### マルチPRフロー（同一セッション内で複数PR）

```
1. EnterWorktree → branch "fix/first"
2. 修正 → コミット → push → PR作成
3. PR掃除（下記参照）
4. git fetch origin main && git checkout -b fix/second origin/main
5. 修正 → コミット → push → PR作成
6. 繰り返し
```

## セッション側の掃除ルール

pr_watcherはマージのみ行い、掃除はしない。PR作成後、セッション自身が掃除する。

### PR作成後の掃除手順

```bash
# 1. PRマージ確認（pr_watcherが5秒間隔でマージ）
gh pr view <PR番号> --json state --jq '.state'  # → "MERGED"

# 2. ローカルブランチ削除（現在チェックアウト中でなければ）
git branch -D <branch>

# 3. リモートブランチ削除
git push origin --delete <branch>
```

### セッション終了時

- EnterWorktreeで入ったworktreeは ExitWorktree で抜ける
- 作業完了後のworktreeディレクトリは放置してよい（次セッションで掃除）

### 孤立worktreeの対処（前セッションの異常終了等）

次セッション開始時に確認:
```bash
git worktree list
# 不要なworktreeがあれば削除
git worktree remove <path> --force
git worktree prune
```

## エージェント分類

### 読み取り専用（worktree 不要）

メインディレクトリで実行可能な作業:
- バックテスト実行（`scripts/run_backtest.py`）
- コード分析・ボトルネック調査
- コードレビュー・アーキテクチャ調査
- テスト実行

### コード変更

worktree内で直接実行。`isolation: "worktree"` は使わない。

## チーム構成

```
1. TeamCreate でチーム作成
2. 読み取りエージェントで調査（並列実行）
3. チームリーダーが結果を集約・タスク分割
4. サブエージェントで実装（isolation なし、同じworktree内）
5. PR作成 → セッション側で掃除
```

### タスク分割の原則

- 同じファイルを複数エージェントが触らないよう設計
- `TaskUpdate.addBlockedBy` で依存関係を明示

## PR マージ

`scripts/pr_watcher.py` はマージ専用:
- PRの自動マージ（コンフリクト時はClaudeによる自動解決）
- `git worktree prune`（壊れた登録の解除）
- `git fetch --prune`（削除済みリモート参照の掃除）

掃除（ブランチ削除・worktree削除）はセッション側の責務。

## .claude/worktrees/ ディレクトリ

- `EnterWorktree` ツールが `.claude/worktrees/` 配下に worktree を作成する
- `.gitignore` 登録済み
- セッション終了時または次セッション開始時に掃除

## 禁止事項

- `isolation: "worktree"` の使用（全面禁止 — ネストworktreeの原因）
- worktree内での `EnterWorktree` 実行
- main / live への直接コミット・プッシュ
- `live` ブランチへの worktree からの直接マージ（必ず main 経由）
- `git commit --amend` による公開済みコミットの書き換え
- `--no-verify` によるフックスキップ
- `git push --force` を main/master/live に実行

## スタンドアロンセッション（エージェントチーム未使用時）

別ターミナルで Claude Code を個別起動して作業する場合も同じルールを適用。

### 作業開始手順

1. `EnterWorktree` ツールでworktreeを作成し、セッションの作業ディレクトリを切り替える
2. worktree 内でコード変更・コミット・プッシュ・PR作成を行う
3. PR作成後、セッション自身が掃除する（上記手順参照）

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
