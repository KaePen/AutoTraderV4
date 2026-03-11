# pr_watcher.py クリーンアップ改善計画

## Context

pr_watcher.py のクリーンアップ機能に複数の欠陥があり、worktree ディレクトリ・ブランチが蓄積し続けている。

**現状の問題:**
- `.claude/worktrees/` が掃除対象外（`tmp/` のみ対象）
- `git worktree remove` 失敗時にフォールバックなし → ブランチも残留
- ネストworktree（`agent-xxx/.claude/worktrees/agent-yyy`）が 3GB 蓄積
- 猶予時間 360 分が長すぎる

## 変更対象ファイル

1. `scripts/pr_watcher.py` — 全コード変更
2. `.claude/rules/agent-team-workflow.md` — ネストworktree禁止ルール追加

## 変更内容

### 1. 定数追加 + 猶予時間短縮 (L104付近)

```python
TMP_DIR = PROJECT_DIR / "tmp"
WORKTREE_DIR = PROJECT_DIR / ".claude" / "worktrees"  # 追加
CLEANUP_GRACE_MINUTES = int(os.environ.get("CLEANUP_GRACE_MINUTES", "60"))  # 360→60
```

### 2. `_remove_worktree()` に shutil.rmtree フォールバック追加 (L293-333)

`git worktree remove --force` 失敗時に `shutil.rmtree()` → `git worktree prune` で再試行。

### 3. `_delete_branch()` のworktree削除失敗時の挙動変更 (L356-370)

現状: worktree削除失敗 → ブランチ削除もスキップ
変更: アクティブ保護の場合のみスキップ。それ以外は `worktree prune` 後にブランチ削除を続行。

### 4. `_cleanup_orphan_tmp_dirs()` → `_cleanup_orphan_dirs()` に改名・拡張 (L383-430)

- `tmp/` + `.claude/worktrees/` の両方を対象化
- 有効worktreeに紐づかないディレクトリを削除
- 有効worktree内のネストworktreeも再帰チェック

### 5. `_cleanup_nested_worktrees()` 新関数追加

worktree 内の `.claude/worktrees/` を再帰的に検出・削除。空になった親ディレクトリも掃除。

### 6. `cleanup_stale()` ステップ6更新 (L566-569)

`_cleanup_orphan_tmp_dirs()` → `_cleanup_orphan_dirs()` に差し替え。

### 7. `auto_merge_pr()` にマージ後即時クリーンアップ追加 (L980付近)

マージ成功後に `_cleanup_orphan_dirs()` を呼び出し、次の定期クリーンアップを待たずに掃除。

### 8. ルール追加 (`.claude/rules/agent-team-workflow.md`)

禁止事項に追加:
- worktree 内からの `EnterWorktree` 実行（ネストworktree禁止）
- worktree 内で `isolation: "worktree"` 付きサブエージェント起動の禁止

## 検証方法

1. 構文チェック: `python -c "import ast; ast.parse(open('scripts/pr_watcher.py').read())"`
2. ruff lint: `ruff check scripts/pr_watcher.py`
3. 動作確認: pr_watcher 起動時のクリーンアップで既存の孤立worktree・ネストworktreeが削除されることを確認
4. 保護確認: アクティブなworktree（最近変更あり）が保護されることを確認
