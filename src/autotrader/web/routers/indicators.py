"""指標ルーター"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from autotrader.core.enums import Timeframe
from autotrader.web.dependencies import get_db
from autotrader.web.schemas import ApiResponse, IndicatorResponse
from autotrader.web.services.market_service import MarketService

router = APIRouter()


@router.get(
    "/indicators/{symbol}/{timeframe}",
    response_model=ApiResponse[IndicatorResponse],
)
async def get_indicators(
    symbol: str = Path(description="通貨ペア"),
    timeframe: Timeframe = Path(description="時間足"),
    db: Session = Depends(get_db),
) -> ApiResponse[IndicatorResponse]:
    """指標スナップショットを取得

    Args:
        symbol: 通貨ペア
        timeframe: 時間足
        db: DBセッション

    Returns:
        ApiResponse[IndicatorResponse]: 指標情報
    """
    service = MarketService(db)
    indicators = service.get_indicators(symbol, timeframe)
    return ApiResponse(data=indicators)
