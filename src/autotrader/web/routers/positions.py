"""ポジションルーター"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from autotrader.web.dependencies import get_db
from autotrader.web.schemas import ApiResponse, PositionResponse
from autotrader.web.services.market_service import MarketService

router = APIRouter()


def _dict_to_position_response(d: dict) -> PositionResponse:
    """エンジンのポジション辞書をPositionResponseに変換

    Args:
        d: ポジション辞書

    Returns:
        PositionResponse: レスポンス
    """
    from datetime import datetime, timezone
    from autotrader.core.enums import SignalType

    opened_at = d.get("opened_at")
    if isinstance(opened_at, str):
        opened_at = datetime.fromisoformat(opened_at)
    elif opened_at is None:
        opened_at = datetime.now(timezone.utc)

    signal_type_val = d.get("signal_type", "BUY")
    if isinstance(signal_type_val, str):
        signal_type_val = SignalType(signal_type_val)

    return PositionResponse(
        position_id=d.get("position_id", ""),
        ticket=d.get("ticket", 0),
        symbol=d.get("symbol", ""),
        signal_type=signal_type_val,
        volume=d.get("volume", 0.0),
        entry_price=d.get("entry_price", 0.0),
        current_price=d.get("current_price", 0.0),
        stop_loss=d.get("stop_loss"),
        take_profit=d.get("take_profit"),
        opened_at=opened_at,
        unrealized_pnl=d.get("unrealized_pnl", 0.0),
        unrealized_pnl_pips=d.get(
            "unrealized_pnl_pips", 0.0
        ),
    )


@router.get(
    "/positions",
    response_model=ApiResponse[list[PositionResponse]],
)
async def get_positions(
    request: Request,
    db: Session = Depends(get_db),
    symbol: str | None = Query(
        default=None,
        description="通貨ペア（指定なしで全て）",
    ),
) -> ApiResponse[list[PositionResponse]]:
    """オープンポジションを取得

    ライブエンジン接続中はキャッシュ済みポジションを返す。
    未接続時はDBから取得する。

    Args:
        request: FastAPIリクエスト
        db: DBセッション
        symbol: 通貨ペア

    Returns:
        ApiResponse[list[PositionResponse]]: ポジション一覧
    """
    # エンジンのキャッシュを優先
    engine = getattr(request.app.state, "live_engine", None)
    if engine is not None and engine.running:
        positions = engine.cached_positions
        if symbol:
            positions = [
                p for p in positions
                if p.get("symbol") == symbol
            ]
        return ApiResponse(
            data=[
                _dict_to_position_response(p)
                for p in positions
            ]
        )

    # フォールバック: DB
    service = MarketService(db)
    positions = service.get_positions(symbol)
    return ApiResponse(data=positions)
