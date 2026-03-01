"""認証ルーター

ログイン・トークン発行・ユーザー情報取得エンドポイント。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from autotrader.web.auth.config import get_auth_settings
from autotrader.web.auth.dependencies import get_current_user
from autotrader.web.auth.security import create_access_token
from autotrader.web.auth.user_store import get_user_store
from autotrader.web.middleware import limiter
from autotrader.web.schemas import ApiResponse

logger = logging.getLogger(__name__)

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


class SetupRequest(BaseModel):
    """初回セットアップリクエスト

    Attributes:
        username: 管理者ユーザー名
        password: 管理者パスワード
    """

    username: str
    password: str


class SetupResponse(BaseModel):
    """セットアップレスポンス

    Attributes:
        message: 結果メッセージ
        username: 作成されたユーザー名
    """

    message: str
    username: str


@router.post(
    "/token", response_model=ApiResponse[TokenResponse]
)
@limiter.limit("5/minute")
async def login(
    request: Request,
    form: TokenRequest,
) -> ApiResponse[TokenResponse]:
    """ログイン・トークン発行

    YAMLベースのユーザーストアで認証を行う。

    Args:
        request: FastAPIリクエスト
        form: ログインフォーム

    Returns:
        ApiResponse[TokenResponse]: アクセストークン

    Raises:
        HTTPException: 認証失敗時
    """
    store = get_user_store()
    user = store.authenticate(form.username, form.password)

    if user is None:
        logger.warning(
            "ログイン失敗: username=%s", form.username
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザー名またはパスワードが正しくありません",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # トークン発行
    access_token = create_access_token(
        data={"sub": user["username"], "role": user["role"]}
    )

    logger.info("ログイン成功: username=%s", user["username"])
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


@router.get("/status")
async def get_auth_status() -> ApiResponse[dict]:
    """認証システムの状態を取得

    Returns:
        ApiResponse: 認証状態情報
    """
    settings = get_auth_settings()
    store = get_user_store()

    return ApiResponse(
        data={
            "auth_disabled": settings.auth_disabled,
            "user_count": len(store.list_users()),
            "secret_configured": (
                settings.secret_key
                != "changeme-insecure-secret"
            ),
        }
    )


@router.post(
    "/setup", response_model=ApiResponse[SetupResponse]
)
async def initial_setup(
    form: SetupRequest,
) -> ApiResponse[SetupResponse]:
    """初回セットアップ

    ユーザーが存在しない場合のみ、管理者ユーザーを作成する。
    既にユーザーが存在する場合は403エラー。

    Args:
        form: セットアップリクエスト

    Returns:
        ApiResponse[SetupResponse]: セットアップ結果

    Raises:
        HTTPException: 既にユーザーが存在する場合
    """
    store = get_user_store()

    # 既存ユーザーがいる場合はセットアップ不可
    if len(store.list_users()) > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="セットアップは既に完了しています",
        )

    # 管理者ユーザーを作成
    store.add_user(
        username=form.username,
        password=form.password,
        role="admin",
    )

    logger.info(
        "初回セットアップ完了: username=%s", form.username
    )
    return ApiResponse(
        data=SetupResponse(
            message="セットアップが完了しました",
            username=form.username,
        )
    )
