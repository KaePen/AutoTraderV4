"""PRウォッチャー - PRを検知して自動マージする。

掃除（ブランチ削除・worktree削除）はセッション側が責任を持つ。
pr_watcherはマージ専用。

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
POLL_INTERVAL_SEC = 5
MAX_PARALLEL_MERGES = 3
MAX_MERGE_RETRIES = 2  # 初回+リトライ2回=最大3回試行
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).parent.parent))

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

MERGE_FAIL_RESOLVE_PROMPT = """
あなたはgitのマージ問題を診断・解決する専門エージェントです。

対象PR: #{pr_number} - {pr_title}
ブランチ: {branch}
作業ディレクトリ: {project_dir}

エラー内容:
{error_detail}

以下の手順で調査・解決してください:
1. git -C {project_dir} status でリポジトリの状態を確認
2. エラーの原因を特定
3. 可能であれば解決策を実行
4. 解決できない場合はその理由を説明

【重要】
- git push --force は絶対に使わないこと
- main ブランチへの直接コミットはしないこと
- 解決できない場合は正直にその旨を報告すること
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


def _resolve_merge_failure_with_claude(
    pr_number: int,
    pr_title: str,
    branch: str,
    error_detail: str,
) -> bool:
    """Claudeエージェントにマージ失敗の診断・解決を委譲する。

    コンフリクト以外のマージ失敗（pull --ff-only失敗、
    merge失敗等）に対してClaude Codeを呼び出して解決を試みる。

    Args:
        pr_number: PR番号
        pr_title: PRタイトル
        branch: ブランチ名
        error_detail: エラーの詳細情報

    Returns:
        bool: 解決成功ならTrue
    """
    prompt = MERGE_FAIL_RESOLVE_PROMPT.format(
        pr_number=pr_number,
        pr_title=pr_title,
        branch=branch,
        project_dir=str(PROJECT_DIR),
        error_detail=error_detail,
    )

    print(
        f"[INFO] PR #{pr_number} マージ失敗 - Claudeに診断・解決を委譲",
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
            f"[ERROR] PR #{pr_number} マージ失敗解決タイムアウト（10分超過）",
            flush=True,
        )
        return False

    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", flush=True)

    # 解決成功の判定: マージ中状態が解消されていること
    if _is_merge_in_progress():
        print(
            f"[WARN] PR #{pr_number} マージ状態が残存 - merge --abort で復旧",
            flush=True,
        )
        _git(["merge", "--abort"])
        return False

    print(
        f"[INFO] PR #{pr_number} マージ失敗の診断完了",
        flush=True,
    )
    return True


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


