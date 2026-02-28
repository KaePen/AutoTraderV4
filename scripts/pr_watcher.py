"""PRウォッチャー - PRを検知して自動マージし、ブランチ・worktreeを掃除する。

使い方:
    python -u scripts/pr_watcher.py

環境変数:
    PROJECT_DIR: プロジェクトディレクトリ（省略時はスクリプトの親ディレクトリ）
    CLEANUP_GRACE_MINUTES: worktree保護の猶予時間（省略時30分）
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Windowsコンソールの文字化け対策 + バッファリング無効化
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer,
        encoding="utf-8",
        errors="replace",
        line_buffering=True,
    )


def _find_gh_executable() -> str:
    """gh実行ファイルのパスを解決する。

    Returns:
        str: gh実行ファイルのパス
    """
    candidates = [
        Path("C:/Program Files/GitHub CLI/gh.exe"),
        Path("C:/Program Files (x86)/GitHub CLI/gh.exe"),
    ]
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        candidates.append(Path(local_app) / "Programs/GitHub CLI/gh.exe")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    which_cmd = ["where", "gh"] if sys.platform == "win32" else ["which", "gh"]
    result = subprocess.run(which_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip().splitlines()[0]

    return "gh"


def _find_claude_executable() -> str:
    """claude実行ファイルのパスを解決する。

    Returns:
        str: claude実行ファイルのパス
    """
    local_bin = (
        Path(os.environ.get("USERPROFILE", "")) / ".local/bin/claude.exe"
    )
    if local_bin.exists():
        return str(local_bin)

    appdata = os.environ.get("APPDATA", "")
    if appdata:
        npm_path = Path(appdata) / "npm/claude.cmd"
        if npm_path.exists():
            return str(npm_path)

    which_cmd = (
        ["where", "claude"] if sys.platform == "win32" else ["which", "claude"]
    )
    result = subprocess.run(which_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip().splitlines()[0]

    return "claude"


GH_CMD = _find_gh_executable()
CLAUDE_CMD = _find_claude_executable()

# 設定
REPO = "KaePen/AutoTraderV4"
POLL_INTERVAL_SEC = 5
MAX_PARALLEL_MERGES = 3
CLEANUP_EVERY_N_CYCLES = 10  # N周期ごとにフルクリーンアップ実行
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).parent.parent))
TMP_DIR = PROJECT_DIR / "tmp"

# 安全クリーンアップ設定
CLEANUP_GRACE_MINUTES = int(
    os.environ.get("CLEANUP_GRACE_MINUTES", "30")
)
LOCK_FILE_NAME = ".claude-active"

# merge + push は排他制御（並行マージによるpush競合を防止）
_merge_lock = threading.Lock()

CONFLICT_RESOLVE_PROMPT = """
あなたはgitのマージコンフリクトを解決する専門エージェントです。

対象PR: #{pr_number} - {pr_title}
作業ディレクトリ: {project_dir}（すでにコンフリクト状態）

以下の手順で解決してください:
1. git -C {project_dir} diff --name-only --diff-filter=U でコンフリクトファイルを確認
2. 各ファイルの <<<<<<< HEAD ... >>>>>>> を読み取り、両方の意図を汲んで解決
3. git -C {project_dir} add <解決したファイル>
4. git -C {project_dir} merge --continue --no-edit

解決できない場合は git -C {project_dir} merge --abort して処理を中断し理由を説明してください。

