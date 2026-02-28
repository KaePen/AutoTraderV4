"""認証設定モジュール"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """認証設定

    Attributes:
        secret_key: JWT署名用シークレットキー
        algorithm: JWTアルゴリズム
        access_token_expire_minutes: トークン有効期限（分）
        default_username: デフォルトユーザー名
        default_password_hash: デフォルトパスワードハッシュ
    """

    model_config = SettingsConfigDict(
        env_prefix="AUTOTRADER_AUTH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = Field(
        default="changeme-insecure-secret",
        description="JWT署名用シークレットキー（本番環境では必ず変更）",
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24時間
    default_username: str = "admin"
    default_password_hash: str = Field(
        default=(
            "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/Lw0k"
            "XqP8Q8F8Y7QKq"
        ),
        description="デフォルトパスワード: admin",
    )


@lru_cache
def get_auth_settings() -> AuthSettings:
    """認証設定シングルトンを取得

    Returns:
        AuthSettings: 認証設定インスタンス
    """
    return AuthSettings()
