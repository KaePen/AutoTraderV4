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


GH_CMD = _find_gh_executable()

# 設定
REPO = "KaePen/AutoTraderV4"
POLL_INTERVAL_SEC = 30
MAX_PARALLEL_MERGES = 5  # 同時処理するPR数の上限
PROJECT_DIR = Path(
    os.environ.get("PROJECT_DIR", Path(__file__).parent.parent)
)

# merge + push は排他制御（並行マージによるpush競合を防止）
_merge_lock = threading.Lock()


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
            print(f"[ERROR] PR #{num} merge失敗: {r.stderr}", flush=True)
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
