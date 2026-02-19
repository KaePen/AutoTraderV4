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


def _dict_to_trade_response(d: dict) -> TradeResponse:
    """エンジンのトレード辞書をTradeResponseに変換

    Args:
        d: トレード辞書

    Returns:
        TradeResponse: レスポンス
    """
    from datetime import datetime, timezone
    from autotrader.core.enums import SignalType

    opened_at = d.get("opened_at")
    if isinstance(opened_at, str):
        opened_at = datetime.fromisoformat(opened_at)
    elif opened_at is None:
        opened_at = datetime.now(timezone.utc)

    closed_at = d.get("closed_at")
    if isinstance(closed_at, str):
        closed_at = datetime.fromisoformat(closed_at)

    signal_type_val = d.get("signal_type", "BUY")
    if isinstance(signal_type_val, str):
        signal_type_val = SignalType(signal_type_val)

    return TradeResponse(
        trade_id=d.get("trade_id", ""),
        ticket=d.get("ticket", 0),
        symbol=d.get("symbol", ""),
        signal_type=signal_type_val,
        volume=d.get("volume", 0.0),
        entry_price=d.get("entry_price", 0.0),
        exit_price=d.get("exit_price"),
        stop_loss=d.get("stop_loss"),
        take_profit=d.get("take_profit"),
        profit_loss=d.get("profit_loss"),
        profit_loss_pips=d.get("profit_loss_pips"),
        exit_reason=d.get("exit_reason"),
        opened_at=opened_at,
        closed_at=closed_at,
    )


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

    ライブエンジン接続中はメモリ上の履歴を返す。
    未接続時はDBから取得する。

    Args:
        request: FastAPIリクエスト
        db: DBセッション
        symbol: 通貨ペア
        limit: 取得件数
        offset: オフセット

    Returns:
        ApiResponse[list[TradeResponse]]: トレード履歴
    """
    # エンジンのインメモリ履歴を優先
    engine = getattr(request.app.state, "live_engine", None)
    if engine is not None and engine.trade_history:
        trades = engine.trade_history
        if symbol:
            trades = [
                t for t in trades
                if t.get("symbol") == symbol
            ]
        trades = trades[offset:offset + limit]
        return ApiResponse(
            data=[
                _dict_to_trade_response(t) for t in trades
            ]
        )

    # フォールバック: DB
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

    ライブエンジン接続中はメモリ上の履歴から集計。
    未接続時はDBから取得する。

    Args:
        request: FastAPIリクエスト
        db: DBセッション
        symbol: 通貨ペア
        days: 集計日数

    Returns:
        ApiResponse[TradeSummaryResponse]: トレードサマリー
    """
    # エンジンのインメモリ履歴から集計
    engine = getattr(request.app.state, "live_engine", None)
    if engine is not None and engine.trade_history:
        trades = engine.trade_history
        if symbol:
            trades = [
                t for t in trades
                if t.get("symbol") == symbol
            ]
        # 決済済みトレードのみ集計
        closed = [
            t for t in trades if t.get("closed_at")
        ]
        total = len(closed)
        wins = [
            t for t in closed
            if (t.get("profit_loss") or 0) > 0
        ]
        losses = [
            t for t in closed
            if (t.get("profit_loss") or 0) < 0
        ]
        total_profit = sum(
            t.get("profit_loss", 0) for t in wins
        )
        total_loss = abs(sum(
            t.get("profit_loss", 0) for t in losses
        ))
        return ApiResponse(
            data=TradeSummaryResponse(
                total_trades=total,
                winning_trades=len(wins),
                losing_trades=len(losses),
                win_rate=(
                    len(wins) / total if total > 0
                    else 0.0
                ),
                total_profit=total_profit,
                total_loss=total_loss,
                net_profit=total_profit - total_loss,
                profit_factor=(
                    total_profit / total_loss
                    if total_loss > 0 else 0.0
                ),
                average_win=(
                    total_profit / len(wins)
                    if wins else 0.0
                ),
                average_loss=(
                    total_loss / len(losses)
                    if losses else 0.0
                ),
            )
        )

    # フォールバック: DB
    service = MarketService(db)
    summary = service.get_trade_summary(symbol, days)
    return ApiResponse(data=summary)
