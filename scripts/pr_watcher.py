"""PRウォッチャー - 新規PRを検知して差分確認後に自動マージする。

使い方:
    python -u scripts/pr_watcher.py

環境変数:
    PROJECT_DIR: プロジェクトディレクトリ（省略時はスクリプトの親ディレクトリ）
"""

from __future__ import annotations

import io
import json
import os
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
POLL_INTERVAL_SEC = 30
MAX_PARALLEL_MERGES = 3  # 同時処理するPR数の上限
PROJECT_DIR = Path(
    os.environ.get("PROJECT_DIR", Path(__file__).parent.parent)
)

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


def _git(args: list[str]) -> subprocess.CompletedProcess:
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

    result = subprocess.run(
        [
            CLAUDE_CMD,
            "-p", prompt,
            "--allowedTools", "Bash,Read,Edit,Write,Glob,Grep",
        ],
        env=env,
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", flush=True)

    # MERGE_HEADが消えていれば merge --continue が成功している
    resolved = not _is_merge_in_progress()
    status = "解決成功" if resolved else "解決失敗"
    print(f"[INFO] PR #{pr_number} コンフリクト{status}", flush=True)
    return resolved


def get_open_prs() -> list[dict]:
    """GitHub APIでオープンなPR一覧を取得する。

    Returns:
        list[dict]: PR情報のリスト（number, title, headRefName）
    """
    result = subprocess.run(
        [
            GH_CMD, "pr", "list",
            "--repo", REPO,
            "--state", "open",
            "--base", "main",
            "--json", "number,title,headRefName",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"[ERROR] gh pr list 失敗: {result.stderr}", flush=True)
        return []
    return json.loads(result.stdout)


def auto_merge_pr(pr: dict) -> None:
    """PRを差分確認してmainにマージする。

    1. fetch → 差分ログ出力 → merge + push（排他）→ リモートブランチ削除

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
        print(f"[ERROR] PR #{num} fetch失敗: {r.stderr}", flush=True)
        return

    # 2. 差分サマリをログ出力（確認用）
    diff = _git(["diff", "--stat", f"origin/main...origin/{branch}"])
    if diff.stdout.strip():
        print(f"[INFO] PR #{num} 差分:\n{diff.stdout}", flush=True)

    # 3. merge + push（競合防止のため排他制御）
    with _merge_lock:
        # mainを最新化してからマージ
        r = _git(["pull", "--ff-only", "origin", "main"])
        if r.returncode != 0:
            print(f"[ERROR] PR #{num} pull失敗: {r.stderr}", flush=True)
            return

        r = _git([
            "merge", "--no-ff", f"origin/{branch}",
            "-m", f"Merge PR #{num}: {title}",
        ])

        if r.returncode != 0:
            is_conflict = (
                "CONFLICT" in r.stdout
                or "Automatic merge failed" in r.stderr
                or _is_merge_in_progress()
            )
            if not is_conflict:
                print(f"[ERROR] PR #{num} merge失敗: {r.stderr}", flush=True)
                _git(["merge", "--abort"])
                return

            # コンフリクト → Claudeに解決委譲（ロックは保持したまま）
            resolved = _resolve_conflict_with_claude(num, title)
            if not resolved:
                _git(["merge", "--abort"])
                return

        r = _git(["push", "origin", "main"])
        if r.returncode != 0:
            print(f"[ERROR] PR #{num} push失敗: {r.stderr}", flush=True)
            return

    # 4. リモートブランチ削除
    r = _git(["push", "origin", "--delete", branch])
    if r.returncode != 0:
        print(
            f"[WARN] PR #{num} リモートブランチ削除失敗: {r.stderr}",
            flush=True,
        )

    # 5. ローカルブランチ削除（存在しなければスキップ）
    # -D: worktree経由でマージしたためgitの「マージ済み」判定外→強制削除
    r = _git(["branch", "-D", branch])
    if r.returncode != 0 and "not found" not in r.stderr:
        print(
            f"[WARN] PR #{num} ローカルブランチ削除失敗: {r.stderr}",
            flush=True,
        )

    print(f"[INFO] PR #{num} マージ完了: {title}", flush=True)


def main() -> None:
    """メインループ - 定期的にPRを確認して並行マージする。"""
    print(f"[INFO] PRウォッチャー起動 (間隔: {POLL_INTERVAL_SEC}秒)", flush=True)
    print(f"[INFO] 対象リポジトリ: {REPO}", flush=True)
    print(f"[INFO] 最大並行処理数: {MAX_PARALLEL_MERGES}", flush=True)
    print(f"[INFO] gh: {GH_CMD}", flush=True)
    print("[INFO] 停止するには Ctrl+C を押してください", flush=True)

    # セッション中のみ処理済みを記憶（再起動時はリセット）
    # submitした時点で登録するため、完了前でも重複起動しない
    processed: set[int] = set()

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_MERGES) as executor:
        try:
            while True:
                prs = get_open_prs()
                new_prs = [
                    pr for pr in prs if pr["number"] not in processed
                ]

                if not new_prs:
                    print(
                        f"[INFO] 未処理PRなし - {POLL_INTERVAL_SEC}秒後に再確認",
                        flush=True,
                    )
                else:
                    for pr in new_prs:
                        processed.add(pr["number"])
                        executor.submit(auto_merge_pr, pr)
                        print(
                            f"[INFO] PR #{pr['number']} をキューに追加: "
                            f"{pr['title']}",
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
