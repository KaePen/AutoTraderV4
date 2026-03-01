"""指標ルーター"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd
from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from autotrader.core.enums import Timeframe
from autotrader.web.dependencies import (
    get_db,
    get_engine_manager,
    get_live_engine,
)
from autotrader.web.middleware import limiter
from autotrader.web.schemas import (
    ApiResponse,
    IndicatorResponse,
    IndicatorSeriesResponse,
)
from autotrader.web.schemas.responses import IndicatorPoint
from autotrader.web.services.market_service import MarketService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/indicators/{symbol}/{timeframe}",
    response_model=ApiResponse[IndicatorResponse],
)
@limiter.limit("60/minute")
async def get_indicators(
    request: Request,
    symbol: str = Path(description="通貨ペア"),
    timeframe: Timeframe = Path(description="時間足"),
    db: Session = Depends(get_db),
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[IndicatorResponse]:
    """指標スナップショットを取得

    EngineManager経由でシンボル別エンジンからデータ取得。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア
        timeframe: 時間足
        db: DBセッション
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[IndicatorResponse]: 指標情報
    """
    # シンボル別エンジンを取得
    if mgr:
        target = mgr.get_engine(symbol)
        if target:
            engine = target

    if engine and engine.connected:
        try:
            raw = engine.get_indicators(timeframe.value)
            if raw:
                ind = IndicatorResponse(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=datetime.now(UTC),
                    rsi=raw.get("rsi"),
                    macd=raw.get("macd"),
                    macd_signal=raw.get("macd_signal"),
                    macd_hist=raw.get("macd_hist"),
                    adx=raw.get("adx"),
                    plus_di=raw.get("plus_di"),
                    minus_di=raw.get("minus_di"),
                    bb_upper=raw.get("bb_upper"),
                    bb_middle=raw.get("bb_middle"),
                    bb_lower=raw.get("bb_lower"),
                    atr=raw.get("atr"),
                    ema_fast=raw.get("ema_fast"),
                    ema_slow=raw.get("ema_slow"),
                )
                return ApiResponse(data=ind)
        except Exception as e:
            logger.warning(
                "エンジン指標取得失敗、スタブフォールバック: %s",
                e,
            )

    # フォールバック
    service = MarketService(db)
    indicators = service.get_indicators(symbol, timeframe)
    return ApiResponse(data=indicators)


@router.get(
    "/indicators/{symbol}/{timeframe}/series",
    response_model=ApiResponse[IndicatorSeriesResponse],
)
@limiter.limit("60/minute")
async def get_indicator_series(
    request: Request,
    symbol: str = Path(description="通貨ペア"),
    timeframe: Timeframe = Path(description="時間足"),
    limit: int = Query(
        default=500, ge=50, le=1000, description="取得本数"
    ),
    db: Session = Depends(get_db),
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[IndicatorSeriesResponse]:
    """チャートオーバーレイ用指標時系列を取得

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア
        timeframe: 時間足
        limit: 取得本数
        db: DBセッション
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[IndicatorSeriesResponse]: 指標時系列
    """
    # シンボル別エンジンを取得
    if mgr:
        target = mgr.get_engine(symbol)
        if target:
            engine = target
    if not (engine and engine.connected):
        return ApiResponse(data=IndicatorSeriesResponse())

    try:
        import pandas_ta as ta
    except ImportError:
        return ApiResponse(data=IndicatorSeriesResponse())

    try:
        df = await engine.get_candles(
            symbol, timeframe, limit
        )
    except Exception as e:
        logger.warning("指標時系列取得失敗: %s", e)
        return ApiResponse(data=IndicatorSeriesResponse())

    if df.empty or len(df) < 30:
        return ApiResponse(data=IndicatorSeriesResponse())

    # UNIX秒タイムスタンプ列
    times = (
        df["time"]
        .apply(lambda t: t.timestamp() if hasattr(t, "timestamp") else float(t))
        .tolist()
    )

    def _series(s) -> list[IndicatorPoint]:
        """pandas Seriesを IndicatorPoint リストに変換"""
        if s is None or s.empty:
            return []
        return [
            IndicatorPoint(time=times[i], value=float(v))
            for i, v in enumerate(s)
            if pd.notna(v)
        ]

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"] if "volume" in df.columns else None

    ema12 = _series(ta.ema(close, length=12))
    ema26 = _series(ta.ema(close, length=26))
    ema50 = _series(ta.ema(close, length=50))
    ema200 = _series(ta.ema(close, length=200))

    bb_df = ta.bbands(close, length=20, std=2.0)
    bb_upper, bb_middle, bb_lower = [], [], []
    if bb_df is not None and not bb_df.empty:
        cols = bb_df.columns.tolist()
        bb_lower = _series(bb_df[cols[0]])
        bb_middle = _series(bb_df[cols[1]])
        bb_upper = _series(bb_df[cols[2]])

    rsi = _series(ta.rsi(close, length=14))

    # VWAP: (high+low+close)/3 * volume の累積 / volume累積
    vwap: list[IndicatorPoint] = []
    if volume is not None and not volume.empty:
        typical = (high + low + close) / 3
        cum_tpv = (typical * volume).cumsum()
        cum_vol = volume.cumsum()
        vwap_s = cum_tpv / cum_vol.replace(0, pd.NA)
        vwap = _series(vwap_s)

    return ApiResponse(
        data=IndicatorSeriesResponse(
            ema12=ema12,
            ema26=ema26,
            ema50=ema50,
            ema200=ema200,
            bb_upper=bb_upper,
            bb_middle=bb_middle,
            bb_lower=bb_lower,
            rsi=rsi,
            vwap=vwap,
        )
    )
