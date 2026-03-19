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
