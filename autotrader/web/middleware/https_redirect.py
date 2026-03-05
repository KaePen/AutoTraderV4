"""HTTPS リダイレクトミドルウェア

本番環境で HTTP → HTTPS リダイレクトを強制する。
"""

from __future__ import annotations

import ipaddress
import logging

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

logger = logging.getLogger(__name__)

# HTTPSリダイレクトをスキップするプライベートネットワーク
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def _is_private_host(host: str) -> bool:
    """ホストがプライベートネットワークかどうか判定

    Args:
        host: ホスト文字列（ポート付き可）

    Returns:
        bool: プライベートネットワークなら True
    """
    # ポート番号を除去
    hostname = host.split(":")[0]

    if hostname in ("localhost",):
        return True

    try:
        addr = ipaddress.ip_address(hostname)
        return any(
            addr in net for net in _PRIVATE_NETWORKS
        )
    except ValueError:
        return False


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """HTTPS リダイレクトミドルウェア

    HTTP リクエストを HTTPS にリダイレクトする。
    プライベートネットワーク（localhost, 192.168.x.x 等）は除外。
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """リクエスト処理

        Args:
            request: リクエストオブジェクト
            call_next: 次のミドルウェア/ハンドラ

        Returns:
            Response: レスポンス
        """
        # プライベートネットワークは HTTPS リダイレクトしない
        host = request.headers.get("host", "")
        if _is_private_host(host):
            return await call_next(request)

        # X-Forwarded-Proto ヘッダーで判定（プロキシ経由対応）
        proto = request.headers.get("x-forwarded-proto", "http")
        if proto == "http":
            url = request.url.replace(scheme="https")
            logger.info(
                "HTTP → HTTPS リダイレクト: %s", url
            )
            return RedirectResponse(
                url=str(url), status_code=302
            )

        return await call_next(request)
