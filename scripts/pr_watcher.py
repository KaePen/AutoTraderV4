"""PRウォッチャー - 新規PRを検知してClaude Codeを自動起動する。

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
import time
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
PROJECT_DIR = Path(
    os.environ.get("PROJECT_DIR", Path(__file__).parent.parent)
)

REVIEW_PROMPT = """
あなたはPRのコードレビューとmainへのマージを担当するエージェントです。

対象PR: #{pr_number} - {pr_title}
ブランチ: {pr_branch} → main

以下の手順で対応してください:
1. git diff main...{pr_branch} で変更内容を確認
2. テスト実行 (.venv/Scripts/python -m pytest tests/ -q)
3. コードレビュー（バグ・セキュリティ・スタイル）
4. 問題なければ main にマージ（git merge --no-ff）
5. git push origin main

問題がある場合は処理を中断して理由を説明してください。
"""


def get_open_prs() -> list[dict]:
    """GitHub APIでオープンなPR一覧を取得する。"""
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
        print(f"[ERROR] gh pr list failed: {result.stderr}", flush=True)
        return []
    return json.loads(result.stdout)


def run_review_agent(pr: dict) -> None:
    """PR対応のClaude Codeエージェントを起動する。

    Args:
        pr: PR情報辞書（number, title, headRefName）
    """
    prompt = REVIEW_PROMPT.format(
        pr_number=pr["number"],
        pr_title=pr["title"],
        pr_branch=pr["headRefName"],
    )

    print(
        f"[INFO] PR #{pr['number']} のレビューを開始: {pr['title']}",
        flush=True,
    )

    # CLAUDECODE環境変数を除去（ネストセッション防止エラー回避）
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

    status = "完了" if result.returncode == 0 else f"エラー(code={result.returncode})"
    print(f"[INFO] PR #{pr['number']} レビュー{status}", flush=True)


def main() -> None:
    """メインループ - 定期的にPRを確認してエージェントを起動する。"""
    print(f"[INFO] PRウォッチャー起動 (間隔: {POLL_INTERVAL_SEC}秒)", flush=True)
    print(f"[INFO] 対象リポジトリ: {REPO}", flush=True)
    print(f"[INFO] claude: {CLAUDE_CMD}", flush=True)
    print(f"[INFO] gh: {GH_CMD}", flush=True)
    print("[INFO] 停止するには Ctrl+C を押してください", flush=True)

    # セッション中のみ処理済みを記憶（再起動時はリセット）
    processed: set[int] = set()

    try:
        while True:
            prs = get_open_prs()
            new_prs = [pr for pr in prs if pr["number"] not in processed]

            if not new_prs:
                print(
                    f"[INFO] 未処理PRなし - {POLL_INTERVAL_SEC}秒後に再確認",
                    flush=True,
                )
            else:
                for pr in new_prs:
                    run_review_agent(pr)
                    processed.add(pr["number"])

            time.sleep(POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\n[INFO] PRウォッチャーを停止しました", flush=True)


if __name__ == "__main__":
    main()
