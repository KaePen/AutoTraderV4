"""MT5ブリッジ接続テスト

WSL2からWindows側ブリッジサーバーへの接続確認。
WSL2ではlocalhostがWindows側に到達しないため、
WindowsホストIPを自動検出して接続する。

使用方法:
    python scripts/test_bridge.py
    python scripts/test_bridge.py --host 10.255.255.254
    MT5_BRIDGE_HOST=10.255.255.254 python scripts/test_bridge.py
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys


def _detect_wsl_host_ip() -> str:
    """WSL2環境でWindowsホストIPを自動検出"""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # default via X.X.X.X dev eth0
        for part in result.stdout.split():
            if part.count(".") == 3:
                return part
    except Exception:
        pass

    # フォールバック: resolv.conf
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.startswith("nameserver"):
                    return line.split()[1]
    except Exception:
        pass

    return "localhost"


async def test(host: str, port: int) -> None:
    """ブリッジ接続テスト"""
    # 遅延importでパッケージ依存を確認
    from autotrader.adapters.mt5.connection import (
        BridgeTransport,
    )
    from autotrader.adapters.mt5.exceptions import (
        MT5BridgeError,
    )

    print(f"接続先: {host}:{port}")
    t = BridgeTransport(host=host, port=port)
    try:
        await t.initialize()
    except MT5BridgeError:
        print("エラー: ブリッジサーバーに接続できません。")
        print()
        print("Windows側で以下を実行してください:")
        print(
            "  C:\\Users\\yamas\\AppData\\Local\\Programs"
            "\\Python\\Python312\\python.exe"
            " C:\\Users\\yamas\\mt5_bridge_server.py"
        )
        print()
        print("またはWSLから:")
        print(
            "  /mnt/c/Users/yamas/AppData/Local/Programs"
            "/Python/Python312/python.exe"
            " /mnt/c/Users/yamas/mt5_bridge_server.py"
        )
        sys.exit(1)

    print("初期化OK")
    info = await t.account_info()
    print(
        f"口座: 残高={info.get('balance', 0):,.0f} "
        f"証拠金={info.get('equity', 0):,.0f}"
    )
    tick = await t.symbol_info_tick("USDJPY")
    print(
        f"USDJPY: ask={tick.get('ask', 0)} "
        f"bid={tick.get('bid', 0)}"
    )
    await t.shutdown()
    print("テスト完了")


def main() -> None:
    """エントリーポイント"""
    parser = argparse.ArgumentParser(
        description="MT5ブリッジ接続テスト"
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("MT5_BRIDGE_HOST", ""),
        help="ブリッジサーバーホスト（未指定時は自動検出）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(
            os.environ.get("MT5_BRIDGE_PORT", "18812")
        ),
        help="ブリッジサーバーポート（デフォルト: 18812）",
    )
    args = parser.parse_args()

    # ホストIP決定
    host = args.host
    if not host:
        host = _detect_wsl_host_ip()
        print(f"WindowsホストIP自動検出: {host}")

    asyncio.run(test(host, args.port))


if __name__ == "__main__":
    main()
