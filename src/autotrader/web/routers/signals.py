"""シグナルルーター"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autotrader.web.dependencies import get_db
from autotrader.web.schemas import ApiResponse, SignalResponse
from autotrader.web.services.signal_service import SignalService

router = APIRouter()


@router.get("/signals/current", response_model=ApiResponse[list[SignalResponse]])
async def get_current_signals(
    db: Session = Depends(get_db),
    symbol: str = Query(default="USDJPY", description="通貨ペア"),
) -> ApiResponse[list[SignalResponse]]:
    """現在のシグナルを取得

    Args:
        db: DBセッション
        symbol: 通貨ペア

    Returns:
        ApiResponse[list[SignalResponse]]: シグナル一覧
    """
    service = SignalService(db)
    signals = service.get_current_signals(symbol)
    return ApiResponse(data=signals)


@router.get("/signals/history", response_model=ApiResponse[list[SignalResponse]])
async def get_signal_history(
    db: Session = Depends(get_db),
    symbol: str = Query(default="USDJPY", description="通貨ペア"),
    limit: int = Query(default=50, ge=1, le=500, description="取得件数"),
    offset: int = Query(default=0, ge=0, description="オフセット"),
) -> ApiResponse[list[SignalResponse]]:
    """シグナル履歴を取得

    Args:
        db: DBセッション
        symbol: 通貨ペア
        limit: 取得件数
        offset: オフセット

    Returns:
        ApiResponse[list[SignalResponse]]: シグナル履歴
    """
    service = SignalService(db)
    signals = service.get_signal_history(symbol, limit, offset)
    return ApiResponse(data=signals)
