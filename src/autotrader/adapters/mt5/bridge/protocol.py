"""JSON-RPCプロトコル定義

メッセージフレーミング: 4バイト長さプレフィックス + JSON本文
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any

# エラーコード
ERROR_PARSE = -32700       # パースエラー
ERROR_INVALID_REQ = -32600 # 無効なリクエスト
ERROR_METHOD_NOT_FOUND = -32601  # メソッド未定義
ERROR_INVALID_PARAMS = -32602    # 無効なパラメータ
ERROR_INTERNAL = -32603    # 内部エラー
ERROR_MT5 = -32000         # MT5固有エラー


@dataclass(frozen=True)
class RpcRequest:
    """JSON-RPCリクエスト

    Attributes:
        method: メソッド名
        params: パラメータ
        id: リクエストID
    """

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int = 0

    def to_bytes(self) -> bytes:
        """バイト列に変換（長さプレフィックス付き）

        Returns:
            bytes: フレーム化されたメッセージ
        """
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": self.method,
            "params": self.params,
            "id": self.id,
        }).encode("utf-8")
        header = struct.pack(">I", len(payload))
        return header + payload


@dataclass(frozen=True)
class RpcResponse:
    """JSON-RPCレスポンス

    Attributes:
        result: 結果（成功時）
        error: エラー情報（失敗時）
        id: リクエストID
    """

    result: Any = None
    error: dict[str, Any] | None = None
    id: int = 0

    def to_bytes(self) -> bytes:
        """バイト列に変換（長さプレフィックス付き）

        Returns:
            bytes: フレーム化されたメッセージ
        """
        data: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error:
            data["error"] = self.error
        else:
            data["result"] = self.result

        payload = json.dumps(data, default=str).encode("utf-8")
        header = struct.pack(">I", len(payload))
        return header + payload


def make_error(
    code: int, message: str, req_id: int = 0
) -> RpcResponse:
    """エラーレスポンスを生成

    Args:
        code: エラーコード
        message: エラーメッセージ
        req_id: リクエストID

    Returns:
        RpcResponse: エラーレスポンス
    """
    return RpcResponse(
        error={"code": code, "message": message},
        id=req_id,
    )


def parse_request(data: bytes) -> RpcRequest:
    """バイト列からリクエストをパース

    Args:
        data: JSONバイト列（長さプレフィックスなし）

    Returns:
        RpcRequest: パース済みリクエスト

    Raises:
        ValueError: パースエラー
    """
    try:
        obj = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"JSONパースエラー: {e}") from e

    return RpcRequest(
        method=str(obj.get("method", "")),
        params=obj.get("params", {}),
        id=int(obj.get("id", 0)),
    )


def parse_response(data: bytes) -> RpcResponse:
    """バイト列からレスポンスをパース

    Args:
        data: JSONバイト列（長さプレフィックスなし）

    Returns:
        RpcResponse: パース済みレスポンス

    Raises:
        ValueError: パースエラー
    """
    try:
        obj = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"JSONパースエラー: {e}") from e

    return RpcResponse(
        result=obj.get("result"),
        error=obj.get("error"),
        id=int(obj.get("id", 0)),
    )
