"""ダッシュボードルーター"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autotrader.web.dependencies import get_db
from autotrader.web.schemas import ApiResponse, DashboardResponse
from autotrader.web.schemas.responses import AccountInfoResponse
from autotrader.web.services.market_service import MarketService

router = APIRouter()


@router.get("/dashboard", response_model=ApiResponse[DashboardResponse])
async def get_dashboard(
    db: Session = Depends(get_db),
) -> ApiResponse[DashboardResponse]:
    """ダッシュボード情報を取得

    Args:
        db: DBセッション

    Returns:
        ApiResponse[DashboardResponse]: ダッシュボード情報
    """
    service = MarketService(db)
    dashboard_data = service.get_dashboard()
    return ApiResponse(data=dashboard_data)
