# AutoTraderV4 Git Worktree ワークフロー（全セッション共通）

## ブランチ戦略

```
worktree (feat/xxx, fix/xxx)
  ↓ PR → セッションがマージ → main
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
- マージ済みPRのブランチに追加コミットしても反映されない（新PRで対応）

### マルチPRフロー（同一セッション内で複数PR）

```
1. EnterWorktree → branch "fix/first"
2. 修正 → コミット → push → PR作成 → マージ → 掃除
3. git fetch origin main && git checkout -b fix/second origin/main
4. 修正 → コミット → push → PR作成 → マージ → 掃除
5. 繰り返し
```

## PR作成→マージ→掃除（セッション内で完結）

セッション自身がPR作成からマージ・掃除まで全て行う。外部デーモンは不要。

### 手順

```bash
# 1. PR作成
gh pr create --title "..." --body "..."

# 2. セッション自身でマージ（squash merge）
gh pr merge <PR番号> --squash --delete-branch

# 3. ローカル同期
git fetch origin main
git checkout -b <next-branch> origin/main  # 次の作業へ
# または
git rebase origin/main  # 同一ブランチで継続する場合
```

`--delete-branch` によりリモートブランチは自動削除される。
ローカルブランチは次のブランチ切り替え時に `git branch -D <branch>` で削除。

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
5. PR作成 → マージ → 掃除（セッション内で完結）
```

### タスク分割の原則

- 同じファイルを複数エージェントが触らないよう設計
- `TaskUpdate.addBlockedBy` で依存関係を明示

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
2. worktree 内でコード変更・コミット・プッシュ・PR作成・マージ・掃除を行う

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
