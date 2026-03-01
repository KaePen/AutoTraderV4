"""設定ルーター"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from autotrader.web.middleware import limiter
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
@limiter.limit("60/minute")
async def get_settings(
    request: Request,
    service: SettingsService = Depends(
        get_settings_service
    ),
) -> ApiResponse[SettingsResponse]:
    """現在の設定を取得

    Args:
        request: FastAPIリクエスト
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
@limiter.limit("20/minute")
async def update_settings(
    http_request: Request,
    request: SettingsUpdateRequest,
    service: SettingsService = Depends(
        get_settings_service
    ),
) -> ApiResponse[SettingsResponse]:
    """設定を更新

    Args:
        http_request: FastAPIリクエスト
        request: 更新リクエスト
        service: 設定サービス

    Returns:
        ApiResponse[SettingsResponse]: 更新後の設定
    """
    updated_settings = service.update_settings(request)
    return ApiResponse(data=updated_settings)
