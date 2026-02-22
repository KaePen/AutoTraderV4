#!/usr/bin/env python
"""WebUIサーバー起動スクリプト

使用例:
    uv run python scripts/run_webui.py
    uv run python scripts/run_webui.py --port 8080
    uv run python scripts/run_webui.py --reload
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root ))


def parse_args() -> argparse.Namespace:
    """引数パース

    Returns:
        argparse.Namespace: パース結果
    """
    parser = argparse.ArgumentParser(
        description="AutoTrader WebUIサーバー"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="ホストアドレス",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="ポート番号",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="開発モード（自動リロード）",
    )
    return parser.parse_args()


def main() -> None:
    """メイン関数"""
    args = parse_args()
    
    import uvicorn
    
    print(f"AutoTrader WebUI サーバー起動")
    print(f"URL: http://{args.host}:{args.port}")
    print(f"バックテストAPI: http://{args.host}:{args.port}/api/v1/backtest")
    print(f"WebSocket: ws://{args.host}:{args.port}/ws/backtest")
    print()
    
    uvicorn.run(
        "autotrader.web.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
