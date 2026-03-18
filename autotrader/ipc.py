"""ファイルベースIPC ユーティリティ

supervisorとの連携用コマンドファイル監視機能を提供する。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path


def command_watcher(
    cmd_file: Path,
    logger: logging.Logger,
    *,
    poll_interval: float = 2.0,
) -> None:
    """コマンドファイルを定期監視するスレッド関数

    Args:
        cmd_file: 監視対象のJSONコマンドファイルパス
        logger: ロガーインスタンス
        poll_interval: 監視間隔（秒）
    """
    while True:
        try:
            if cmd_file.exists():
                data = json.loads(
                    cmd_file.read_text(encoding="utf-8"),
                )
                cmds = data.get("commands", [])
                cmd_file.unlink(missing_ok=True)
                if "shutdown" in cmds:
                    logger.info(
                        "shutdownコマンド受信、終了します",
                    )
                    logging.shutdown()
                    os._exit(0)
        except (json.JSONDecodeError, OSError):
            pass
        time.sleep(poll_interval)