【重要】git checkout は絶対に使わないこと。
"""


# ─── git ヘルパー ───────────────────────────────────


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    """PROJECT_DIRでgitコマンドを実行する。

    Args:
        args: gitサブコマンドと引数のリスト

    Returns:
        subprocess.CompletedProcess: 実行結果
    """
    return subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(PROJECT_DIR),
    )


def _is_merge_in_progress() -> bool:
    """mainディレクトリがマージ中状態かどうかを確認する。

    Returns:
        bool: MERGE_HEADが存在すればTrue
    """
    return (PROJECT_DIR / ".git" / "MERGE_HEAD").exists()


# ─── 安全チェック ─────────────────────────────────────


def _has_lock_file(wt_path: str | Path) -> bool:
    """worktreeにロックファイルが存在するか確認する。

    エージェントは作業開始時に .claude-active を作成し、
    完了時に削除する規約。このファイルがある間はクリーンアップ対象外。

    Args:
        wt_path: worktreeディレクトリのパス

    Returns:
        bool: ロックファイルが存在すればTrue
    """
    return Path(wt_path, LOCK_FILE_NAME).exists()


def _is_dir_recently_modified(
    dir_path: str | Path,
    grace_minutes: int = CLEANUP_GRACE_MINUTES,
) -> bool:
    """ディレクトリが最近変更されたか確認する。

    ディレクトリ自体のmtimeと、直下のファイルのmtimeを確認し、
    いずれかがgrace_minutes以内であればTrueを返す。

    Args:
        dir_path: 確認するディレクトリのパス
        grace_minutes: 猶予時間（分）

    Returns:
        bool: 猶予時間内に変更があればTrue
    """
    p = Path(dir_path)
    if not p.exists():
        return False

    threshold = time.time() - (grace_minutes * 60)

    # ディレクトリ自体のmtime
    try:
        if p.stat().st_mtime > threshold:
            return True
    except OSError:
        return False

    # 直下のファイル・サブディレクトリのmtimeも確認
    try:
        for entry in p.iterdir():
            try:
                if entry.stat().st_mtime > threshold:
                    return True
            except OSError:
                continue
    except OSError:
        pass

    return False


def _is_worktree_active(wt_path: str | Path) -> str | None:
    """worktreeがアクティブ（使用中）かどうか判定する。

    ロックファイルまたは最近の変更があれば使用中とみなす。

    Args:
        wt_path: worktreeディレクトリのパス

    Returns:
        str | None: 使用中の理由（None=使用中でない）
    """
    if _has_lock_file(wt_path):
        return "ロックファイル検出"
    if _is_dir_recently_modified(wt_path):
        return f"最近変更あり（{CLEANUP_GRACE_MINUTES}分以内）"
    return None


# ─── worktree / ブランチ掃除 ────────────────────────


def _get_worktree_for_branch(branch: str) -> str | None:
    """ブランチに紐づくworktreeパスを取得する。

    Args:
        branch: ブランチ名

    Returns:
        str | None: worktreeパス（メインworktreeは除外）
    """
    r = _git(["worktree", "list", "--porcelain"])
    if r.returncode != 0:
        return None

    current_path: str | None = None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree "):]
        elif line.startswith("branch refs/heads/"):
            wt_branch = line[len("branch refs/heads/"):]
            if (
                wt_branch == branch
                and current_path
                and Path(current_path).resolve() != PROJECT_DIR.resolve()
            ):
                return current_path
    return None


def _remove_worktree(
    branch: str,
    *,
    respect_activity: bool = True,
) -> bool:
    """ブランチに紐づくworktreeを削除する。

    Args:
        branch: ブランチ名
        respect_activity: Trueの場合、アクティブなworktreeは削除しない

    Returns:
        bool: 削除した場合True
    """
    wt_path = _get_worktree_for_branch(branch)
    if not wt_path:
        return False

    # 安全チェック: アクティブなworktreeは削除しない
    if respect_activity:
        reason = _is_worktree_active(wt_path)
        if reason:
            print(
                f"[SKIP] worktree保護（{reason}）: {wt_path}",
                flush=True,
            )
            return False

    r = _git(["worktree", "remove", "--force", wt_path])
    if r.returncode == 0:
        print(f"[INFO] worktree削除: {wt_path}", flush=True)
        return True

    print(
        f"[WARN] worktree削除失敗: {wt_path}: {r.stderr.strip()}",
        flush=True,
    )
    return False


def _delete_branch(
    branch: str,
    *,
    force: bool = False,
    respect_activity: bool = True,
) -> bool:
    """ローカルブランチを削除する（worktreeがあれば先に除去）。

    worktree削除に失敗した場合はブランチ削除もスキップする。

    Args:
        branch: ブランチ名
        force: -D（強制）を使うかどうか
        respect_activity: Trueの場合、アクティブなworktreeは削除しない

    Returns:
        bool: 削除した場合True
    """
    wt_path = _get_worktree_for_branch(branch)
    if wt_path:
        removed = _remove_worktree(
            branch, respect_activity=respect_activity
        )
        if not removed:
            print(
                f"[WARN] worktree除去失敗のためブランチ削除スキップ: {branch}",
                flush=True,
            )
            return False
    flag = "-D" if force else "-d"
    r = _git(["branch", flag, branch])
    if r.returncode == 0:
        return True
    if "not found" not in r.stderr:
        print(
            f"[WARN] ブランチ削除失敗 ({branch}): {r.stderr.strip()}",
            flush=True,
        )
    return False


def _cleanup_orphan_tmp_dirs() -> int:
    """tmp/配下の孤立ディレクトリを削除する。

    有効なworktreeに紐づかないディレクトリを削除。
    ただしアクティブなディレクトリは保護する。

    Returns:
        int: 削除したディレクトリ数
    """
    if not TMP_DIR.exists():
        return 0

    # 有効なworktreeパスを収集
    valid_paths: set[str] = set()
    r = _git(["worktree", "list", "--porcelain"])
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if line.startswith("worktree "):
                resolved = str(Path(line[len("worktree "):]).resolve())
                valid_paths.add(resolved)

    removed = 0
    for entry in TMP_DIR.iterdir():
        if not entry.is_dir():
            continue
        if str(entry.resolve()) in valid_paths:
            continue

        # 安全チェック: アクティブなディレクトリは保護
        reason = _is_worktree_active(entry)
        if reason:
            print(
                f"[SKIP] ディレクトリ保護（{reason}）: {entry.name}",
                flush=True,
            )
            continue

        try:
            shutil.rmtree(entry)
            print(
                f"[INFO] 孤立ディレクトリ削除: {entry.name}",
                flush=True,
            )
            removed += 1
        except OSError as e:
            print(
                f"[WARN] ディレクトリ削除失敗: {entry.name}: {e}",
                flush=True,
            )
    return removed


def cleanup_stale() -> None:
    """ゴミworktree・ブランチ・リモート参照を一掃する。

    アクティブなworktree（ロックファイルまたは最近の変更あり）は保護する。

    実行内容:
    1. git worktree prune（壊れた登録を解除）
    2. git fetch --prune（削除済みリモート参照を掃除）
    3. マージ済みローカルブランチを削除（アクティブworktree保護）
    4. 孤立ローカルブランチ（リモート無し＆worktree無し）を削除
    5. マージ済みリモートブランチを削除
    6. tmp/配下の孤立ディレクトリを削除（アクティブ保護）
    """
    print("[INFO] クリーンアップ開始...", flush=True)
    cleaned = 0

    # 1. worktree prune
    _git(["worktree", "prune"])

    # 2. fetch --prune
    r = _git(["fetch", "--prune", "origin"])
    if r.returncode == 0 and r.stderr:
        pruned_lines = [
            ln for ln in r.stderr.splitlines() if "[deleted]" in ln
        ]
        if pruned_lines:
            print(
                f"[INFO] staleリモート参照 {len(pruned_lines)}件を削除",
                flush=True,
            )
            cleaned += len(pruned_lines)

    # 3. マージ済みローカルブランチを削除（アクティブworktree保護）
    r = _git(["branch", "--merged", "main"])
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            branch = line.strip()
            if branch.startswith("* "):
                branch = branch[2:]
            if not branch or branch == "main":
                continue
            if _delete_branch(branch, respect_activity=True):
                print(
                    f"[INFO] マージ済みブランチ削除: {branch}",
                    flush=True,
                )
                cleaned += 1

    # 4. 孤立ローカルブランチ（リモート無し＆worktree無し）
    r = _git(["branch"])
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            branch = line.strip()
            if branch.startswith("* "):
                branch = branch[2:]
            if not branch or branch == "main":
                continue
            # リモートに存在するか確認
            rr = _git(
                [
                    "rev-parse",
                    "--verify",
                    f"refs/remotes/origin/{branch}",
                ]
            )
            if rr.returncode != 0:
                # worktreeがアクティブなら残す
                wt = _get_worktree_for_branch(branch)
                if wt:
                    reason = _is_worktree_active(wt)
                    if reason:
                        print(
                            f"[SKIP] 孤立ブランチ保護"
                            f"（{reason}）: {branch}",
                            flush=True,
                        )
                        continue
                    # worktreeあるがアクティブでない → 削除可能
                    print(
                        f"[WARN] 孤立ブランチ"
                        f"（非アクティブworktree）: {branch}",
                        flush=True,
                    )
                if _delete_branch(
                    branch,
                    force=True,
                    respect_activity=True,
                ):
                    print(
                        f"[INFO] 孤立ブランチ削除: {branch}",
                        flush=True,
                    )
                    cleaned += 1

    # 5. マージ済みリモートブランチを削除
    r = _git(["branch", "-r", "--merged", "main"])
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            ref = line.strip()
            if not ref.startswith("origin/"):
                continue
            remote_branch = ref[len("origin/"):]
            if remote_branch in ("main", "HEAD"):
                continue
            dr = _git(
                [
                    "push",
                    "origin",
                    "--delete",
                    remote_branch,
                ]
            )
            if dr.returncode == 0:
                print(
                    f"[INFO] マージ済みリモート削除: {remote_branch}",
                    flush=True,
                )
                cleaned += 1

    # 6. tmp/配下の孤立ディレクトリを削除（アクティブ保護）
    cleaned += _cleanup_orphan_tmp_dirs()

    status = f"{cleaned}件処理" if cleaned > 0 else "不要なゴミなし"
    print(f"[INFO] クリーンアップ完了: {status}", flush=True)


# ─── コンフリクト解決 ──────────────────────────────


def _resolve_conflict_with_claude(
    pr_number: int,
    pr_title: str,
) -> bool:
    """Claudeエージェントにコンフリクト解決を委譲する。

    PROJECT_DIRはすでにコンフリクト状態であることが前提。
    Claudeが merge --continue を完了させた場合にTrueを返す。

    Args:
        pr_number: PR番号
        pr_title: PRタイトル

    Returns:
        bool: 解決成功ならTrue
    """
    prompt = CONFLICT_RESOLVE_PROMPT.format(
        pr_number=pr_number,
        pr_title=pr_title,
        project_dir=str(PROJECT_DIR),
    )

    print(
        f"[INFO] PR #{pr_number} コンフリクト検出 - Claudeに解決を委譲",
        flush=True,
    )

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    try:
        result = subprocess.run(
            [
                CLAUDE_CMD,
                "-p",
                prompt,
                "--allowedTools",
                "Bash,Read,Edit,Write,Glob,Grep",
            ],
            env=env,
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,  # 10分上限
        )
    except subprocess.TimeoutExpired:
        print(
            f"[ERROR] PR #{pr_number} コンフリクト解決タイムアウト"
            "（10分超過）",
            flush=True,
        )
        return False

    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", flush=True)

    resolved = not _is_merge_in_progress()
    status = "解決成功" if resolved else "解決失敗"
    print(
        f"[INFO] PR #{pr_number} コンフリクト{status}",
        flush=True,
    )
    return resolved


# ─── PR取得・マージ ─────────────────────────────────


def get_open_prs() -> list[dict[str, object]]:
    """GitHub APIでオープンなPR一覧を取得する。

    Returns:
        list[dict[str, object]]: PR情報のリスト
    """
    result = subprocess.run(
        [
            GH_CMD,
            "pr",
            "list",
            "--repo",
            REPO,
            "--state",
            "open",
            "--base",
            "main",
            "--json",
            "number,title,headRefName",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(
            f"[ERROR] gh pr list 失敗: {result.stderr}",
            flush=True,
        )
        return []
    return json.loads(result.stdout)


def auto_merge_pr(pr: dict[str, object]) -> None:
    """PRを差分確認してmainにマージする。

    マージ後のローカルクリーンアップはアクティブチェック付きで実行。
    worktreeがアクティブ（エージェント使用中）の場合はリモートブランチのみ
    削除し、ローカルの掃除は次回の cleanup_stale() に委譲する。

    手順:
    1. fetch → 差分ログ出力
    2. merge + push（排他制御）
    3. リモートブランチ削除
    4. ローカルクリーンアップ（アクティブworktree保護）
    5. worktree prune

    Args:
        pr: PR情報辞書（number, title, headRefName）
    """
    num = pr["number"]
    branch = pr["headRefName"]
    title = pr["title"]

    print(f"[INFO] PR #{num} 処理開始: {title}", flush=True)

    # 1. リモートブランチをfetch
    r = _git(["fetch", "origin", branch])
    if r.returncode != 0:
        print(
            f"[ERROR] PR #{num} fetch失敗: {r.stderr}",
            flush=True,
        )
        return

    # 2. 差分サマリをログ出力（確認用）
    diff = _git(
        [
            "diff",
            "--stat",
            f"origin/main...origin/{branch}",
        ]
    )
    if diff.stdout.strip():
        print(
            f"[INFO] PR #{num} 差分:\n{diff.stdout}",
            flush=True,
        )

    # 3. merge + push（競合防止のため排他制御）
    with _merge_lock:
        # mainを最新化してからマージ
        r = _git(["pull", "--ff-only", "origin", "main"])
        if r.returncode != 0:
            print(
                f"[ERROR] PR #{num} pull失敗: {r.stderr}",
                flush=True,
            )
            return

        r = _git(
            [
                "merge",
                "--no-ff",
                f"origin/{branch}",
                "-m",
                f"Merge PR #{num}: {title}",
            ]
        )

        if r.returncode != 0:
            is_conflict = (
                "CONFLICT" in r.stdout
                or "Automatic merge failed" in r.stderr
                or _is_merge_in_progress()
            )
            if not is_conflict:
                print(
                    f"[ERROR] PR #{num} merge失敗: {r.stderr}",
                    flush=True,
                )
                _git(["merge", "--abort"])
                return

            resolved = _resolve_conflict_with_claude(num, title)
            if not resolved:
                _git(["merge", "--abort"])
                return

        r = _git(["push", "origin", "main"])
        if r.returncode != 0:
            print(
                f"[ERROR] PR #{num} push失敗: {r.stderr}",
                flush=True,
            )
            # マージコミットをロールバック（次のPRのpullが失敗するのを防止）
            _git(["reset", "--hard", "HEAD~1"])
            return

    # 4. リモートブランチ削除
    r = _git(["push", "origin", "--delete", branch])
    if r.returncode != 0:
        print(
            f"[WARN] PR #{num} リモートブランチ削除失敗: {r.stderr.strip()}",
            flush=True,
        )

    # 5. ローカルクリーンアップ（アクティブworktree保護）
    # エージェントが使用中の場合はスキップし、cleanup_stale()に委譲
    wt_path = _get_worktree_for_branch(branch)
    if wt_path:
        reason = _is_worktree_active(wt_path)
        if reason:
            print(
                f"[SKIP] PR #{num} ローカル掃除延期"
                f"（{reason}）: {wt_path}",
                flush=True,
            )
        else:
            _delete_branch(
                branch, force=True, respect_activity=False
            )
    else:
        # worktreeなし → ブランチだけ削除
        _delete_branch(
            branch, force=True, respect_activity=False
        )

    # 6. worktree prune（壊れた登録があれば解消）
    _git(["worktree", "prune"])

    print(f"[INFO] PR #{num} マージ完了: {title}", flush=True)


# ─── メインループ ──────────────────────────────────


def main() -> None:
    """メインループ - 定期的にPRを確認して並行マージする。"""
    print(
        f"[INFO] PRウォッチャー起動 (間隔: {POLL_INTERVAL_SEC}秒)",
        flush=True,
    )
    print(f"[INFO] 対象リポジトリ: {REPO}", flush=True)
    print(
        f"[INFO] 最大並行処理数: {MAX_PARALLEL_MERGES}",
        flush=True,
    )
    print(
        f"[INFO] クリーンアップ猶予: {CLEANUP_GRACE_MINUTES}分",
        flush=True,
    )
    print(f"[INFO] gh: {GH_CMD}", flush=True)
    print(
        "[INFO] 停止するには Ctrl+C を押してください",
        flush=True,
    )

    # 起動時クリーンアップ（排他制御下で実行）
    with _merge_lock:
        cleanup_stale()

    # セッション中のみ処理済みを記憶（再起動時はリセット）
    processed: set[int] = set()
    cycle_count = 0

    def _on_future_done(
        future: object,
        pr_num: int,
    ) -> None:
        """ワーカーの未処理例外をログ出力する。"""
        from concurrent.futures import Future

        if isinstance(future, Future):
            exc = future.exception()
            if exc:
                print(
                    f"[ERROR] PR #{pr_num} 未処理例外: {exc}",
                    flush=True,
                )

    with ThreadPoolExecutor(
        max_workers=MAX_PARALLEL_MERGES,
    ) as executor:
        try:
            while True:
                prs = get_open_prs()
                new_prs = [pr for pr in prs if pr["number"] not in processed]

                cycle_count += 1

                if not new_prs:
                    # PRがないタイミングで定期クリーンアップ
                    if cycle_count % CLEANUP_EVERY_N_CYCLES == 0:
                        with _merge_lock:
                            cleanup_stale()
                    else:
                        print(
                            f"[INFO] 未処理PRなし"
                            f" - {POLL_INTERVAL_SEC}秒後"
                            "に再確認",
                            flush=True,
                        )
                else:
                    for pr in new_prs:
                        pr_num = pr["number"]
                        processed.add(pr_num)
                        fut = executor.submit(auto_merge_pr, pr)
                        fut.add_done_callback(
                            lambda f, n=pr_num: _on_future_done(f, n)
                        )
                        print(
                            f"[INFO] PR #{pr_num}"
                            f" をキューに追加: {pr['title']}",
                            flush=True,
                        )

                time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            print(
                "\n[INFO] PRウォッチャーを停止中"
                " (実行中のマージは完了を待機)...",
                flush=True,
            )


if __name__ == "__main__":
    main()
