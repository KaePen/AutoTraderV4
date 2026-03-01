"""認証機能のユニットテスト"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from autotrader.web.auth.security import (
    create_access_token,
    get_password_hash,
    verify_password,
    verify_token,
)
from autotrader.web.auth.user_store import (
    UserStore,
    get_user_store,
    reset_user_store,
)


class TestPasswordSecurity:
    """パスワードハッシュ化・検証テスト"""

    def test_password_hash_and_verify(self):
        """パスワードハッシュ化・検証"""
        password = "test_password_123"
        hashed = get_password_hash(password)

        assert hashed != password
        assert verify_password(password, hashed)
        assert not verify_password("wrong_password", hashed)

    def test_different_passwords_different_hashes(self):
        """異なるパスワードは異なるハッシュを生成"""
        hash1 = get_password_hash("password1")
        hash2 = get_password_hash("password2")
        assert hash1 != hash2


class TestJWTToken:
    """JWTトークン生成・検証テスト"""

    def test_create_and_verify_token(self):
        """JWTトークン生成・検証"""
        data = {"sub": "testuser", "role": "admin"}
        token = create_access_token(data)

        assert isinstance(token, str)
        assert len(token) > 0

        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "testuser"
        assert payload["role"] == "admin"
        assert "exp" in payload

    def test_verify_invalid_token(self):
        """無効なトークンの検証"""
        invalid_token = "invalid.token.here"
        payload = verify_token(invalid_token)
        assert payload is None

    def test_verify_empty_token(self):
        """空トークンの検証"""
        payload = verify_token("")
        assert payload is None


class TestUserStore:
    """YAMLベースユーザーストアのテスト"""

    @pytest.fixture(autouse=True)
    def reset_store(self):
        """各テスト後にシングルトンをリセット"""
        yield
        reset_user_store()

    def test_create_default_user(self, tmp_path):
        """デフォルトユーザー作成"""
        auth_file = tmp_path / "auth.yaml"
        store = UserStore(auth_file)

        # デフォルトユーザーが作成されている
        users = store.list_users()
        assert "admin" in users

        # ファイルが作成されている
        assert auth_file.exists()

    def test_authenticate_success(self, tmp_path):
        """認証成功"""
        auth_file = tmp_path / "auth.yaml"
        store = UserStore(auth_file)

        # デフォルトユーザーで認証
        user = store.authenticate("admin", "admin")
        assert user is not None
        assert user["username"] == "admin"
        assert user["role"] == "admin"

    def test_authenticate_wrong_password(self, tmp_path):
        """パスワード誤りで認証失敗"""
        auth_file = tmp_path / "auth.yaml"
        store = UserStore(auth_file)

        user = store.authenticate("admin", "wrong")
        assert user is None

    def test_authenticate_unknown_user(self, tmp_path):
        """存在しないユーザーで認証失敗"""
        auth_file = tmp_path / "auth.yaml"
        store = UserStore(auth_file)

        user = store.authenticate("unknown", "password")
        assert user is None

    def test_add_user(self, tmp_path):
        """ユーザー追加"""
        auth_file = tmp_path / "auth.yaml"
        store = UserStore(auth_file)

        # 新規ユーザー追加
        user = store.add_user("newuser", "password123", "user")
        assert user["username"] == "newuser"
        assert user["role"] == "user"

        # 認証可能
        auth_user = store.authenticate("newuser", "password123")
        assert auth_user is not None

    def test_update_password(self, tmp_path):
        """パスワード更新"""
        auth_file = tmp_path / "auth.yaml"
        store = UserStore(auth_file)

        # パスワード更新
        result = store.update_password("admin", "newpassword")
        assert result is True

        # 新パスワードで認証可能
        user = store.authenticate("admin", "newpassword")
        assert user is not None

        # 旧パスワードでは認証不可
        user = store.authenticate("admin", "admin")
        assert user is None

    def test_delete_user(self, tmp_path):
        """ユーザー削除"""
        auth_file = tmp_path / "auth.yaml"
        store = UserStore(auth_file)

        # ユーザー追加
        store.add_user("toDelete", "password", "user")
        assert "toDelete" in store.list_users()

        # 削除
        result = store.delete_user("toDelete")
        assert result is True
        assert "toDelete" not in store.list_users()

    def test_reload(self, tmp_path):
        """YAMLファイル再読み込み"""
        auth_file = tmp_path / "auth.yaml"
        store = UserStore(auth_file)

        # ユーザー追加
        store.add_user("reloadTest", "password", "user")

        # 新しいストアインスタンスで読み込み
        store2 = UserStore(auth_file)
        assert "reloadTest" in store2.list_users()


class TestAuthEndpoints:
    """認証エンドポイントのテスト"""

    def test_login_success(self, client, tmp_path, monkeypatch):
        """ログイン成功"""
        # テスト用auth.yamlを使用
        auth_file = tmp_path / "auth.yaml"
        reset_user_store()

        # UserStoreをモックして一時ファイルを使用
        store = UserStore(auth_file)
        with patch(
            "autotrader.web.auth.router.get_user_store",
            return_value=store,
        ):
            response = client.post(
                "/api/v1/auth/token",
                json={
                    "username": "admin",
                    "password": "admin",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "access_token" in data["data"]
        assert data["data"]["token_type"] == "bearer"

    def test_login_invalid_credentials(
        self, client, tmp_path
    ):
        """無効な認証情報でログイン失敗"""
        auth_file = tmp_path / "auth.yaml"
        reset_user_store()

        store = UserStore(auth_file)
        with patch(
            "autotrader.web.auth.router.get_user_store",
            return_value=store,
        ):
            response = client.post(
                "/api/v1/auth/token",
                json={
                    "username": "admin",
                    "password": "wrong",
                },
            )

        assert response.status_code == 401

    def test_get_me_endpoint(self, client):
        """ユーザー情報取得（モック認証）"""
        # conftest.py で get_current_user がモックされているため、
        # 常に認証済みとして扱われる
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["username"] == "test_admin"
        assert data["data"]["role"] == "admin"

    def test_auth_status_endpoint(self, client, tmp_path):
        """認証状態取得"""
        auth_file = tmp_path / "auth.yaml"
        reset_user_store()

        store = UserStore(auth_file)
        with patch(
            "autotrader.web.auth.router.get_user_store",
            return_value=store,
        ):
            response = client.get("/api/v1/auth/status")

        assert response.status_code == 200
        data = response.json()
        assert "auth_disabled" in data["data"]
        assert "user_count" in data["data"]
        assert "secret_configured" in data["data"]


class TestAuthDisabled:
    """認証無効化モードのテスト"""

    def test_auth_disabled_returns_dummy_user(
        self, monkeypatch
    ):
        """auth_disabled=True でダミーユーザーを返す"""
        from autotrader.web.auth.config import (
            get_auth_settings,
            AuthSettings,
        )
        from autotrader.web.auth.dependencies import (
            get_current_user,
            _DUMMY_USER,
        )

        # auth_disabled=True の設定をモック
        mock_settings = MagicMock()
        mock_settings.auth_disabled = True

        with patch(
            "autotrader.web.auth.dependencies.get_auth_settings",
            return_value=mock_settings,
        ):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                get_current_user(None)
            )

        assert result == _DUMMY_USER
        assert result["role"] == "admin"
