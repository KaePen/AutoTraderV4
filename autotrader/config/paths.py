"""データ・ログパス解決。

環境変数 or 固定外部パスから外部データパスを解決する。
worktree作業時でも同一パスを参照可能にする。

ディレクトリ構造:
    AutoTraderV4_data/
    ├── data/               マーケットデータ（29GB）
    ├── backtest/           BT出力を集約
    │   ├── results/        バックテスト結果JSON
    │   ├── month_results/  月別チェックポイント
    │   ├── logs/           バックテストログ
    │   └── worker_progress/ ワーカー進捗
    └── state/              ランタイムJSON集約
        ├── backtest_queue.json
        ├── backtest_queue_state.json
        ├── runner_state.json
        ├── runner_commands.json
        ├── bt_webui_commands.json
        ├── live_webui_commands.json
        ├── supervisor_state.json
        └── supervisor_events.json
"""
from __future__ import annotations

import os
from pathlib import Path

_DATA_ROOT = Path("D:/Projects/AutoTraderV4_data")


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


# ===================================================================
# データルート
# ===================================================================


def get_data_root() -> Path:
    """データルートディレクトリを返す。

    環境変数 AUTOTRADER_DATA_ROOT で上書き可能。

    Returns:
        Path: データルートパス
    """
    env = os.environ.get("AUTOTRADER_DATA_ROOT")
    return Path(env) if env else _DATA_ROOT


def get_data_dir() -> str:
    """マーケットデータディレクトリパスを解決する。

    優先順位:
    1. 環境変数 AUTOTRADER_DATA_DIR
    2. {data_root}/data（固定パス）
    3. フォールバック: プロジェクトルート/data

    Returns:
        str: データディレクトリパス
    """
    # 1. 環境変数
    env_dir = os.environ.get("AUTOTRADER_DATA_DIR")
    if env_dir:
        return env_dir

    # 2. 固定外部パス
    external = get_data_root() / "data"
    if external.exists():
        return str(external)

    # 3. フォールバック
    return str(_find_project_root() / "data")


# ===================================================================
# バックテスト出力
# ===================================================================


def get_backtest_dir() -> Path:
    """バックテスト出力ルート。

    Returns:
        Path: backtest/ ディレクトリパス
    """
    return get_data_root() / "backtest"


def get_results_dir() -> Path:
    """バックテスト結果ディレクトリ。

    新パス (backtest/results/) → 旧パス (backtest_results/) の
    順でフォールバックする。

    Returns:
        Path: 結果ディレクトリパス
    """
    new = get_backtest_dir() / "results"
    if new.exists():
        return new
    old = get_data_root() / "backtest_results"
    if old.exists():
        return old
    return new


def get_month_results_dir() -> Path:
    """月別チェックポイントディレクトリ。

    新パス (backtest/month_results/) → 旧パス
    (month_results/) の順でフォールバックする。

    Returns:
        Path: 月別結果ディレクトリパス
    """
    new = get_backtest_dir() / "month_results"
    if new.exists():
        return new
    old = get_data_root() / "month_results"
    if old.exists():
        return old
    return new


def get_log_dir() -> str:
    """ログディレクトリパスを解決する。

    優先順位:
    1. 環境変数 AUTOTRADER_LOG_DIR
    2. {data_root}/backtest/logs（新パス）
    3. {data_root}/logs（旧パス）
    4. フォールバック: プロジェクトルート/logs/backtest_log

    Returns:
        str: ログディレクトリパス
    """
    # 1. 環境変数
    env_dir = os.environ.get("AUTOTRADER_LOG_DIR")
    if env_dir:
        return env_dir

    # 2. 新パス
    new = get_backtest_dir() / "logs"
    if new.exists():
        return str(new)

    # 3. 旧パス
    old = get_data_root() / "logs"
    if old.exists():
        return str(old)

    # 4. フォールバック
    return str(
        _find_project_root() / "logs" / "backtest_log"
    )


def get_worker_progress_dir() -> Path:
    """ワーカー進捗ディレクトリ。

    新パス (backtest/worker_progress/) → 旧パス
    (worker_progress/) の順でフォールバックする。

    Returns:
        Path: ワーカー進捗ディレクトリパス
    """
    new = get_backtest_dir() / "worker_progress"
    if new.exists():
        return new
    old = get_data_root() / "worker_progress"
    if old.exists():
        return old
    return new


# ===================================================================
# ランタイム状態ファイル
# ===================================================================


def get_state_dir() -> Path:
    """ランタイム状態ディレクトリ。

    新パス (state/) → 旧パス (ルート直下) の順で
    フォールバックする。

    Returns:
        Path: 状態ディレクトリパス
    """
    new = get_data_root() / "state"
    if new.exists():
        return new
    return new  # 旧パスは個別ゲッターで処理


def _state_file(name: str) -> Path:
    """状態ファイルパスを解決する（新→旧フォールバック）。

    Args:
        name: ファイル名

    Returns:
        Path: ファイルパス
    """
    new = get_state_dir() / name
    if new.exists():
        return new
    old = get_data_root() / name
    if old.exists():
        return old
    return new


def get_queue_file() -> Path:
    """バックテストキューファイル。

    Returns:
        Path: backtest_queue.json パス
    """
    return _state_file("backtest_queue.json")


def get_queue_state_file() -> Path:
    """キュー実行状態ファイル。

    Returns:
        Path: backtest_queue_state.json パス
    """
    return _state_file("backtest_queue_state.json")


def get_runner_state_file() -> Path:
    """ランナー状態ファイル（WebUI連携）。

    Returns:
        Path: runner_state.json パス
    """
    return _state_file("runner_state.json")


def get_runner_cmd_file() -> Path:
    """ランナーコマンドファイル。

    Returns:
        Path: runner_commands.json パス
    """
    return _state_file("runner_commands.json")


def get_bt_webui_cmd_file() -> Path:
    """BT WebUIコマンドファイル。

    Returns:
        Path: bt_webui_commands.json パス
    """
    return _state_file("bt_webui_commands.json")


def get_live_webui_cmd_file() -> Path:
    """ライブWebUIコマンドファイル。

    Returns:
        Path: live_webui_commands.json パス
    """
    return _state_file("live_webui_commands.json")


def get_supervisor_state_file() -> Path:
    """スーパーバイザー状態ファイル。

    Returns:
        Path: supervisor_state.json パス
    """
    return _state_file("supervisor_state.json")


def get_supervisor_events_file() -> Path:
    """スーパーバイザーイベントファイル。

    Returns:
        Path: supervisor_events.json パス
    """
    return _state_file("supervisor_events.json")
