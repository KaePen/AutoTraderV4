"""ダッシュボードルーター"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from autotrader.web.dependencies import get_db, get_live_engine
from autotrader.web.schemas import ApiResponse, DashboardResponse
from autotrader.web.schemas.responses import AccountInfoResponse
from autotrader.web.services.market_service import MarketService

logger = logging.getLogger(__name__)

router = APIRouter()


def _account_from_engine(engine) -> AccountInfoResponse | None:
    """エンジンからリアル口座情報を取得

    Args:
        engine: LiveTradingEngine

    Returns:
        AccountInfoResponse | None: 口座情報（未接続時None）
    """
    if not engine or not engine.connected:
        return None
    acct = engine.account_info
    if not acct:
        return None
    return AccountInfoResponse(
        balance=acct.balance,
        equity=acct.equity,
        margin=acct.margin,
        free_margin=acct.free_margin,
        margin_level=acct.margin_level,
        profit=acct.profit,
        login=acct.login,
        server=acct.server,
        name=acct.name,
        currency=acct.currency,
        leverage=acct.leverage,
    )


@router.get("/dashboard", response_model=ApiResponse[DashboardResponse])
async def get_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    engine=Depends(get_live_engine),
) -> ApiResponse[DashboardResponse]:
    """ダッシュボード情報を取得

    MT5接続中はリアル口座情報を使用し、
    未接続時はMarketServiceのデフォルト値にフォールバック。

    Args:
        request: FastAPIリクエスト
        db: DBセッション
        engine: LiveTradingEngine

    Returns:
        ApiResponse[DashboardResponse]: ダッシュボード情報
    """
    # MT5ライブデータを優先
    live_account = _account_from_engine(engine)

    service = MarketService(db)
    dashboard_data = service.get_dashboard(
        account_override=live_account
    )
    return ApiResponse(data=dashboard_data)
