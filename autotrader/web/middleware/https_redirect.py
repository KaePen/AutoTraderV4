"""HTTPS リダイレクトミドルウェア

本番環境で HTTP → HTTPS リダイレクトを強制する。
"""

from __future__ import annotations

import logging

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

logger = logging.getLogger(__name__)


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """HTTPS リダイレクトミドルウェア

    HTTP リクエストを HTTPS にリダイレクトする。
    開発環境（localhost）は除外。
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
        # localhost は HTTPS リダイレクトしない
        host = request.headers.get("host", "")
        if "localhost" in host or "127.0.0.1" in host:
            return await call_next(request)

        # X-Forwarded-Proto ヘッダーで判定（プロキシ経由対応）
        proto = request.headers.get("x-forwarded-proto", "http")
        if proto == "http":
            url = request.url.replace(scheme="https")
            logger.info(
                "HTTP → HTTPS リダイレクト: %s", url
            )
            return RedirectResponse(
                url=str(url), status_code=301
            )

        return await call_next(request)
