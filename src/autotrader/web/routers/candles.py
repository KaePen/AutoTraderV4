"""ローソク足ルーター"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from autotrader.core.enums import Timeframe
from autotrader.web.dependencies import get_db
from autotrader.web.schemas import ApiResponse, CandleResponse
from autotrader.web.services.market_service import MarketService

router = APIRouter()


@router.get(
    "/candles/{symbol}/{timeframe}",
    response_model=ApiResponse[list[CandleResponse]],
)
async def get_candles(
    symbol: str = Path(description="通貨ペア"),
    timeframe: Timeframe = Path(description="時間足"),
    limit: int = Query(default=200, ge=1, le=1000, description="取得本数"),
    db: Session = Depends(get_db),
) -> ApiResponse[list[CandleResponse]]:
    """ローソク足データを取得

    Args:
        symbol: 通貨ペア
        timeframe: 時間足
        limit: 取得本数
        db: DBセッション

    Returns:
        ApiResponse[list[CandleResponse]]: ローソク足一覧
    """
    service = MarketService(db)
    candles = service.get_candles(symbol, timeframe, limit)
    return ApiResponse(data=candles)
