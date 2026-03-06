"""ポジションルーター"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from autotrader.core.enums import SignalType
from autotrader.web.dependencies import (
    get_db,
    get_engine_manager,
    get_live_engine,
)
from autotrader.web.middleware import limiter
from autotrader.web.schemas import ApiResponse, PositionResponse
from autotrader.web.services.market_service import MarketService

router = APIRouter()


def _dict_to_position_response(
    d: dict,
    db_opened_at_map: dict[int, datetime] | None = None,
) -> PositionResponse:
    """エンジンのポジション辞書をPositionResponseに変換

    Args:
        d: ポジション辞書
        db_opened_at_map: DB由来のticket→opened_atマップ

    Returns:
        PositionResponse: レスポンス
    """

    opened_at = d.get("opened_at")
    if isinstance(opened_at, str):
        opened_at = datetime.fromisoformat(opened_at)
    elif opened_at is None:
        opened_at = datetime.now(UTC)

    # DB由来のopened_atで上書き（永続的なソース）
    ticket = d.get("ticket", 0)
    if db_opened_at_map and ticket in db_opened_at_map:
        opened_at = db_opened_at_map[ticket]

    # elapsed_minutesが未計算の場合、opened_atから計算
    elapsed_minutes = d.get("elapsed_minutes")
    if elapsed_minutes is None and opened_at is not None:
        now = datetime.now(UTC)
        # naive datetimeの場合UTCとして扱う
        if opened_at.tzinfo is None:
            from datetime import timezone

            opened_at = opened_at.replace(
                tzinfo=timezone.utc
            )
        elapsed_sec = (now - opened_at).total_seconds()
        elapsed_minutes = max(0, int(elapsed_sec / 60))

    signal_type_val = d.get("signal_type", "BUY")
    if isinstance(signal_type_val, str):
        signal_type_val = SignalType(signal_type_val)

    return PositionResponse(
        position_id=d.get("position_id", ""),
        ticket=ticket,
        trade_id=d.get("trade_id", ""),
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
        signal_id=d.get("signal_id"),
        regime=d.get("regime"),
        mode=d.get("mode"),
        consensus_score=d.get("consensus_score"),
        remaining_minutes=d.get("remaining_minutes"),
        max_hold_minutes=d.get("max_hold_minutes"),
        elapsed_minutes=elapsed_minutes,
    )


@router.get(
    "/positions",
    response_model=ApiResponse[list[PositionResponse]],
)
@limiter.limit("60/minute")
async def get_positions(
    request: Request,
    db: Session = Depends(get_db),
    symbol: str | None = Query(
        default=None,
        description="通貨ペア（指定なしで全て）",
    ),
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[list[PositionResponse]]:
    """オープンポジションを取得

    EngineManager経由で全エンジンからポジションを集約。

    Args:
        request: FastAPIリクエスト
        db: DBセッション
        symbol: 通貨ペア
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[list[PositionResponse]]: ポジション一覧
    """
    # DBからオープンポジションのopened_atマップを取得
    db_opened_at_map = _get_db_opened_at_map(db)

    # EngineManager経由で全エンジンから集約
    if mgr and mgr.engines:
        positions = mgr.all_cached_positions
        if symbol:
            positions = [
                p for p in positions
                if p.get("symbol") == symbol
            ]
        return ApiResponse(
            data=[
                _dict_to_position_response(p, db_opened_at_map)
                for p in positions
            ]
        )

    # 後方互換: 単一エンジン
    if engine is not None and engine.running:
        positions = engine.cached_positions
        if symbol:
            positions = [
                p for p in positions
                if p.get("symbol") == symbol
            ]
        return ApiResponse(
            data=[
                _dict_to_position_response(p, db_opened_at_map)
                for p in positions
            ]
        )

    # フォールバック: DB
    service = MarketService(db)
    positions = service.get_positions(symbol)
    return ApiResponse(data=positions)


def _get_db_opened_at_map(db: Session) -> dict[int, datetime]:
    """DBからオープンポジションのticket→opened_atマップを取得

    Args:
        db: DBセッション

    Returns:
        dict[int, datetime]: ticket→opened_at
    """
    from autotrader.adapters.database.models import (
        TradeRecord,
    )

    try:
        records = (
            db.query(
                TradeRecord.ticket, TradeRecord.opened_at
            )
            .filter(TradeRecord.is_open.is_(True))
            .all()
        )
        return {
            r.ticket: r.opened_at
            for r in records
            if r.ticket is not None and r.opened_at is not None
        }
    except Exception:
        return {}
