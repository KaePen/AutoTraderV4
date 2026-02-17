"""ポジションルーター"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autotrader.web.dependencies import get_db
from autotrader.web.schemas import ApiResponse, PositionResponse
from autotrader.web.services.market_service import MarketService

router = APIRouter()


@router.get("/positions", response_model=ApiResponse[list[PositionResponse]])
async def get_positions(
    db: Session = Depends(get_db),
    symbol: str | None = Query(default=None, description="通貨ペア（指定なしで全て）"),
) -> ApiResponse[list[PositionResponse]]:
    """オープンポジションを取得

    Args:
        db: DBセッション
        symbol: 通貨ペア

    Returns:
        ApiResponse[list[PositionResponse]]: ポジション一覧
    """
    service = MarketService(db)
    positions = service.get_positions(symbol)
    return ApiResponse(data=positions)
