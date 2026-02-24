"""シグナルルーター"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request

from autotrader.web.dependencies import (
    get_engine_manager,
    get_live_engine,
)
from autotrader.web.schemas import (
    ApiResponse,
    AnalysisResponse,
    SignalResponse,
)

logger = logging.getLogger(__name__)

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
        regime=signal.regime,
        mode=signal.mode,
        consensus_score=signal.consensus_score,
        lot=signal.lot,
    )


async def _resolve_engine(mgr, symbol, fallback_engine):
    """シンボルに対応するエンジンを解決（なければ自動作成）

    Args:
        mgr: EngineManager | None
        symbol: 通貨ペアシンボル | None
        fallback_engine: フォールバックエンジン

    Returns:
        LiveTradingEngine | None: エンジン
    """
    if not mgr or not symbol:
        return fallback_engine

    # 既存エンジンを検索
    target = mgr.get_engine(symbol)
    if target:
        return target

    # MT5未接続ならフォールバック
    if not mgr.connected:
        return fallback_engine

    # エンジンを自動作成（auto_trade=Falseで起動）
    try:
        from autotrader.web.main import build_engine_config
        config = build_engine_config(symbol)
        target = await mgr.add_symbol(config)
        logger.info(
            "エンジン自動作成: %s（シンボル切替）",
            symbol,
        )
        return target
    except Exception as e:
        logger.warning(
            "エンジン自動作成失敗: %s: %s", symbol, e,
        )
        return fallback_engine


@router.get(
    "/signals/analysis",
    response_model=ApiResponse[AnalysisResponse],
)
async def get_analysis(
    request: Request,
    symbol: str | None = Query(
        default=None,
        description="通貨ペア（省略時はエンジンのシンボル）",
    ),
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[AnalysisResponse]:
    """直近tick分析状態を取得

    EngineManager経由でシンボル別エンジンから分析結果を取得。
    対象シンボルのエンジンがなければ自動作成する。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア（省略時はエンジンのシンボル）
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[AnalysisResponse]: 分析状態
    """
    # シンボル別エンジンを解決（なければ自動作成）
    engine = await _resolve_engine(
        mgr, symbol, engine,
    )

    engine_symbol = (
        engine._config.symbol if engine else None
    )

    # エンジン自動作成後もシンボル不一致
    # （MT5未接続等で作成できなかった場合）
    if (
        symbol
        and engine
        and engine_symbol
        and symbol != engine_symbol
    ):
        return ApiResponse(
            data=AnalysisResponse(
                symbol=symbol,
                rationale="MT5未接続のため起動できません",
                engine_running=False,
                mt5_connected=engine.connected
                if engine else False,
            )
        )

    if engine is None or engine.last_analysis is None:
        running = engine.running if engine else False
        connected = engine.connected if engine else False
        auto_trade = (
            engine.enable_auto_trade if engine else False
        )
        return ApiResponse(
            data=AnalysisResponse(
                symbol=engine_symbol or symbol,
                rationale=(
                    "分析待機中（データなし）"
                    if running
                    else "エンジン停止中"
                ),
                engine_running=running,
                auto_trade_enabled=auto_trade,
                mt5_connected=connected,
                demo_mode=(
                    engine.demo_mode_enabled
                    if engine
                    else False
                ),
            )
        )

    cs = engine.last_analysis
    tick_time = engine.last_tick_time

    # 現在のbot設定から閾値を取得（デモ/ライブ切替を即時反映）
    entry_threshold = (
        engine.get_current_entry_threshold()
        or cs.entry_threshold
    )

    return ApiResponse(
        data=AnalysisResponse(
            symbol=engine_symbol,
            direction=cs.direction.value,
            confidence=cs.confidence,
            consensus_score=cs.consensus_score,
            entry_threshold=entry_threshold,
            regime=cs.regime,
            mode=cs.mode,
            rationale=cs.rationale,
            buy_score=cs.buy_score,
            sell_score=cs.sell_score,
            htf_alignment=cs.htf_alignment,
            penalty_total=cs.penalty_total,
            penalty_breakdown=cs.penalty_breakdown,
            trend_strength=cs.trend_strength,
            aligned_tfs=list(cs.aligned_tfs),
            tf_scores=dict(cs.scores),
            tf_breakdowns={
                k: dict(v)
                for k, v
                in cs.tf_score_breakdowns.items()
            },
            tf_directions=dict(cs.tf_directions),
            last_tick_time=(
                tick_time.isoformat()
                if tick_time
                else None
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
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[list[SignalResponse]]:
    """現在のシグナルを取得

    ライブエンジンのメモリ上の履歴を返す。
    エンジン未起動時は空リストを返す。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[list[SignalResponse]]: シグナル一覧
    """
    # シンボル別エンジンを取得
    if mgr:
        target = mgr.get_engine(symbol)
        if target:
            engine = target

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
    engine=Depends(get_live_engine),
    mgr=Depends(get_engine_manager),
) -> ApiResponse[list[SignalResponse]]:
    """シグナル履歴を取得

    ライブエンジンのメモリ上の履歴を返す。
    エンジン未起動時は空リストを返す。

    Args:
        request: FastAPIリクエスト
        symbol: 通貨ペア
        limit: 取得件数
        offset: オフセット
        engine: LiveTradingEngine
        mgr: EngineManager

    Returns:
        ApiResponse[list[SignalResponse]]: シグナル履歴
    """
    # シンボル別エンジンを取得
    if mgr:
        target = mgr.get_engine(symbol)
        if target:
            engine = target

    if engine is not None and engine.signal_history:
        signals = [
            _signal_to_response(s)
            for s in engine.signal_history
            if s.symbol == symbol
        ]
        signals = signals[offset:offset + limit]
        return ApiResponse(data=signals)

    return ApiResponse(data=[])
