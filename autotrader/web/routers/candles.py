"""ローソク足ルーター"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
)
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
    end_time: str | None = Query(
        default=None,
        description="この時刻より前のデータを取得（ISO8601、排他）",
    ),
    db: Session = Depends(get_db),
    engine=Depends(get_live_engine),
) -> ApiResponse[list[CandleResponse]]:
    """ローソク足データを取得

    MT5接続中はライブデータ、未接続時はCSVフォールバック。
    end_time指定時は指定時刻より前のデータを返す。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア
        timeframe: 時間足
        limit: 取得本数
        end_time: 排他上限時刻（ISO8601文字列）
        db: DBセッション
        engine: LiveTradingEngine

    Returns:
        ApiResponse[list[CandleResponse]]: ローソク足一覧
    """
    # end_time文字列をdatetimeに変換
    end_dt: datetime | None = None
    if end_time:
        try:
            end_dt = datetime.fromisoformat(
                end_time.replace("Z", "+00:00")
            )
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"end_timeの形式が不正: {end_time}",
            )

    # MT5接続中はライブデータを取得
    if engine and engine.connected:
        try:
            if end_dt:
                df = await engine.get_candles_before(
                    symbol, timeframe, end_dt, limit
                )
            else:
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
    candles = service.get_candles(
        symbol, timeframe, limit, end_time=end_dt
    )
    return ApiResponse(data=candles)
