"""AutoTraderV4_data ディレクトリ構造マイグレーション。

旧構造（フラット）→ 新構造（論理グループ化）への移行を行う。
同一ドライブ内の shutil.move は rename 操作で即時完了。

前提: ランナー・WebUI・supervisor が停止していること。

使い方:
    uv run python scripts/migrate_data_dirs.py [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_DATA_ROOT = Path("D:/Projects/AutoTraderV4_data")

# 旧パス → 新パス のマッピング
_DIR_MOVES: list[tuple[str, str]] = [
    # (旧ディレクトリ名, 新パス)
    ("backtest_results", "backtest/results"),
    ("month_results", "backtest/month_results"),
    ("logs", "backtest/logs"),
    ("worker_progress", "backtest/worker_progress"),
]

_FILE_MOVES: list[tuple[str, str]] = [
    # (旧ファイル名, 新パス)
    ("backtest_queue.json", "state/backtest_queue.json"),
    (
        "backtest_queue_state.json",
        "state/backtest_queue_state.json",
    ),
    ("runner_state.json", "state/runner_state.json"),
    ("runner_commands.json", "state/runner_commands.json"),
    (
        "bt_webui_commands.json",
        "state/bt_webui_commands.json",
    ),
    (
        "live_webui_commands.json",
        "state/live_webui_commands.json",
    ),
    (
        "supervisor_state.json",
        "state/supervisor_state.json",
    ),
    (
        "supervisor_events.json",
        "state/supervisor_events.json",
    ),
]


def _migrate(dry_run: bool = False) -> None:
    """マイグレーション実行。

    Args:
        dry_run: Trueの場合、実際の移動は行わない
    """
    prefix = "[DRY-RUN] " if dry_run else ""

    # 親ディレクトリ作成
    for parent in ("backtest", "state"):
        d = _DATA_ROOT / parent
        if not d.exists():
            print(f"{prefix}mkdir: {d}")
            if not dry_run:
                d.mkdir(parents=True, exist_ok=True)

    # ディレクトリ移動
    for old_name, new_rel in _DIR_MOVES:
        old = _DATA_ROOT / old_name
        new = _DATA_ROOT / new_rel
        if not old.exists():
            print(f"  skip (不存在): {old}")
            continue
        if new.exists():
            print(f"  skip (既存): {new}")
            continue
        # 親ディレクトリ確保
        new.parent.mkdir(parents=True, exist_ok=True)
        print(f"{prefix}move: {old} → {new}")
        if not dry_run:
            shutil.move(str(old), str(new))

    # ファイル移動
    for old_name, new_rel in _FILE_MOVES:
        old = _DATA_ROOT / old_name
        new = _DATA_ROOT / new_rel
        if not old.exists():
            print(f"  skip (不存在): {old}")
            continue
        if new.exists():
            print(f"  skip (既存): {new}")
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        print(f"{prefix}move: {old} → {new}")
        if not dry_run:
            shutil.move(str(old), str(new))

    print(f"\n{prefix}マイグレーション完了")


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(
        description="AutoTraderV4_data 構造マイグレーション",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際の移動は行わず、計画のみ表示",
    )
    args = parser.parse_args()

    # 安全確認
    if not _DATA_ROOT.exists():
        print(f"ERROR: {_DATA_ROOT} が存在しません")
        sys.exit(1)

    if not args.dry_run:
        print("=== マイグレーション実行 ===")
        print(
            "前提: ランナー・WebUI・supervisor が"
            "停止していること"
        )
        print(f"対象: {_DATA_ROOT}")
        resp = input("続行しますか? [y/N] ")
        if resp.lower() != "y":
            print("中止しました")
            sys.exit(0)

    _migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
