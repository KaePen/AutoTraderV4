"""認証機能のユニットテスト"""

from __future__ import annotations

import pytest

from autotrader.web.auth.security import (
    create_access_token,
    get_password_hash,
    verify_password,
    verify_token,
)


def test_password_hash_and_verify():
    """パスワードハッシュ化・検証テスト"""
    password = "test_password_123"
    hashed = get_password_hash(password)

    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("wrong_password", hashed)


def test_create_and_verify_token():
    """JWTトークン生成・検証テスト"""
    data = {"sub": "testuser", "role": "admin"}
    token = create_access_token(data)

    assert isinstance(token, str)
    assert len(token) > 0

    payload = verify_token(token)
    assert payload is not None
    assert payload["sub"] == "testuser"
    assert payload["role"] == "admin"
    assert "exp" in payload


def test_verify_invalid_token():
    """無効なトークンの検証テスト"""
    invalid_token = "invalid.token.here"
    payload = verify_token(invalid_token)
    assert payload is None


@pytest.mark.asyncio
async def test_login_endpoint(client):
    """ログインエンドポイントテスト"""
    response = await client.post(
        "/api/v1/auth/token",
        json={"username": "admin", "password": "admin"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "access_token" in data["data"]
    assert data["data"]["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_credentials(client):
    """無効な認証情報でのログインテスト"""
    response = await client.post(
        "/api/v1/auth/token",
        json={"username": "admin", "password": "wrong"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_me_endpoint(client):
    """ユーザー情報取得エンドポイントテスト"""
    login_response = await client.post(
        "/api/v1/auth/token",
        json={"username": "admin", "password": "admin"},
    )
    token = login_response.json()["data"]["access_token"]

    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["username"] == "admin"
    assert data["data"]["role"] == "admin"


@pytest.mark.asyncio
async def test_get_me_without_token(client):
    """トークンなしでのユーザー情報取得テスト"""
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 403  # HTTPBearer requires token
