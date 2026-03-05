"""データ・ログパス解決。

環境変数 or 固定外部パスから外部データパスを解決する。
worktree作業時でも同一パスを参照可能にする。
"""
from __future__ import annotations

import os
from pathlib import Path


def _find_project_root() -> Path:
    """プロジェクトルートを探索する。

    .git ディレクトリまたはファイルを持つ最も近い親を返す。
    見つからない場合はカレントディレクトリ。

    Returns:
        Path: プロジェクトルートパス
    """
    current = Path.cwd()
    for parent in [current, *current.parents]:
        git_path = parent / ".git"
        # 通常リポジトリ: .git はディレクトリ
        # worktree: .git はファイル
        if git_path.exists():
            return parent
    return current


def get_data_dir() -> str:
    """データディレクトリパスを解決する。

    優先順位:
    1. 環境変数 AUTOTRADER_DATA_DIR
    2. D:/Projects/AutoTraderV4_data/data（固定パス）
    3. フォールバック: プロジェクトルート/data

    Returns:
        str: データディレクトリパス
    """
    # 1. 環境変数
    env_dir = os.environ.get("AUTOTRADER_DATA_DIR")
    if env_dir:
        return env_dir

    # 2. 固定外部パス
    external = Path("D:/Projects/AutoTraderV4_data/data")
    if external.exists():
        return str(external)

    # 3. フォールバック
    return str(_find_project_root() / "data")


def get_log_dir() -> str:
    """ログディレクトリパスを解決する。

    優先順位:
    1. 環境変数 AUTOTRADER_LOG_DIR
    2. D:/Projects/AutoTraderV4_data/logs（固定パス）
    3. フォールバック: プロジェクトルート/logs/backtest_log

    Returns:
        str: ログディレクトリパス
    """
    # 1. 環境変数
    env_dir = os.environ.get("AUTOTRADER_LOG_DIR")
    if env_dir:
        return env_dir

    # 2. 固定外部パス
    external = Path("D:/Projects/AutoTraderV4_data/logs")
    if external.exists():
        return str(external)

    # 3. フォールバック
    return str(
        _find_project_root() / "logs" / "backtest_log"
    )
