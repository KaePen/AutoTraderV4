"""セキュリティユーティリティ

パスワードハッシュ化・検証、JWT生成・検証機能。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from autotrader.web.auth.config import get_auth_settings

# パスワードハッシュ化コンテキスト
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """パスワード検証

    Args:
        plain_password: 平文パスワード
        hashed_password: ハッシュ化パスワード

    Returns:
        bool: 検証結果
    """
    return _pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """パスワードハッシュ化

    Args:
        password: 平文パスワード

    Returns:
        str: ハッシュ化パスワード
    """
    return _pwd_context.hash(password)


def create_access_token(
    data: dict[str, Any], expires_delta: timedelta | None = None
) -> str:
    """JWTアクセストークン生成

    Args:
        data: トークンペイロード
        expires_delta: 有効期限（Noneの場合は設定値を使用）

    Returns:
        str: JWT文字列
    """
    settings = get_auth_settings()
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(
            minutes=settings.access_token_expire_minutes
        )

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings.secret_key, algorithm=settings.algorithm
    )
    return encoded_jwt


def verify_token(token: str) -> dict[str, Any] | None:
    """JWTトークン検証

    Args:
        token: JWT文字列

    Returns:
        dict[str, Any] | None: ペイロード（検証失敗時はNone）
    """
    settings = get_auth_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        return payload
    except JWTError:
        return None
