"""MT5ブリッジサーバー（Windows側で実行するスタンドアロン版）

このファイルをWindows側にコピーして実行する。
AutoTraderV4パッケージのインストール不要。

前提条件:
    1. Windows上のPython 3.10+
    2. pip install MetaTrader5
    3. MT5ターミナルが起動済み

使用方法:
    python mt5_bridge_server.py
    python mt5_bridge_server.py --port 18812
    python mt5_bridge_server.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import struct
import sys
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# MT5パッケージ（Windows環境でのみ利用可能）
_mt5: Any = None

# --- JSON-RPCプロトコル ---
ERROR_PARSE = -32700
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INTERNAL = -32603
ERROR_MT5 = -32000


def _init_mt5() -> bool:
    """MT5パッケージを初期化"""
    global _mt5
    try:
        import MetaTrader5 as mt5
        _mt5 = mt5
        return True
    except ImportError:
        logger.error(
            "MetaTrader5パッケージが見つかりません。\n"
            "インストール: pip install MetaTrader5"
        )
        return False


def _to_dict(obj: Any) -> Any:
    """NamedTuple/numpy arrayを辞書に変換"""
    if obj is None:
        return None
    if hasattr(obj, "_asdict"):
        return obj._asdict()
    if hasattr(obj, "dtype") and hasattr(obj, "__len__"):
        return [
            dict(zip(obj.dtype.names, row)) for row in obj
        ]
    return obj


def _make_response(
    result: Any = None,
    error: dict | None = None,
    req_id: int = 0,
) -> bytes:
    """JSON-RPCレスポンスをバイト列に変換"""
    data: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error:
        data["error"] = error
    else:
        data["result"] = result
    payload = json.dumps(data, default=str).encode("utf-8")
    header = struct.pack(">I", len(payload))
    return header + payload


def _make_error(code: int, message: str, req_id: int = 0) -> bytes:
    """エラーレスポンスを生成"""
    return _make_response(
        error={"code": code, "message": message},
        req_id=req_id,
    )


# --- RPCハンドラ ---
async def _handle_initialize(params: dict) -> Any:
    """MT5初期化"""
    kwargs: dict[str, Any] = {}
    if "path" in params:
        kwargs["path"] = params["path"]
    result = _mt5.initialize(**kwargs)
    if not result:
        error = _mt5.last_error()
        raise RuntimeError(f"MT5初期化失敗: {error}")
    logger.info("MT5初期化成功")
    return True


async def _handle_login(params: dict) -> Any:
    """MT5ログイン"""
    result = _mt5.login(
        int(params["login"]),
        password=str(params.get("password", "")),
        server=str(params.get("server", "")),
    )
    if not result:
        error = _mt5.last_error()
        raise RuntimeError(f"MT5ログイン失敗: {error}")
    acct = _mt5.account_info()
    if acct:
        logger.info(
            "ログイン成功: %s (残高: %.0f)",
            acct.server, acct.balance,
        )
    return True


async def _handle_shutdown(params: dict) -> Any:
    """MT5シャットダウン"""
    _mt5.shutdown()
    logger.info("MT5シャットダウン")
    return True


async def _handle_account_info(params: dict) -> Any:
    """口座情報取得"""
    return _to_dict(_mt5.account_info())


async def _handle_symbol_info(params: dict) -> Any:
    """シンボル情報取得"""
    return _to_dict(_mt5.symbol_info(params["symbol"]))


async def _handle_symbol_info_tick(params: dict) -> Any:
    """ティック情報取得"""
    return _to_dict(_mt5.symbol_info_tick(params["symbol"]))


async def _handle_copy_rates_from_pos(params: dict) -> Any:
    """直近N本のローソク足取得"""
    rates = _mt5.copy_rates_from_pos(
        params["symbol"],
        int(params["timeframe"]),
        int(params["start_pos"]),
        int(params["count"]),
    )
    return _to_dict(rates)


async def _handle_copy_rates_range(params: dict) -> Any:
    """期間指定ローソク足取得"""
    dt_from = datetime.fromtimestamp(
        int(params["date_from"]), tz=timezone.utc
    )
    dt_to = datetime.fromtimestamp(
        int(params["date_to"]), tz=timezone.utc
    )
    rates = _mt5.copy_rates_range(
        params["symbol"],
        int(params["timeframe"]),
        dt_from, dt_to,
    )
    return _to_dict(rates)


async def _handle_order_send(params: dict) -> Any:
    """注文送信"""
    result = _mt5.order_send(params["request"])
    return _to_dict(result)


async def _handle_positions_get(params: dict) -> Any:
    """オープンポジション取得"""
    if "symbol" in params:
        positions = _mt5.positions_get(symbol=params["symbol"])
    else:
        positions = _mt5.positions_get()
    if positions is None:
        return []
    return [_to_dict(p) for p in positions]


async def _handle_history_deals_get(params: dict) -> Any:
    """約定履歴取得"""
    dt_from = datetime.fromtimestamp(
        int(params["date_from"]), tz=timezone.utc
    )
    dt_to = datetime.fromtimestamp(
        int(params["date_to"]), tz=timezone.utc
    )
    deals = _mt5.history_deals_get(dt_from, dt_to)
    if deals is None:
        return []
    return [_to_dict(d) for d in deals]


# メソッド→ハンドラ
_HANDLERS = {
    "initialize": _handle_initialize,
    "login": _handle_login,
    "shutdown": _handle_shutdown,
    "account_info": _handle_account_info,
    "symbol_info": _handle_symbol_info,
    "symbol_info_tick": _handle_symbol_info_tick,
    "copy_rates_from_pos": _handle_copy_rates_from_pos,
    "copy_rates_range": _handle_copy_rates_range,
    "order_send": _handle_order_send,
    "positions_get": _handle_positions_get,
    "history_deals_get": _handle_history_deals_get,
}


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """クライアント接続ハンドラ"""
    addr = writer.get_extra_info("peername")
    logger.info("接続: %s", addr)

    try:
        while True:
            # 4バイト長さプレフィックス
            header = await reader.readexactly(4)
            msg_len = struct.unpack(">I", header)[0]
            data = await reader.readexactly(msg_len)

            # リクエストパース
            try:
                obj = json.loads(data.decode("utf-8"))
                method = str(obj.get("method", ""))
                params = obj.get("params", {})
                req_id = int(obj.get("id", 0))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                writer.write(_make_error(ERROR_PARSE, str(e)))
                await writer.drain()
                continue

            # ハンドラ実行
            handler = _HANDLERS.get(method)
            if handler is None:
                response = _make_error(
                    ERROR_METHOD_NOT_FOUND,
                    f"未定義: {method}",
                    req_id,
                )
            else:
                try:
                    result = await handler(params)
                    response = _make_response(result, req_id=req_id)
                except RuntimeError as e:
                    response = _make_error(
                        ERROR_MT5, str(e), req_id
                    )
                except Exception as e:
                    logger.exception("エラー: %s", e)
                    response = _make_error(
                        ERROR_INTERNAL, str(e), req_id
                    )

            writer.write(response)
            await writer.drain()

    except asyncio.IncompleteReadError:
        logger.info("切断: %s", addr)
    except Exception as e:
        logger.error("エラー: %s %s", addr, e)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def run_server(host: str, port: int) -> None:
    """サーバー起動"""
    if not _init_mt5():
        sys.exit(1)

    server = await asyncio.start_server(handle_client, host, port)
    addrs = ", ".join(
        str(sock.getsockname()) for sock in server.sockets
    )
    logger.info("MT5ブリッジサーバー起動: %s", addrs)
    print(f"\n{'='*50}")
    print(f"  MT5 Bridge Server")
    print(f"  Listening on {addrs}")
    print(f"  Ctrl+C で停止")
    print(f"{'='*50}\n")

    async with server:
        await server.serve_forever()


def main() -> None:
    """エントリーポイント"""
    parser = argparse.ArgumentParser(
        description="MT5 Bridge Server (Windowsで実行)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="バインドアドレス (デフォルト: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=18812,
        help="ポート番号 (デフォルト: 18812)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(run_server(args.host, args.port))
    except KeyboardInterrupt:
        print("\nサーバー停止")


if __name__ == "__main__":
    main()
