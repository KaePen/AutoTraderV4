# Plan: Worktree運用の堅牢化

## Context

現在の worktree ワークフローで以下の課題が発生している:

1. **VSCodeのブランチが勝手に移動する**: `gh pr merge --delete-branch` でリモートブランチが消えた際に、VSCode（メインディレクトリで開いている）のブランチ表示が影響を受ける
2. **マージ忘れ**: PR作成後にマージせずセッションが終了する場合がある
3. **不要worktree削除漏れ**: セッション異常終了時にworktreeディレクトリが残り続ける

### 現状の仕組み

- Stop hook: `fix-worktree-transcript.js`（トランスクリプト修復のみ、git操作なし）
- セッション終了時のworktree掃除: 手動（次セッション開始時に `git worktree list` → 手動削除）
- マージ確認: ルールに記載あるが強制なし
- 現在残っている孤立ディレクトリ: `.claude/worktrees/` 配下に4件（git未登録の空ディレクトリ）

## 修正内容

### 1. Stop hookでworktree掃除スクリプト追加

**新規ファイル**: `~/.claude/cleanup-worktrees.sh`

セッション終了時に自動実行するスクリプト:

```bash
#!/bin/bash
# セッション終了時のworktree掃除
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -z "$PROJECT_ROOT" ] && exit 0

WORKTREE_DIR="$PROJECT_ROOT/.claude/worktrees"
[ -d "$WORKTREE_DIR" ] || exit 0

# 1. gitに登録済みのworktreeを正式に削除
git worktree list --porcelain | grep "^worktree " | \
  grep ".claude/worktrees" | sed 's/^worktree //' | \
  while read -r wt; do
    git worktree remove "$wt" --force 2>/dev/null
  done
git worktree prune 2>/dev/null

# 2. git未登録の孤立ディレクトリを削除
find "$WORKTREE_DIR" -mindepth 1 -maxdepth 2 -type d -empty -delete 2>/dev/null
# plans/ だけ残ったディレクトリも削除
find "$WORKTREE_DIR" -mindepth 1 -maxdepth 1 -type d | while read -r d; do
  [ -z "$(ls -A "$d" 2>/dev/null)" ] || \
  [ "$(ls "$d" 2>/dev/null)" = "plans" ] && rm -rf "$d"
done
```

**設定変更**: `~/.claude/settings.json` の Stop フックに追加

```json
"Stop": [
  {
    "hooks": [
      { "type": "command", "command": "node \"C:/Users/yamas/.claude/fix-worktree-transcript.js\"", "timeout": 10 },
      { "type": "command", "command": "bash \"C:/Users/yamas/.claude/cleanup-worktrees.sh\"", "timeout": 15 }
    ]
  }
]
```

### 2. セッション開始時の孤立worktree掃除をルールに明記

**対象ファイル**: `~/.claude/rules/agent-team-workflow.md` + `.claude/rules/agent-team-workflow.md`

現行の「次セッション開始時に確認」セクションを強化:

```markdown
### セッション開始時の掃除（必須）

worktree作成前に必ず実行:
```bash
# git登録済みworktreeの掃除
git worktree list
# main以外があれば削除
git worktree remove <path> --force
git worktree prune

# 孤立ディレクトリの掃除
rm -rf .claude/worktrees/*/

# マージされていないPRの確認
gh pr list --author @me --state open
```
```

### 3. VSCodeブランチ移動問題への対策

**原因**: VSCodeがメインディレクトリ（`D:/Projects/AutoTraderV4`）を開いている状態で、worktree内から `gh pr merge --delete-branch` を実行すると、リモートブランチ削除後のfetch時にmainのHEADが更新される。VSCodeのGit拡張がこれを検知してブランチ表示を更新する。

**対策**: ルールに注記追加。これはVSCodeのGit拡張の正常な動作であり、mainのHEADが最新に更新されるだけで**実害はない**。ただし混乱を避けるため:

- VSCodeではmainブランチを開いた状態を維持する
- worktree作業はClaude Codeのターミナルで完結させる
- `gh pr merge` 後に `git fetch origin main` でmainを最新化する（これは既にルールに記載済み）

### 4. 現在の孤立リソースの即時掃除

実装時に以下を実行:

```bash
# 孤立ディレクトリ削除
rm -rf .claude/worktrees/feat/
rm -rf .claude/worktrees/fix/
rm -rf .claude/worktrees/fix-display-bugs/
rm -rf .claude/worktrees/refactor/

# 孤立リモートブランチ削除
git push origin --delete worktree-fix/precompute-serialize 2>/dev/null
git fetch --prune origin
```

## 対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `~/.claude/cleanup-worktrees.sh` | 新規作成（Stop hook用掃除スクリプト） |
| `~/.claude/settings.json` | Stop hookにスクリプト追加 |
| `~/.claude/rules/agent-team-workflow.md` | セッション開始時掃除の強化 + VSCode注記 |
| `.claude/rules/agent-team-workflow.md` | 同上（プロジェクト版） |

## 検証方法

1. Stop hookが正常に実行されることを確認（セッション終了時のログ）
2. 孤立ディレクトリが掃除されることを確認（`ls .claude/worktrees/`）
3. `git worktree list` でmain以外が残っていないことを確認
