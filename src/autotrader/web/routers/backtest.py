"""バックテストAPIルーター"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from autotrader.web.dependencies import get_db
from autotrader.web.schemas import ApiResponse


router = APIRouter(prefix="/backtest")

# バックテスト実行状態
_backtest_state: dict[str, Any] = {
    "running": False,
    "progress": 0.0,
    "current_year": None,
    "results": None,
    "error": None,
    "start_time": None,
    "cancel_requested": False,
}

# スレッドプール
_executor = ThreadPoolExecutor(max_workers=2)


class BacktestRequest(BaseModel):
    """バックテストリクエスト"""

    start_year: int = Field(default=2020, ge=2010, le=2030)
    end_year: int = Field(default=2024, ge=2010, le=2030)
    initial_balance: float = Field(default=1_000_000.0)
    volume: float = Field(default=1.0)
    data_dir: str = Field(default="data/csv")
    use_short_timeframe: bool = Field(
        default=True,
        description="短い時間足(M5)を基準に使用",
    )
    # UnifiedBotConfigオーバーライド（Optionalで指定時のみ上書き）
    range_day_bbw_threshold: float | None = None
    range_day_score_premium: float | None = None
    weak_hours_enabled: bool | None = None
    weak_hours_score_premium: float | None = None
    tokyo_night_swing_enabled: bool | None = None
    tokyo_night_swing_premium: float | None = None
    use_dynamic_lot: bool | None = None
    base_risk_pct: float | None = None
    max_lot_per_trade: float | None = None
    max_total_exposure_lot: float | None = None
    equity_floor_pct: float | None = None
    slippage_buffer_pips: float | None = None
    enable_position_manager: bool | None = None
    stagnation_min_mfe_r: float | None = None
    range_day_early_be_r: float | None = None
    insurance_trigger_r: float | None = None


class BacktestStatus(BaseModel):
    """バックテスト状態"""

    running: bool
    progress: float
    current_year: int | None
    start_time: str | None
    error: str | None


class YearResult(BaseModel):
    """年別結果"""

    year: int
    trades: int
    win_rate: float
    profit_factor: float
    net_profit: float
    max_drawdown: float
    sharpe: float


class MonthResult(BaseModel):
    """月別結果"""

    year: int
    month: int
    trades: int
    pnl: float
    return_pct: float


class BacktestResultResponse(BaseModel):
    """バックテスト結果レスポンス"""

    total_trades: int
    win_rate: float
    profit_factor: float
    net_profit: float
    max_drawdown: float
    sharpe_ratio: float
    annual_return: float
    yearly_results: list[YearResult]
    monthly_results: list[MonthResult]


def _check_cancel_requested() -> bool:
    """キャンセルがリクエストされたかチェック

    Returns:
        bool: キャンセルされた場合True
    """
    global _backtest_state
    return _backtest_state.get("cancel_requested", False)


def _update_state_callback(event) -> None:
    """状態更新コールバック"""
    global _backtest_state
    from autotrader.backtest.events import EventType, ProgressEvent

    if event.event_type == EventType.PROGRESS:
        if isinstance(event, ProgressEvent):
            _backtest_state["progress"] = event.percentage
    elif event.event_type == EventType.YEAR_START:
        _backtest_state["current_year"] = event.data.get("year")
    elif event.event_type == EventType.YEAR_END:
        _backtest_state["current_year"] = event.data.get("year")


def _run_backtest_sync(
    request: BacktestRequest,
    loop: asyncio.AbstractEventLoop | None = None,
) -> dict[str, Any]:
    """バックテスト同期実行

    Args:
        request: リクエスト
        loop: イベントループ

    Returns:
        結果辞書
    """
    global _backtest_state

    from autotrader.backtest.service import (
        BacktestService,
        BacktestServiceConfig,
        create_bot_config,
    )
    from autotrader.backtest.websocket_listener import (
        create_websocket_listener,
    )

    try:
        # サービス設定
        service_config = BacktestServiceConfig(
            start_year=request.start_year,
            end_year=request.end_year,
            initial_balance=request.initial_balance,
            volume=request.volume,
            data_dir=request.data_dir,
            use_short_timeframe=request.use_short_timeframe,
        )

        # サービス作成
        service = BacktestService(
            config=service_config,
            cancel_callback=_check_cancel_requested,
        )

        # ランナー作成（イベント設定のため先に作成）
        runner = service.create_runner()

        # 状態更新コールバックを追加
        runner._emitter.add_callback(_update_state_callback)

        # WebSocketリスナーを追加
        if loop is not None:
            ws_listener = create_websocket_listener()
            ws_listener._loop = loop
            runner._emitter.add_listener(ws_listener)

        # UnifiedBotConfigオーバーライド
        overrides = {}
        override_fields = [
            "range_day_bbw_threshold",
            "range_day_score_premium",
            "weak_hours_enabled",
            "weak_hours_score_premium",
            "tokyo_night_swing_enabled",
            "tokyo_night_swing_premium",
            "use_dynamic_lot",
            "base_risk_pct",
            "max_lot_per_trade",
            "max_total_exposure_lot",
            "equity_floor_pct",
            "slippage_buffer_pips",
            "enable_position_manager",
            "stagnation_min_mfe_r",
            "range_day_early_be_r",
            "insurance_trigger_r",
        ]
        for field_name in override_fields:
            val = getattr(request, field_name, None)
            if val is not None:
                overrides[field_name] = val

        from autotrader.decision.unified import UnifiedBotConfig
        if overrides:
            bot_config = UnifiedBotConfig(**overrides)
        else:
            bot_config = create_bot_config()

        result = runner.run_unified(
            service_config.start_year,
            service_config.end_year,
            bot_config,
        )

        return {
            "success": True,
            "result": {
                "total_trades": result.trades,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "net_profit": result.net_profit,
                "max_drawdown": result.max_drawdown,
                "sharpe_ratio": result.sharpe_ratio,
                "annual_return": result.annual_return,
                "yearly_results": result.yearly_results,
                "monthly_results": result.monthly_results,
            },
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


async def _run_backtest_background(request: BacktestRequest) -> None:
    """バックグラウンドでバックテスト実行

    Args:
        request: リクエスト
    """
    import functools

    global _backtest_state

    _backtest_state["running"] = True
    _backtest_state["progress"] = 0.0
    _backtest_state["error"] = None
    _backtest_state["results"] = None
    _backtest_state["start_time"] = datetime.now().isoformat()
    _backtest_state["cancel_requested"] = False

    loop = asyncio.get_running_loop()

    try:
        func = functools.partial(_run_backtest_sync, request, loop)
        result = await loop.run_in_executor(_executor, func)

        if result["success"]:
            _backtest_state["results"] = result["result"]
        else:
            _backtest_state["error"] = result["error"]
    except Exception as e:
        _backtest_state["error"] = str(e)
    finally:
        _backtest_state["running"] = False
        _backtest_state["progress"] = 100.0


@router.post("/run", response_model=ApiResponse[dict])
async def run_backtest(
    request: BacktestRequest,
    background_tasks: BackgroundTasks,
) -> ApiResponse[dict]:
    """バックテスト実行（バックグラウンド）

    Args:
        request: リクエスト
        background_tasks: バックグラウンドタスク

    Returns:
        開始結果
    """
    global _backtest_state

    if _backtest_state["running"]:
        raise HTTPException(
            status_code=409,
            detail="バックテストは既に実行中です",
        )

    background_tasks.add_task(_run_backtest_background, request)

    return ApiResponse(
        data={
            "message": "バックテストを開始しました",
            "start_year": request.start_year,
            "end_year": request.end_year,
        }
    )


@router.get("/status", response_model=ApiResponse[BacktestStatus])
async def get_backtest_status() -> ApiResponse[BacktestStatus]:
    """バックテスト状態取得

    Returns:
        状態
    """
    global _backtest_state

    return ApiResponse(
        data=BacktestStatus(
            running=_backtest_state["running"],
            progress=_backtest_state["progress"],
            current_year=_backtest_state.get("current_year"),
            start_time=_backtest_state.get("start_time"),
            error=_backtest_state.get("error"),
        )
    )


@router.get(
    "/results",
    response_model=ApiResponse[BacktestResultResponse | None],
)
async def get_backtest_results() -> (
    ApiResponse[BacktestResultResponse | None]
):
    """バックテスト結果取得

    Returns:
        結果（なければNone）
    """
    global _backtest_state

    results = _backtest_state.get("results")
    if results is None:
        return ApiResponse(data=None)

    return ApiResponse(
        data=BacktestResultResponse(
            total_trades=results["total_trades"],
            win_rate=results["win_rate"],
            profit_factor=results["profit_factor"],
            net_profit=results["net_profit"],
            max_drawdown=results["max_drawdown"],
            sharpe_ratio=results["sharpe_ratio"],
            annual_return=results["annual_return"],
            yearly_results=[
                YearResult(**yr)
                for yr in results.get("yearly_results", [])
            ],
            monthly_results=[
                MonthResult(**mr)
                for mr in results.get("monthly_results", [])
            ],
        )
    )


@router.post("/cancel", response_model=ApiResponse[dict])
async def cancel_backtest() -> ApiResponse[dict]:
    """バックテストキャンセル

    Returns:
        結果
    """
    global _backtest_state

    if not _backtest_state["running"]:
        raise HTTPException(
            status_code=400,
            detail="実行中のバックテストはありません",
        )

    _backtest_state["cancel_requested"] = True

    return ApiResponse(
        data={
            "message": "キャンセルをリクエストしました",
            "status": "cancelling",
        }
    )


@router.get("/history", response_model=ApiResponse[list[dict]])
async def get_backtest_history(
    limit: int = 20,
    db: Session = Depends(get_db),
) -> ApiResponse[list[dict]]:
    """バックテスト履歴を取得

    Args:
        limit: 取得件数
        db: DBセッション

    Returns:
        履歴一覧
    """
    from autotrader.web.services.backtest_history_service import (
        BacktestHistoryService,
    )

    service = BacktestHistoryService(db)
    history = service.get_history(limit=limit)
    return ApiResponse(data=history)


@router.get(
    "/{backtest_id}/trades",
    response_model=ApiResponse[list[dict]],
)
async def get_backtest_trades(
    backtest_id: int,
    db: Session = Depends(get_db),
) -> ApiResponse[list[dict]]:
    """特定バックテストのトレード一覧

    Args:
        backtest_id: バックテストID
        db: DBセッション

    Returns:
        トレード一覧
    """
    from autotrader.web.services.backtest_history_service import (
        BacktestHistoryService,
    )

    service = BacktestHistoryService(db)
    trades = service.get_backtest_trades(backtest_id)
    return ApiResponse(data=trades)
