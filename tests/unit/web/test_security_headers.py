"""セキュリティヘッダーミドルウェアのテスト"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autotrader.web.middleware.security_headers import (
    SecurityHeadersMiddleware,
    _is_production,
)


@pytest.fixture
def app() -> FastAPI:
    """テスト用 FastAPI アプリ"""
    app = FastAPI()

    @app.get("/test")
    def test_endpoint():
        return {"status": "ok"}

    return app


@pytest.fixture
def client_dev(app: FastAPI) -> TestClient:
    """開発環境クライアント"""
    with patch.dict(os.environ, {"AUTOTRADER_ENV": "development"}):
        app.add_middleware(SecurityHeadersMiddleware)
        return TestClient(app)


@pytest.fixture
def client_prod(app: FastAPI) -> TestClient:
    """本番環境クライアント"""
    with patch.dict(os.environ, {"AUTOTRADER_ENV": "production"}):
        app.add_middleware(SecurityHeadersMiddleware, enable_hsts=True)
        return TestClient(app)


class TestIsProduction:
    """_is_production 関数のテスト"""

    def test_development_env(self):
        """開発環境"""
        with patch.dict(os.environ, {"AUTOTRADER_ENV": "development"}):
            assert _is_production() is False

    def test_production_env(self):
        """本番環境"""
        with patch.dict(os.environ, {"AUTOTRADER_ENV": "production"}):
            assert _is_production() is True

    def test_prod_env(self):
        """prod 環境（短縮形）"""
        with patch.dict(os.environ, {"AUTOTRADER_ENV": "prod"}):
            assert _is_production() is True

    def test_default_env(self):
        """デフォルト（未設定）"""
        with patch.dict(os.environ, {}, clear=True):
            # AUTOTRADER_ENV が未設定なら development 扱い
            assert _is_production() is False


class TestSecurityHeadersMiddleware:
    """SecurityHeadersMiddleware のテスト"""

    def test_x_content_type_options(self, client_dev: TestClient):
        """X-Content-Type-Options ヘッダー"""
        response = client_dev.get("/test")
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_x_frame_options(self, client_dev: TestClient):
        """X-Frame-Options ヘッダー"""
        response = client_dev.get("/test")
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_x_xss_protection(self, client_dev: TestClient):
        """X-XSS-Protection ヘッダー"""
        response = client_dev.get("/test")
        assert response.headers["X-XSS-Protection"] == "1; mode=block"

    def test_referrer_policy(self, client_dev: TestClient):
        """Referrer-Policy ヘッダー"""
        response = client_dev.get("/test")
        assert (
            response.headers["Referrer-Policy"]
            == "strict-origin-when-cross-origin"
        )

    def test_permissions_policy(self, client_dev: TestClient):
        """Permissions-Policy ヘッダー"""
        response = client_dev.get("/test")
        assert "geolocation=()" in response.headers["Permissions-Policy"]
        assert "microphone=()" in response.headers["Permissions-Policy"]
        assert "camera=()" in response.headers["Permissions-Policy"]

    def test_csp_development(self, client_dev: TestClient):
        """CSP ヘッダー（開発環境）"""
        response = client_dev.get("/test")
        csp = response.headers["Content-Security-Policy"]
        # 開発環境では unsafe-inline が許可される
        assert "'unsafe-inline'" in csp
        assert "default-src 'self'" in csp

    def test_no_hsts_development(self, client_dev: TestClient):
        """HSTS なし（開発環境）"""
        response = client_dev.get("/test")
        assert "Strict-Transport-Security" not in response.headers


class TestSecurityHeadersMiddlewareProduction:
    """本番環境でのテスト"""

    def test_hsts_production(self, client_prod: TestClient):
        """HSTS ヘッダー（本番環境）"""
        response = client_prod.get("/test")
        hsts = response.headers.get("Strict-Transport-Security", "")
        assert "max-age=31536000" in hsts
        assert "includeSubDomains" in hsts

    def test_csp_production(self, client_prod: TestClient):
        """CSP ヘッダー（本番環境）"""
        response = client_prod.get("/test")
        csp = response.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp


class TestCustomCSP:
    """カスタム CSP のテスト"""

    def test_custom_csp_policy(self):
        """カスタム CSP ポリシー"""
        app = FastAPI()

        @app.get("/test")
        def test_endpoint():
            return {"status": "ok"}

        custom_csp = "default-src 'none'; script-src 'self'"
        app.add_middleware(
            SecurityHeadersMiddleware, csp_policy=custom_csp
        )
        client = TestClient(app)

        response = client.get("/test")
        assert response.headers["Content-Security-Policy"] == custom_csp