def auto_merge_pr(
    pr: dict[str, object],
    merged: set[int] | None = None,
    fail_count: dict[int, int] | None = None,
) -> str:
    """PRをmainにマージする（マージ専用、掃除はしない）。

    手順:
    1. fetch → 差分ログ出力
    2. merge + push（排他制御）
    3. worktree prune / fetch --prune（軽量メンテナンスのみ）

    Args:
        pr: PR情報辞書（number, title, headRefName）
        merged: マージ成功したPR番号のセット（成功時に追加）
        fail_count: PR番号→失敗回数の辞書（失敗時にカウントアップ）

    Returns:
        str: "merged"=成功, "failed"=失敗（リトライ不要）,
             "retry"=失敗（リトライ可能）
    """
    num = pr["number"]
    branch = pr["headRefName"]
    title = pr["title"]

    print(f"[INFO] PR #{num} 処理開始: {title}", flush=True)

    def _inc_fail() -> str:
        """失敗カウントを増やし、リトライ可否を返す。"""
        if fail_count is not None:
            fail_count[num] = fail_count.get(num, 0) + 1
            if fail_count[num] > MAX_MERGE_RETRIES:
                print(
                    f"[FAILED] PR #{num} 最大リトライ回数"
                    f"（{MAX_MERGE_RETRIES}）超過 - スキップ",
                    flush=True,
                )
                return "failed"
        return "retry"

    def _is_network_error(stderr: str) -> bool:
        """ネットワーク系エラーかどうかを判定する。"""
        indicators = [
            "Could not resolve host",
            "unable to access",
            "Connection refused",
            "Connection timed out",
            "SSL",
            "fatal: unable to connect",
        ]
        return any(ind in stderr for ind in indicators)

    # 1. リモートブランチをfetch
    r = _git(["fetch", "origin", branch])
    if r.returncode != 0:
        print(
            f"[ERROR] PR #{num} fetch失敗: {r.stderr}",
            flush=True,
        )
        return _inc_fail()

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
            err = r.stderr.strip()
            print(
                f"[ERROR] PR #{num} pull失敗: {err}",
                flush=True,
            )
            if _is_network_error(err):
                return _inc_fail()
            resolved = _resolve_merge_failure_with_claude(
                num,
                title,
                branch,
                f"git pull --ff-only origin main 失敗:\n{err}",
            )
            if not resolved:
                return _inc_fail()

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
            if is_conflict:
                resolved = _resolve_conflict_with_claude(
                    num,
                    title,
                )
                if not resolved:
                    _git(["merge", "--abort"])
                    return _inc_fail()
            else:
                err = r.stderr.strip()
                print(
                    f"[ERROR] PR #{num} merge失敗: {err}",
                    flush=True,
                )
                _git(["merge", "--abort"])
                resolved = _resolve_merge_failure_with_claude(
                    num,
                    title,
                    branch,
                    f"git merge --no-ff 失敗:\n{err}",
                )
                if not resolved:
                    return _inc_fail()
                r2 = _git(
                    [
                        "merge",
                        "--no-ff",
                        f"origin/{branch}",
                        "-m",
                        f"Merge PR #{num}: {title}",
                    ]
                )
                if r2.returncode != 0:
                    _git(["merge", "--abort"])
                    return _inc_fail()

        r = _git(["push", "origin", "main"])
        if r.returncode != 0:
            err = r.stderr.strip()
            print(
                f"[ERROR] PR #{num} push失敗: {err}",
                flush=True,
            )
            # マージコミットをロールバック
            _git(["reset", "--hard", "HEAD~1"])
            return _inc_fail()

    # 4. 軽量メンテナンス（壊れた登録の解除のみ）
    _git(["worktree", "prune"])
    _git(["fetch", "--prune", "origin"])

    if merged is not None:
        merged.add(num)
    # 成功時は失敗カウントをリセット
    if fail_count is not None and num in fail_count:
        del fail_count[num]
    print(f"[INFO] PR #{num} マージ完了: {title}", flush=True)
    return "merged"


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
    print(f"[INFO] gh: {GH_CMD}", flush=True)
    print(
        "[INFO] 停止するには Ctrl+C を押してください",
        flush=True,
    )

    # 起動時に壊れたworktree登録とstaleリモート参照を掃除
    _git(["worktree", "prune"])
    _git(["fetch", "--prune", "origin"])

    # in_flight: 処理中（重複submit防止）
    # merged: マージ成功済み（再試行不要）
    # failed: 最大リトライ超過（スキップ対象）
    # fail_count: PR番号→失敗回数
    in_flight: set[int] = set()
    merged: set[int] = set()
    failed: set[int] = set()
    fail_count: dict[int, int] = {}

    def _on_future_done(
        future: object,
        pr_num: int,
    ) -> None:
        """ワーカー完了時コールバック。"""
        from concurrent.futures import Future

        in_flight.discard(pr_num)
        if isinstance(future, Future):
            exc = future.exception()
            if exc:
                print(
                    f"[ERROR] PR #{pr_num} 未処理例外: {exc}",
                    flush=True,
                )
                cnt = fail_count.get(pr_num, 0) + 1
                fail_count[pr_num] = cnt
                if cnt > MAX_MERGE_RETRIES:
                    failed.add(pr_num)
                    print(
                        f"[FAILED] PR #{pr_num} リトライ上限超過"
                        " - スキップ",
                        flush=True,
                    )
                return
            result = future.result()
            if result == "failed":
                failed.add(pr_num)

    with ThreadPoolExecutor(
        max_workers=MAX_PARALLEL_MERGES,
    ) as executor:
        try:
            while True:
                prs = get_open_prs()
                skip = merged | in_flight | failed
                new_prs = [pr for pr in prs if pr["number"] not in skip]

                if not new_prs:
                    print(
                        f"[INFO] 未処理PRなし"
                        f" - {POLL_INTERVAL_SEC}秒後"
                        "に再確認",
                        flush=True,
                    )
                else:
                    for pr in new_prs:
                        pr_num = pr["number"]
                        in_flight.add(pr_num)
                        fut = executor.submit(
                            auto_merge_pr,
                            pr,
                            merged,
                            fail_count,
                        )
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
