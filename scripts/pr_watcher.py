"""PRウォッチャー - 新規PRを検知してClaude Codeを自動起動する。

使い方:
    python scripts/pr_watcher.py

環境変数:
    GITHUB_TOKEN: GitHub Personal Access Token (repo権限)
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

# Windowsコンソールの文字化け対策
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# 設定
REPO = "KaePen/AutoTraderV4"
POLL_INTERVAL_SEC = 30  # PR確認間隔（秒）
PROCESSED_FILE = Path(__file__).parent / ".pr_watcher_processed.json"
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).parent.parent))

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


def load_processed() -> set[int]:
    """処理済みPR番号を読み込む。"""
    if PROCESSED_FILE.exists():
        data = json.loads(PROCESSED_FILE.read_text())
        return set(data.get("processed", []))
    return set()


def save_processed(processed: set[int]) -> None:
    """処理済みPR番号を保存する。"""
    PROCESSED_FILE.write_text(
        json.dumps({"processed": list(processed)})
    )


def get_open_prs() -> list[dict]:
    """GitHub APIでオープンなPR一覧を取得する。"""
    cmd = (
        f"gh pr list --repo {REPO} --state open --base main"
        " --json number,title,headRefName"
    )
    result = subprocess.run(
        cmd, capture_output=True, text=True, shell=True
    )
    if result.returncode != 0:
        print(f"[ERROR] gh pr list failed: {result.stderr}")
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
    print(f"[INFO] PR #{pr['number']} のレビューを開始: {pr['title']}")

    subprocess.run(
        f'claude -p "{prompt}" --allowedTools "Bash,Read,Edit,Write,Glob,Grep"',
        cwd=str(PROJECT_DIR),
        shell=True,
    )


def main() -> None:
    """メインループ - 定期的にPRを確認してエージェントを起動する。"""
    print(f"[INFO] PRウォッチャー起動 (間隔: {POLL_INTERVAL_SEC}秒)")
    print(f"[INFO] 対象リポジトリ: {REPO}")

    processed = load_processed()

    while True:
        prs = get_open_prs()

        for pr in prs:
            if pr["number"] not in processed:
                run_review_agent(pr)
                processed.add(pr["number"])
                save_processed(processed)

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
