"""指標ルーター"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

import pandas as pd

from autotrader.core.enums import Timeframe
from autotrader.web.dependencies import get_db
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
async def get_indicators(
    request: Request,
    symbol: str = Path(description="通貨ペア"),
    timeframe: Timeframe = Path(description="時間足"),
    db: Session = Depends(get_db),
) -> ApiResponse[IndicatorResponse]:
    """指標スナップショットを取得

    MT5接続中はローソク足から指標を計算して返却。
    未接続時はMarketServiceのスタブにフォールバック。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア
        timeframe: 時間足
        db: DBセッション

    Returns:
        ApiResponse[IndicatorResponse]: 指標情報
    """
    engine = getattr(request.app.state, "live_engine", None)
    if engine and engine.connected:
        try:
            # エンジン計算済みデータから取得（MT5再取得不要）
            raw = engine._extract_indicators(timeframe.value)
            if raw:
                ind = IndicatorResponse(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=datetime.now(timezone.utc),
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
async def get_indicator_series(
    request: Request,
    symbol: str = Path(description="通貨ペア"),
    timeframe: Timeframe = Path(description="時間足"),
    limit: int = Query(
        default=500, ge=50, le=1000, description="取得本数"
    ),
    db: Session = Depends(get_db),
) -> ApiResponse[IndicatorSeriesResponse]:
    """チャートオーバーレイ用指標時系列を取得

    EMA(12/26)・ボリンジャーバンド・RSIの時系列データを返す。
    MT5接続中はライブデータから計算、未接続時は空レスポンス。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア
        timeframe: 時間足
        limit: 取得本数
        db: DBセッション

    Returns:
        ApiResponse[IndicatorSeriesResponse]: 指標時系列
    """
    engine = getattr(request.app.state, "live_engine", None)
    if not (engine and engine.connected):
        return ApiResponse(data=IndicatorSeriesResponse())

    try:
        import pandas_ta as ta
    except ImportError:
        return ApiResponse(data=IndicatorSeriesResponse())

    try:
        df = await engine._data_provider.get_candles_from_pos(
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
