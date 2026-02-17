"""MT5ブリッジサーバー（Windows側で実行）

TCPソケットサーバでJSON-RPCリクエストを受信し、
MetaTrader5パッケージを直接呼び出してレスポンスを返す。

使用方法:
    python -m autotrader.adapters.mt5.bridge.server
    python -m autotrader.adapters.mt5.bridge.server --port 18812
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

from autotrader.adapters.mt5.bridge.protocol import (
    ERROR_INTERNAL,
    ERROR_METHOD_NOT_FOUND,
    ERROR_MT5,
    ERROR_PARSE,
    RpcResponse,
    make_error,
    parse_request,
)

logger = logging.getLogger(__name__)

# MT5パッケージ（Windows環境でのみ利用可能）
_mt5: Any = None


def _init_mt5() -> bool:
    """MT5パッケージを初期化

    Returns:
        bool: 成功か
    """
    global _mt5
    try:
        import MetaTrader5 as mt5  # type: ignore[import]
        _mt5 = mt5
        return True
    except ImportError:
        logger.error(
            "MetaTrader5パッケージが見つかりません。"
            "pip install MetaTrader5 を実行してください。"
        )
        return False


def _named_tuple_to_dict(obj: Any) -> Any:
    """NamedTupleや特殊オブジェクトを辞書に変換

    Args:
        obj: 変換対象

    Returns:
        Any: 変換結果
    """
    if obj is None:
        return None
    if hasattr(obj, "_asdict"):
        return obj._asdict()
    if hasattr(obj, "dtype") and hasattr(obj, "__len__"):
        # numpy structured array
        return [
            dict(zip(obj.dtype.names, row)) for row in obj
        ]
    return obj


# --- RPCハンドラマップ ---
async def _handle_initialize(params: dict) -> Any:
    """MT5初期化"""
    kwargs: dict[str, Any] = {}
    if "path" in params:
        kwargs["path"] = params["path"]
    result = _mt5.initialize(**kwargs)
    if not result:
        error = _mt5.last_error()
        raise RuntimeError(f"MT5初期化失敗: {error}")
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
    return True


async def _handle_shutdown(params: dict) -> Any:
    """MT5シャットダウン"""
    _mt5.shutdown()
    return True


async def _handle_account_info(params: dict) -> Any:
    """口座情報取得"""
    info = _mt5.account_info()
    return _named_tuple_to_dict(info)


async def _handle_symbol_info(params: dict) -> Any:
    """シンボル情報取得"""
    info = _mt5.symbol_info(params["symbol"])
    return _named_tuple_to_dict(info)


async def _handle_symbol_info_tick(params: dict) -> Any:
    """ティック情報取得"""
    tick = _mt5.symbol_info_tick(params["symbol"])
    return _named_tuple_to_dict(tick)


async def _handle_copy_rates_from_pos(params: dict) -> Any:
    """直近N本のローソク足取得"""
    rates = _mt5.copy_rates_from_pos(
        params["symbol"],
        int(params["timeframe"]),
        int(params["start_pos"]),
        int(params["count"]),
    )
    return _named_tuple_to_dict(rates)


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
    return _named_tuple_to_dict(rates)


async def _handle_order_send(params: dict) -> Any:
    """注文送信"""
    result = _mt5.order_send(params["request"])
    return _named_tuple_to_dict(result)


async def _handle_positions_get(params: dict) -> Any:
    """オープンポジション取得"""
    if "symbol" in params:
        positions = _mt5.positions_get(symbol=params["symbol"])
    else:
        positions = _mt5.positions_get()
    if positions is None:
        return []
    return [_named_tuple_to_dict(p) for p in positions]


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
    return [_named_tuple_to_dict(d) for d in deals]


# メソッド→ハンドラのマッピング
_HANDLERS: dict[str, Any] = {
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
    """クライアント接続ハンドラ

    Args:
        reader: ストリームリーダー
        writer: ストリームライター
    """
    addr = writer.get_extra_info("peername")
    logger.info("クライアント接続: %s", addr)

    try:
        while True:
            # 長さプレフィックス読み取り
            header = await reader.readexactly(4)
            msg_len = struct.unpack(">I", header)[0]

            # メッセージ本体読み取り
            data = await reader.readexactly(msg_len)

            # リクエストパース
            try:
                request = parse_request(data)
            except ValueError as e:
                response = make_error(ERROR_PARSE, str(e))
                writer.write(response.to_bytes())
                await writer.drain()
                continue

            # ハンドラ実行
            handler = _HANDLERS.get(request.method)
            if handler is None:
                response = make_error(
                    ERROR_METHOD_NOT_FOUND,
                    f"メソッド未定義: {request.method}",
                    request.id,
                )
            else:
                try:
                    result = await handler(request.params)
                    response = RpcResponse(
                        result=result, id=request.id
                    )
                except RuntimeError as e:
                    response = make_error(
                        ERROR_MT5, str(e), request.id
                    )
                except Exception as e:
                    logger.exception("ハンドラエラー: %s", e)
                    response = make_error(
                        ERROR_INTERNAL, str(e), request.id
                    )

            writer.write(response.to_bytes())
            await writer.drain()

    except asyncio.IncompleteReadError:
        logger.info("クライアント切断: %s", addr)
    except Exception as e:
        logger.error("クライアントエラー: %s %s", addr, e)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def run_server(
    host: str = "0.0.0.0", port: int = 18812
) -> None:
    """ブリッジサーバー起動

    Args:
        host: バインドホスト
        port: バインドポート
    """
    if not _init_mt5():
        sys.exit(1)

    server = await asyncio.start_server(
        handle_client, host, port
    )

    addrs = ", ".join(
        str(sock.getsockname()) for sock in server.sockets
    )
    logger.info("MT5ブリッジサーバー起動: %s", addrs)
    print(f"MT5 Bridge Server listening on {addrs}")

    async with server:
        await server.serve_forever()


def main() -> None:
    """エントリーポイント"""
    parser = argparse.ArgumentParser(
        description="MT5 Bridge Server (Windows側)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="バインドホスト"
    )
    parser.add_argument(
        "--port", type=int, default=18812, help="バインドポート"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        asyncio.run(run_server(args.host, args.port))
    except KeyboardInterrupt:
        print("\nサーバー停止")


if __name__ == "__main__":
    main()
