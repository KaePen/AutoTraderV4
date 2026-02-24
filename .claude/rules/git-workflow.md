# AutoTraderV4 Git Worktree ワークフロー（全エージェント必須）

## 絶対ルール

**main ブランチへの直接コミットは禁止。全コード変更は worktree 経由で行う。**

このルールは全エージェント（planner, code-reviewer, build-error-resolver,
tdd-guide, architect 等）に適用される。「小さい変更だから」は例外にならない。

## worktree の作成と作業

```bash
# 1. 変数定義
BRANCH="feat/xxx"
WORKTREE="/d/Projects/AutoTraderV4/tmp/${BRANCH//\//_}"

# 2. mainを最新化してからブランチ作成
git -C /d/Projects/AutoTraderV4 pull origin main
git -C /d/Projects/AutoTraderV4 branch "$BRANCH"
git -C /d/Projects/AutoTraderV4 worktree add "$WORKTREE" "$BRANCH"

# 3. 編集は $WORKTREE/ 配下のパスで行う（メインディレクトリのファイルを触らない）

# 4. コミット・プッシュ・PR作成
git -C "$WORKTREE" add <files>
git -C "$WORKTREE" commit -m "..."
git -C "$WORKTREE" push -u origin "$BRANCH"
"C:/Program Files/GitHub CLI/gh.exe" pr create --repo KaePen/AutoTraderV4 --base main ...
```

## PR マージ後の掃除（必須）

PR をマージしたら以下を必ず実行する。`scripts/pr_watcher.py` が自動で行うが、
手動マージの場合はエージェントが責任を持って実行すること。

```bash
# 1. worktree を先に削除（ブランチ削除の前提条件）
git -C /d/Projects/AutoTraderV4 worktree remove "$WORKTREE" --force

# 2. ローカルブランチ削除
git -C /d/Projects/AutoTraderV4 branch -D "$BRANCH"

# 3. リモートブランチ削除（PR経由で自動削除されていなければ）
git -C /d/Projects/AutoTraderV4 push origin --delete "$BRANCH"

# 4. 壊れたworktree登録を解消
git -C /d/Projects/AutoTraderV4 worktree prune

# 5. staleリモート参照を掃除
git -C /d/Projects/AutoTraderV4 fetch --prune origin
```

## 禁止事項

- メインディレクトリ (`D:\Projects\AutoTraderV4`) でのファイル直接編集
- `git checkout -b` をメインディレクトリで実行
- `git push origin main` への直接プッシュ
- `git commit --amend` による公開済みコミットの書き換え
- `--no-verify` によるフックスキップ
- `git push --force` を main/master に実行
- worktree やブランチを掃除せずに作業を終了する

## tmp/ ディレクトリの扱い

- `tmp/` は worktree 専用。PR マージ後にディレクトリも削除される
- `tmp/` は `.gitignore` に登録済み。コミットされることはない
- 手動で `tmp/` を削除した場合は `git worktree prune` を実行すること
