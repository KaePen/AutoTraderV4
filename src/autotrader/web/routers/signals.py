"""シグナルルーター"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from autotrader.web.schemas import (
    ApiResponse,
    AnalysisResponse,
    SignalResponse,
)

router = APIRouter()


def _signal_to_response(signal) -> SignalResponse:
    """SignalエンティティをSignalResponseに変換

    Args:
        signal: Signalエンティティ

    Returns:
        SignalResponse: レスポンス
    """
    return SignalResponse(
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        signal_type=signal.signal_type,
        confidence=signal.confidence,
        confidence_level=signal.confidence_level,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        reasoning=signal.reasoning,
        created_at=signal.created_at,
        indicators_snapshot=signal.indicators_snapshot,
    )


@router.get(
    "/signals/analysis",
    response_model=ApiResponse[AnalysisResponse],
)
async def get_analysis(
    request: Request,
) -> ApiResponse[AnalysisResponse]:
    """直近tick分析状態を取得

    HOLDシグナルを含む全tickの分析結果（スコア・閾値・フィルター結果）を返す。

    Args:
        request: FastAPIリクエスト

    Returns:
        ApiResponse[AnalysisResponse]: 分析状態
    """
    engine = getattr(request.app.state, "live_engine", None)
    if engine is None or engine.last_analysis is None:
        running = engine.running if engine else False
        connected = engine.connected if engine else False
        auto_trade = engine.enable_auto_trade if engine else False
        return ApiResponse(
            data=AnalysisResponse(
                rationale="分析待機中（データなし）" if running else "エンジン停止中",
                engine_running=running,
                auto_trade_enabled=auto_trade,
                mt5_connected=connected,
                demo_mode=engine.demo_mode_enabled if engine else False,
            )
        )

    cs = engine.last_analysis
    tick_time = engine.last_tick_time

    return ApiResponse(
        data=AnalysisResponse(
            direction=cs.direction.value,
            confidence=cs.confidence,
            consensus_score=cs.consensus_score,
            entry_threshold=cs.entry_threshold,
            regime=cs.regime,
            mode=cs.mode,
            rationale=cs.rationale,
            htf_alignment=cs.htf_alignment,
            penalty_total=cs.penalty_total,
            penalty_breakdown=cs.penalty_breakdown,
            trend_strength=cs.trend_strength,
            aligned_tfs=list(cs.aligned_tfs),
            tf_scores=dict(cs.scores),
            tf_breakdowns={
                k: dict(v)
                for k, v in cs.tf_score_breakdowns.items()
            },
            last_tick_time=(
                tick_time.isoformat() if tick_time else None
            ),
            demo_mode=engine.demo_mode_enabled,
            engine_running=engine.running,
            auto_trade_enabled=engine.enable_auto_trade,
            mt5_connected=engine.connected,
        )
    )


@router.get(
    "/signals/current",
    response_model=ApiResponse[list[SignalResponse]],
)
async def get_current_signals(
    request: Request,
    symbol: str = Query(
        default="USDJPY", description="通貨ペア"
    ),
) -> ApiResponse[list[SignalResponse]]:
    """現在のシグナルを取得

    ライブエンジンのメモリ上の履歴を返す。
    エンジン未起動時は空リストを返す。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア

    Returns:
        ApiResponse[list[SignalResponse]]: シグナル一覧
    """
    engine = getattr(request.app.state, "live_engine", None)
    if engine is not None and engine.signal_history:
        signals = [
            _signal_to_response(s)
            for s in engine.signal_history
            if s.symbol == symbol
        ]
        return ApiResponse(data=signals)

    return ApiResponse(data=[])


@router.get(
    "/signals/history",
    response_model=ApiResponse[list[SignalResponse]],
)
async def get_signal_history(
    request: Request,
    symbol: str = Query(
        default="USDJPY", description="通貨ペア"
    ),
    limit: int = Query(
        default=50, ge=1, le=500, description="取得件数"
    ),
    offset: int = Query(
        default=0, ge=0, description="オフセット"
    ),
) -> ApiResponse[list[SignalResponse]]:
    """シグナル履歴を取得

    ライブエンジンのメモリ上の履歴を返す。
    エンジン未起動時は空リストを返す。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア
        limit: 取得件数
        offset: オフセット

    Returns:
        ApiResponse[list[SignalResponse]]: シグナル履歴
    """
    engine = getattr(request.app.state, "live_engine", None)
    if engine is not None and engine.signal_history:
        signals = [
            _signal_to_response(s)
            for s in engine.signal_history
            if s.symbol == symbol
        ]
        signals = signals[offset:offset + limit]
        return ApiResponse(data=signals)

    return ApiResponse(data=[])
