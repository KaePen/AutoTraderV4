"""認証ルーター

ログイン・トークン発行・ユーザー情報取得エンドポイント。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from autotrader.web.auth.config import get_auth_settings
from autotrader.web.auth.dependencies import get_current_user
from autotrader.web.auth.security import (
    create_access_token,
    verify_password,
)
from autotrader.web.middleware import limiter
from autotrader.web.schemas import ApiResponse

router = APIRouter(prefix="/auth")


class TokenRequest(BaseModel):
    """トークンリクエスト

    Attributes:
        username: ユーザー名
        password: パスワード
    """

    username: str
    password: str


class TokenResponse(BaseModel):
    """トークンレスポンス

    Attributes:
        access_token: アクセストークン
        token_type: トークンタイプ
    """

    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """ユーザー情報レスポンス

    Attributes:
        username: ユーザー名
        role: ロール
    """

    username: str
    role: str


@router.post(
    "/token", response_model=ApiResponse[TokenResponse]
)
@limiter.limit("5/minute")
async def login(
    request: Request,
    form: TokenRequest,
) -> ApiResponse[TokenResponse]:
    """ログイン・トークン発行

    Args:
        request: FastAPIリクエスト
        form: ログインフォーム

    Returns:
        ApiResponse[TokenResponse]: アクセストークン

    Raises:
        HTTPException: 認証失敗時
    """
    settings = get_auth_settings()

    # 簡易認証（本番では DB から取得）
    if form.username != settings.default_username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザー名またはパスワードが正しくありません",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(
        form.password, settings.default_password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザー名またはパスワードが正しくありません",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # トークン発行
    access_token = create_access_token(
        data={"sub": form.username, "role": "admin"}
    )

    return ApiResponse(
        data=TokenResponse(access_token=access_token)
    )


@router.get("/me", response_model=ApiResponse[UserResponse])
@limiter.limit("60/minute")
async def get_me(
    request: Request,
    user: Annotated[dict[str, any], Depends(get_current_user)],
) -> ApiResponse[UserResponse]:
    """現在のユーザー情報取得

    Args:
        request: FastAPIリクエスト
        user: ユーザー情報

    Returns:
        ApiResponse[UserResponse]: ユーザー情報
    """
    return ApiResponse(
        data=UserResponse(
            username=user["username"], role=user["role"]
        )
    )
