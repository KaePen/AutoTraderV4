"""トレードルーター"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from autotrader.web.dependencies import get_db
from autotrader.web.schemas import (
    ApiResponse,
    TradeResponse,
    TradeSummaryResponse,
)
from autotrader.web.services.market_service import MarketService

router = APIRouter()


@router.get(
    "/trades",
    response_model=ApiResponse[list[TradeResponse]],
)
async def get_trades(
    request: Request,
    db: Session = Depends(get_db),
    symbol: str | None = Query(
        default=None, description="通貨ペア"
    ),
    limit: int = Query(
        default=50, ge=1, le=500, description="取得件数"
    ),
    offset: int = Query(
        default=0, ge=0, description="オフセット"
    ),
) -> ApiResponse[list[TradeResponse]]:
    """トレード履歴を取得

    DBから決済済みトレードを取得する。

    Args:
        request: FastAPIリクエスト
        db: DBセッション
        symbol: 通貨ペア
        limit: 取得件数
        offset: オフセット

    Returns:
        ApiResponse[list[TradeResponse]]: トレード履歴
    """
    service = MarketService(db)
    trades = service.get_trades(symbol, limit, offset)
    return ApiResponse(data=trades)


@router.get(
    "/trades/summary",
    response_model=ApiResponse[TradeSummaryResponse],
)
async def get_trade_summary(
    request: Request,
    db: Session = Depends(get_db),
    symbol: str | None = Query(
        default=None, description="通貨ペア"
    ),
    days: int = Query(
        default=30, ge=1, le=365, description="集計日数"
    ),
) -> ApiResponse[TradeSummaryResponse]:
    """トレードサマリーを取得

    DBから決済済みトレードを集計する。

    Args:
        request: FastAPIリクエスト
        db: DBセッション
        symbol: 通貨ペア
        days: 集計日数

    Returns:
        ApiResponse[TradeSummaryResponse]: トレードサマリー
    """
    service = MarketService(db)
    summary = service.get_trade_summary(symbol, days)
    return ApiResponse(data=summary)
