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


def _calc_indicators(
    df: pd.DataFrame,
    symbol: str,
    timeframe: Timeframe,
) -> IndicatorResponse:
    """DataFrameからテクニカル指標を計算

    Args:
        df: OHLCVデータフレーム
        symbol: 通貨ペア
        timeframe: 時間足

    Returns:
        IndicatorResponse: 指標レスポンス
    """
    try:
        import pandas_ta as ta
    except ImportError:
        return IndicatorResponse(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(timezone.utc),
        )

    if len(df) < 30:
        return IndicatorResponse(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(timezone.utc),
        )

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # RSI
    rsi_s = ta.rsi(close, length=14)
    rsi_val = (
        float(rsi_s.iloc[-1])
        if rsi_s is not None and not rsi_s.empty
        else None
    )

    # MACD
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    macd_val = None
    macd_signal_val = None
    macd_hist_val = None
    if macd_df is not None and not macd_df.empty:
        cols = macd_df.columns.tolist()
        macd_val = float(macd_df[cols[0]].iloc[-1])
        macd_signal_val = float(macd_df[cols[1]].iloc[-1])
        macd_hist_val = float(macd_df[cols[2]].iloc[-1])

    # ADX / +DI / -DI
    adx_df = ta.adx(high, low, close, length=14)
    adx_val = None
    plus_di_val = None
    minus_di_val = None
    if adx_df is not None and not adx_df.empty:
        cols = adx_df.columns.tolist()
        # 標準: ADX_14, DMP_14, DMN_14
        adx_val = float(adx_df[cols[0]].iloc[-1])
        plus_di_val = float(adx_df[cols[1]].iloc[-1])
        minus_di_val = float(adx_df[cols[2]].iloc[-1])

    # ボリンジャーバンド
    bb_df = ta.bbands(close, length=20, std=2.0)
    bb_upper = None
    bb_middle = None
    bb_lower = None
    if bb_df is not None and not bb_df.empty:
        cols = bb_df.columns.tolist()
        # 標準: BBL, BBM, BBU, BBB, BBP
        bb_lower = float(bb_df[cols[0]].iloc[-1])
        bb_middle = float(bb_df[cols[1]].iloc[-1])
        bb_upper = float(bb_df[cols[2]].iloc[-1])

    # ATR
    atr_s = ta.atr(high, low, close, length=14)
    atr_val = (
        float(atr_s.iloc[-1])
        if atr_s is not None and not atr_s.empty
        else None
    )

    # EMA
    ema_fast_s = ta.ema(close, length=12)
    ema_slow_s = ta.ema(close, length=26)
    ema_fast_val = (
        float(ema_fast_s.iloc[-1])
        if ema_fast_s is not None and not ema_fast_s.empty
        else None
    )
    ema_slow_val = (
        float(ema_slow_s.iloc[-1])
        if ema_slow_s is not None and not ema_slow_s.empty
        else None
    )

    # NaN チェック
    def _clean(v):
        if v is None:
            return None
        if pd.isna(v):
            return None
        return v

    timestamp = (
        df["time"].iloc[-1]
        if "time" in df.columns
        else datetime.now(timezone.utc)
    )

    return IndicatorResponse(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        rsi=_clean(rsi_val),
        macd=_clean(macd_val),
        macd_signal=_clean(macd_signal_val),
        macd_hist=_clean(macd_hist_val),
        adx=_clean(adx_val),
        plus_di=_clean(plus_di_val),
        minus_di=_clean(minus_di_val),
        bb_upper=_clean(bb_upper),
        bb_middle=_clean(bb_middle),
        bb_lower=_clean(bb_lower),
        atr=_clean(atr_val),
        ema_fast=_clean(ema_fast_val),
        ema_slow=_clean(ema_slow_val),
    )


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
            # 指標計算に十分な本数を取得
            df = await engine._data_provider.get_candles_from_pos(
                symbol, timeframe, 100
            )
            if not df.empty:
                indicators = _calc_indicators(
                    df, symbol, timeframe
                )
                return ApiResponse(data=indicators)
        except Exception as e:
            logger.warning(
                "MT5指標計算失敗、スタブフォールバック: %s",
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
