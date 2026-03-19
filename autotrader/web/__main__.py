"""WebUIサーバー起動エントリーポイント

使用方法:
    python -m autotrader.web [--show-mt5] [--auto-trade]
"""
from __future__ import annotations

import argparse
import logging
import os
import threading

import uvicorn

# ログ設定（uvicorn起動前に設定）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("live_webui")

from autotrader.config.paths import get_live_webui_cmd_file

_LIVE_WEBUI_CMD_FILE = get_live_webui_cmd_file()


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパース"""
    parser = argparse.ArgumentParser(
        description="AutoTrader WebUI サーバー",
    )
    parser.add_argument(
        "--show-mt5",
        action="store_true",
        help="MT5ウィンドウを表示する（デフォルト: 非表示）",
    )
    parser.add_argument(
        "--auto-trade",
        action="store_true",
        default=False,
        help="起動時に全採用ペアの自動トレードをONにする",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="バインドホスト（デフォルト: 0.0.0.0）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="バインドポート（デフォルト: 8000）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # CLIフラグ → 環境変数で既存ロジックに伝搬
    if args.auto_trade:
        os.environ["AUTOTRADER_AUTO_TRADE"] = "1"

    # MT5ウィンドウ表示/非表示を環境変数で伝搬
    if args.show_mt5:
        os.environ["MT5_SHOW_WINDOW"] = "1"

    # コマンドファイル監視スレッド起動（supervisor連携）
    from autotrader.ipc import command_watcher

    threading.Thread(
        target=command_watcher,
        args=(_LIVE_WEBUI_CMD_FILE, logger),
        daemon=True,
    ).start()

    from autotrader.web.main import app

    uvicorn.run(app, host=args.host, port=args.port, ws="wsproto")
