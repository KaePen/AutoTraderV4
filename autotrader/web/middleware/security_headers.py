"""セキュリティヘッダーミドルウェア

XSS、clickjacking、MIME sniffing 等の攻撃を防ぐための
セキュリティヘッダーを全レスポンスに付与する。
"""

from __future__ import annotations

import os

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import Response


def _is_production() -> bool:
    """本番環境かどうかを判定

    Returns:
        bool: 本番環境なら True
    """
    env = os.environ.get("AUTOTRADER_ENV", "development").lower()
    return env in ("production", "prod")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """セキュリティヘッダーミドルウェア

    全レスポンスに以下のセキュリティヘッダーを付与:
    - Content-Security-Policy: XSS対策
    - X-Content-Type-Options: MIME type sniffing 対策
    - X-Frame-Options: clickjacking 対策
    - Strict-Transport-Security: HTTPS 強制（本番のみ）
    - X-XSS-Protection: XSS フィルタ

    Attributes:
        enable_hsts: HSTS を有効にするか（本番環境のみ True）
        csp_policy: Content-Security-Policy の値
    """

    def __init__(
        self,
        app,
        enable_hsts: bool | None = None,
        csp_policy: str | None = None,
    ):
        """初期化

        Args:
            app: ASGI アプリケーション
            enable_hsts: HSTS を有効にするか（None で自動判定）
            csp_policy: CSP ポリシー（None でデフォルト値）
        """
        super().__init__(app)
        self.enable_hsts = (
            enable_hsts if enable_hsts is not None else _is_production()
        )
        self.csp_policy = csp_policy or self._default_csp()

    def _default_csp(self) -> str:
        """デフォルトの CSP ポリシーを生成

        Returns:
            str: CSP ポリシー文字列
        """
        # 本番環境では厳格な CSP を適用
        # 開発環境では unsafe-inline を許可（デバッグ用）
        if _is_production():
            return (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self' wss:; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        else:
            # 開発環境: inline script/style を許可
            return (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self' ws: wss:; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """リクエスト処理

        Args:
            request: リクエストオブジェクト
            call_next: 次のミドルウェア/ハンドラ

        Returns:
            Response: セキュリティヘッダー付きレスポンス
        """
        response = await call_next(request)

        # Content-Security-Policy: XSS対策
        response.headers["Content-Security-Policy"] = self.csp_policy

        # X-Content-Type-Options: MIME type sniffing 対策
        response.headers["X-Content-Type-Options"] = "nosniff"

        # X-Frame-Options: clickjacking 対策
        response.headers["X-Frame-Options"] = "DENY"

        # X-XSS-Protection: XSS フィルタ（レガシーブラウザ向け）
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Strict-Transport-Security: HTTPS 強制（本番のみ）
        if self.enable_hsts:
            # max-age=31536000 (1年), includeSubDomains
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # Referrer-Policy: リファラ情報の制御
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions-Policy: ブラウザ機能の制限
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )

        return response
