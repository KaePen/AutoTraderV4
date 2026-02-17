"""設定ルーター"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from autotrader.web.dependencies import get_app_settings
from autotrader.web.schemas import ApiResponse, SettingsResponse
from autotrader.web.schemas.requests import SettingsUpdateRequest
from autotrader.web.services.settings_service import SettingsService
from autotrader.config.settings import Settings

router = APIRouter()


@router.get("/settings", response_model=ApiResponse[SettingsResponse])
async def get_settings(
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[SettingsResponse]:
    """現在の設定を取得

    Args:
        settings: アプリケーション設定

    Returns:
        ApiResponse[SettingsResponse]: 設定情報
    """
    service = SettingsService(settings)
    current_settings = service.get_current_settings()
    return ApiResponse(data=current_settings)


@router.put("/settings", response_model=ApiResponse[SettingsResponse])
async def update_settings(
    request: SettingsUpdateRequest,
    settings: Settings = Depends(get_app_settings),
) -> ApiResponse[SettingsResponse]:
    """設定を更新

    Args:
        request: 更新リクエスト
        settings: アプリケーション設定

    Returns:
        ApiResponse[SettingsResponse]: 更新後の設定
    """
    service = SettingsService(settings)
    updated_settings = service.update_settings(request)
    return ApiResponse(data=updated_settings)
