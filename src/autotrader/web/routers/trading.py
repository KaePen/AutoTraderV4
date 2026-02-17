"""トレーディングルーター

MT5接続管理・自動取引ON/OFFのAPIエンドポイント。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from autotrader.web.schemas import (
    ApiResponse,
    MT5StatusResponse,
    TradingModeResponse,
)
from autotrader.web.schemas.responses import AccountInfoResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trading")


def _get_engine(request: Request):
    """app.stateからエンジンを取得

    Args:
        request: FastAPIリクエスト

    Returns:
        LiveTradingEngine | None: エンジン
    """
    return getattr(request.app.state, "live_engine", None)


@router.get(
    "/mode",
    response_model=ApiResponse[TradingModeResponse],
)
async def get_trading_mode(
    request: Request,
) -> ApiResponse[TradingModeResponse]:
    """現在のトレーディングモード取得

    Args:
        request: FastAPIリクエスト

    Returns:
        ApiResponse[TradingModeResponse]: モード情報
    """
    engine = _get_engine(request)
    if engine:
        return ApiResponse(
            data=TradingModeResponse(
                mode="live",
                label="Live Trading",
                connected=engine.connected,
                auto_trade=engine.enable_auto_trade,
                engine_running=engine.running,
            )
        )
    return ApiResponse(
        data=TradingModeResponse(
            mode="backtest",
            label="Backtest Mode",
        )
    )


@router.get(
    "/mt5/status",
    response_model=ApiResponse[MT5StatusResponse],
)
async def get_mt5_status(
    request: Request,
) -> ApiResponse[MT5StatusResponse]:
    """MT5接続状態取得

    Args:
        request: FastAPIリクエスト

    Returns:
        ApiResponse[MT5StatusResponse]: MT5状態
    """
    engine = _get_engine(request)
    if not engine:
        return ApiResponse(
            data=MT5StatusResponse(connected=False)
        )

    account = None
    if engine.account_info:
        acct = engine.account_info
        account = AccountInfoResponse(
            balance=acct.balance,
            equity=acct.equity,
            margin=acct.margin,
            free_margin=acct.free_margin,
            margin_level=acct.margin_level,
            profit=acct.profit,
        )

    return ApiResponse(
        data=MT5StatusResponse(
            connected=engine.connected,
            transport=engine._config.mt5_config.transport,
            account=account,
        )
    )


@router.post(
    "/mt5/connect",
    response_model=ApiResponse[MT5StatusResponse],
)
async def connect_mt5(
    request: Request,
) -> ApiResponse[MT5StatusResponse]:
    """MT5接続開始

    Args:
        request: FastAPIリクエスト

    Returns:
        ApiResponse[MT5StatusResponse]: 接続結果
    """
    engine = _get_engine(request)
    if not engine:
        return ApiResponse(
            success=False,
            error="ライブエンジンが設定されていません",
            data=MT5StatusResponse(connected=False),
        )

    try:
        await engine.start()
        logger.info("MT5接続成功（API経由）")
    except Exception as e:
        logger.error("MT5接続失敗: %s", e)
        return ApiResponse(
            success=False,
            error=str(e),
            data=MT5StatusResponse(connected=False),
        )

    return await get_mt5_status(request)


@router.post(
    "/mt5/disconnect",
    response_model=ApiResponse[MT5StatusResponse],
)
async def disconnect_mt5(
    request: Request,
) -> ApiResponse[MT5StatusResponse]:
    """MT5切断

    Args:
        request: FastAPIリクエスト

    Returns:
        ApiResponse[MT5StatusResponse]: 切断結果
    """
    engine = _get_engine(request)
    if not engine:
        return ApiResponse(
            data=MT5StatusResponse(connected=False)
        )

    try:
        await engine.stop()
        logger.info("MT5切断成功（API経由）")
    except Exception as e:
        logger.error("MT5切断エラー: %s", e)

    return ApiResponse(
        data=MT5StatusResponse(connected=False)
    )


@router.post(
    "/auto-trade",
    response_model=ApiResponse[TradingModeResponse],
)
async def toggle_auto_trade(
    request: Request,
    enable: bool = False,
) -> ApiResponse[TradingModeResponse]:
    """自動取引ON/OFF

    Args:
        request: FastAPIリクエスト
        enable: 有効化するか

    Returns:
        ApiResponse[TradingModeResponse]: 更新後のモード
    """
    engine = _get_engine(request)
    if not engine:
        return ApiResponse(
            success=False,
            error="ライブエンジンが設定されていません",
            data=TradingModeResponse(),
        )

    engine.enable_auto_trade = enable
    logger.info("自動取引: %s（API経由）", "ON" if enable else "OFF")

    return await get_trading_mode(request)
