"""ローソク足ルーター"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from autotrader.core.enums import Timeframe
from autotrader.web.dependencies import get_db, get_live_engine
from autotrader.web.schemas import ApiResponse, CandleResponse
from autotrader.web.services.market_service import MarketService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/candles/{symbol}/{timeframe}",
    response_model=ApiResponse[list[CandleResponse]],
)
async def get_candles(
    request: Request,
    symbol: str = Path(description="通貨ペア"),
    timeframe: Timeframe = Path(description="時間足"),
    limit: int = Query(
        default=200, ge=1, le=1000, description="取得本数"
    ),
    db: Session = Depends(get_db),
    engine=Depends(get_live_engine),
) -> ApiResponse[list[CandleResponse]]:
    """ローソク足データを取得

    MT5接続中はライブデータ、未接続時はCSVフォールバック。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア
        timeframe: 時間足
        limit: 取得本数
        db: DBセッション
        engine: LiveTradingEngine

    Returns:
        ApiResponse[list[CandleResponse]]: ローソク足一覧
    """
    # MT5接続中はライブデータを取得
    if engine and engine.connected:
        try:
            df = await engine.get_candles(
                symbol, timeframe, limit
            )
            if not df.empty:
                candles = [
                    CandleResponse(
                        symbol=symbol,
                        timeframe=timeframe,
                        time=row["time"],
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(
                            row.get("volume", 0)
                        ),
                    )
                    for _, row in df.iterrows()
                ]
                return ApiResponse(data=candles)
        except Exception as e:
            logger.warning(
                "MT5ローソク足取得失敗、CSVフォールバック: %s",
                e,
            )

    # フォールバック: CSV
    service = MarketService(db)
    candles = service.get_candles(symbol, timeframe, limit)
    return ApiResponse(data=candles)
