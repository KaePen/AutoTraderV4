"""診断エンドポイント

WebSocket・EventBus・エンジンの状態を確認するためのAPI。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from autotrader.core.event_bus import get_event_bus
from autotrader.web.websocket.event_bridge import (
    bridge_event_counts,
)
from autotrader.web.websocket.manager import manager

router = APIRouter()


@router.get("/debug/ws-status")
async def ws_status(request: Request) -> dict[str, Any]:
    """WebSocket・EventBus・エンジンの診断情報

    Returns:
        dict: 接続数、イベントカウント、エンジン状態
    """
    # ConnectionManager 統計
    ws_stats = manager.get_stats()

    # EventBus 購読状況
    bus = get_event_bus()
    subscriptions: dict[str, int] = {}
    for topic, handlers in bus._handlers.items():
        subscriptions[topic] = len(handlers)

    # エンジン状態
    engines_info: list[dict[str, Any]] = []
    mgr = getattr(request.app.state, "engine_manager", None)
    if mgr:
        for sym, eng in mgr._engines.items():
            engines_info.append(
                {
                    "symbol": sym,
                    "running": eng.running,
                    "last_tick_ms": eng._last_mt5_tick_ms,
                }
            )

    return {
        "websocket": ws_stats,
        "eventbus_subscriptions": subscriptions,
        "bridge_event_counts": dict(bridge_event_counts),
        "engines": engines_info,
    }


@router.get("/debug/simulate-dd")
async def simulate_dd(
    request: Request,
    pct: float = 0.0,
) -> dict[str, Any]:
    """DD状態をシミュレートしてWebUI表示を確認

    Args:
        pct: シミュレートするDD% (0でリセット)

    Returns:
        dict: 適用後のDD状態
    """
    mgr = getattr(request.app.state, "engine_manager", None)
    if mgr is None:
        return {"error": "EngineManager未初期化"}

    if pct <= 0:
        # リセット
        mgr._current_dd_pct = 0.0
        mgr._dd_warning_active = False
        mgr._dd_emergency_active = False
        mgr._dd_emergency_at = None
        mgr._emergency_close_done = False
        return {"action": "reset", "dd_status": mgr.dd_status}

    # DD状態を上書き
    mgr._current_dd_pct = pct
    from autotrader.live.engine_manager import (
        DD_EMERGENCY_PCT,
        DD_WARNING_PCT,
    )
    mgr._dd_warning_active = pct >= DD_WARNING_PCT
    if pct >= DD_EMERGENCY_PCT:
        from datetime import datetime, timezone
        mgr._dd_emergency_active = True
        mgr._dd_emergency_at = datetime.now(
            tz=timezone.utc,
        )
    else:
        mgr._dd_emergency_active = False
        mgr._dd_emergency_at = None

    # ピーク未設定なら仮の値をセット
    if mgr._peak_equity <= 0:
        mgr._peak_equity = 1_000_000

    return {
        "action": f"simulate DD {pct}%",
        "dd_status": mgr.dd_status,
    }
