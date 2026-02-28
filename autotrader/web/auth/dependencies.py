"""認証依存関数

FastAPI Dependsで使用する認証関連の依存関数。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer

from autotrader.web.auth.security import verify_token

# HTTPBearer認証スキーム
_security = HTTPBearer()


async def get_current_user(
    credentials: Annotated[
        str, Depends(lambda x=Depends(_security): x.credentials)
    ],
) -> dict[str, any]:
    """現在のユーザー情報取得

    Args:
        credentials: Bearer トークン

    Returns:
        dict[str, any]: ユーザー情報

    Raises:
        HTTPException: 認証失敗時
    """
    payload = verify_token(credentials)
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
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理者権限が必要です",
        )
    return user
