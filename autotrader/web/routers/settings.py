"""設定ルーター"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from autotrader.web.auth.dependencies import require_admin
from autotrader.web.schemas import ApiResponse, SettingsResponse
from autotrader.web.schemas.requests import SettingsUpdateRequest
from autotrader.web.services.settings_service import (
    SettingsService,
    get_settings_service,
)

router = APIRouter()


@router.get(
    "/settings",
    response_model=ApiResponse[SettingsResponse],
)
async def get_settings(
    service: SettingsService = Depends(
        get_settings_service
    ),
) -> ApiResponse[SettingsResponse]:
    """現在の設定を取得

    読み取り専用のため認証不要。

    Args:
        service: 設定サービス

    Returns:
        ApiResponse[SettingsResponse]: 設定情報
    """
    current_settings = service.get_current_settings()
    return ApiResponse(data=current_settings)


@router.put(
    "/settings",
    response_model=ApiResponse[SettingsResponse],
)
async def update_settings(
    request: SettingsUpdateRequest,
    user: Annotated[dict[str, any], Depends(require_admin)],
    service: SettingsService = Depends(
        get_settings_service
    ),
) -> ApiResponse[SettingsResponse]:
    """設定を更新

    管理者権限が必要。

    Args:
        request: 更新リクエスト
        user: 認証済みユーザー（管理者）
        service: 設定サービス

    Returns:
        ApiResponse[SettingsResponse]: 更新後の設定
    """
    updated_settings = service.update_settings(request)
    return ApiResponse(data=updated_settings)
