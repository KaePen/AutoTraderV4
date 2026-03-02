"""認証依存関数

FastAPI Dependsで使用する認証関連の依存関数。
auth_disabled=True の場合は認証をスキップする。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from autotrader.web.auth.config import get_auth_settings
from autotrader.web.auth.security import verify_token

# HTTPBearer認証スキーム（auto_error=False で認証なしリクエストも許可）
_security = HTTPBearer(auto_error=False)

# 開発用ダミーユーザー（auth_disabled時に使用）
_DUMMY_USER: dict[str, any] = {
    "username": "dev_user",
    "role": "admin",
}


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_security),
    ],
) -> dict[str, any]:
    """現在のユーザー情報取得

    auth_disabled=True の場合は認証をスキップしダミーユーザーを返す。

    Args:
        credentials: Bearer トークン

    Returns:
        dict[str, any]: ユーザー情報

    Raises:
        HTTPException: 認証失敗時（auth_disabled=Falseの場合）
    """
    settings = get_auth_settings()

    # 認証無効時はダミーユーザーを返す
    if settings.auth_disabled:
        return _DUMMY_USER

    # トークンがない場合
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証が必要です",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # トークン検証
    payload = verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="認証に失敗しました",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str | None = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="トークンが不正です",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "username": username,
        "role": payload.get("role", "user"),
    }


async def require_admin(
    user: Annotated[dict[str, any], Depends(get_current_user)],
) -> dict[str, any]:
    """管理者権限要求

    Args:
        user: ユーザー情報

    Returns:
        dict[str, any]: ユーザー情報

    Raises:
        HTTPException: 管理者権限がない場合
    """
    settings = get_auth_settings()

    # 認証無効時は常に許可
    if settings.auth_disabled:
        return _DUMMY_USER

    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理者権限が必要です",
        )
    return user


async def get_optional_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_security),
    ],
) -> dict[str, any] | None:
    """オプショナルなユーザー情報取得

    認証がなくてもエラーにならない。
    読み取り専用エンドポイント向け。

    Args:
        credentials: Bearer トークン（オプショナル）

    Returns:
        dict[str, any] | None: ユーザー情報（未認証時はNone）
    """
    settings = get_auth_settings()

    # 認証無効時はダミーユーザーを返す
    if settings.auth_disabled:
        return _DUMMY_USER

    # トークンがない場合はNone
    if credentials is None:
        return None

    # トークン検証
    payload = verify_token(credentials.credentials)
    if payload is None:
        return None

    username: str | None = payload.get("sub")
    if username is None:
        return None

    return {
        "username": username,
        "role": payload.get("role", "user"),
    }
