"""EventBus -> WebSocket ブリッジ

EventBus のトピックを購読し、ConnectionManager 経由で
WebSocketクライアントへ配信する。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from autotrader.core.event_bus import get_event_bus
from autotrader.web.websocket.manager import (
    EventType,
    manager,
)

logger = logging.getLogger(__name__)

# 診断カウンター（EventBus側の受信数）
bridge_event_counts: dict[str, int] = defaultdict(int)


async def _on_price_updated(data: dict[str, Any]) -> None:
    """価格更新 -> dashboard"""
    bridge_event_counts["price.updated"] += 1
    await manager.broadcast(
        EventType.PRICE_UPDATE, data, "dashboard"
    )


async def _on_signal_generated(
    data: dict[str, Any],
) -> None:
    """シグナル生成 -> signals + dashboard"""
    await manager.broadcast(
        EventType.SIGNAL_UPDATE, data, "signals"
    )
    await manager.broadcast(
        EventType.SIGNAL_UPDATE, data, "dashboard"
    )


async def _on_tick_completed(
    data: dict[str, Any],
) -> None:
    """[旧] tick完了 -> dashboard

    新設計では state.tick / analysis.refreshed に分割されたが、
    互換のため受信ハンドラは残す。engine 側が publish しなくなった
    時点で自然消滅する。
    """
    bridge_event_counts["tick.completed"] += 1
    await manager.broadcast(
        EventType.TICK_UPDATE, data, "dashboard"
    )


async def _on_state_tick(
    data: dict[str, Any],
) -> None:
    """毎 tick 状態更新 -> dashboard

    account / positions / alerts のみ (analysis なし)。
    フロント側 _applyStateTick でハンドリング。
    """
    bridge_event_counts["state.tick"] += 1
    await manager.broadcast(
        EventType.STATE_TICK, data, "dashboard"
    )


async def _on_analysis_refreshed(
    data: dict[str, Any],
) -> None:
    """M1 確定時の analysis 更新 -> dashboard

    analysis / radar / indicators (毎秒ではなく M1 確定時のみ)。
    フロント側 _applyAnalysisUpdate でハンドリング。
    """
    bridge_event_counts["analysis.refreshed"] += 1
    await manager.broadcast(
        EventType.ANALYSIS_UPDATE, data, "dashboard"
    )


async def _on_position_opened(
    data: dict[str, Any],
) -> None:
    """ポジションオープン -> dashboard"""
    await manager.broadcast(
        EventType.POSITION_UPDATE, data, "dashboard"
    )


async def _on_position_closed(
    data: dict[str, Any],
) -> None:
    """ポジションクローズ -> dashboard"""
    await manager.broadcast(
        EventType.POSITION_UPDATE, data, "dashboard"
    )


async def _on_news_received(
    data: dict[str, Any],
) -> None:
    """ニュース受信 -> dashboard"""
    await manager.broadcast(
        EventType.NEWS_UPDATE, data, "dashboard"
    )


async def _on_calendar_updated(
    data: dict[str, Any],
) -> None:
    """カレンダー更新 -> dashboard"""
    await manager.broadcast(
        EventType.CALENDAR_UPDATE, data, "dashboard"
    )


async def _on_logic_reloaded(
    data: dict[str, Any],
) -> None:
    """ロジックリロード完了 -> dashboard"""
    await manager.broadcast(
        EventType.LOGIC_RELOAD,
        {"status": "completed", **data},
        "dashboard",
    )


async def _on_logic_reload_failed(
    data: dict[str, Any],
) -> None:
    """ロジックリロード失敗 -> dashboard"""
    await manager.broadcast(
        EventType.LOGIC_RELOAD,
        {"status": "failed", **data},
        "dashboard",
    )


async def _on_logic_change_detected(
    data: dict[str, Any],
) -> None:
    """ロジック変更検知 -> dashboard"""
    await manager.broadcast(
        EventType.LOGIC_CHANGE_DETECTED, data, "dashboard"
    )


def setup_event_bridge() -> None:
    """EventBus -> WebSocket ブリッジを登録

    アプリケーション起動時（lifespan）で1回呼ぶ。
    """
    get_event_bus().subscribe(
        "price.updated", _on_price_updated
    )
    get_event_bus().subscribe(
        "signal.generated", _on_signal_generated
    )
    get_event_bus().subscribe(
        "tick.completed", _on_tick_completed
    )
    get_event_bus().subscribe(
        "state.tick", _on_state_tick
    )
    get_event_bus().subscribe(
        "analysis.refreshed", _on_analysis_refreshed
    )
    get_event_bus().subscribe(
        "position.opened", _on_position_opened
    )
    get_event_bus().subscribe(
        "position.closed", _on_position_closed
    )
    get_event_bus().subscribe(
        "news.received", _on_news_received
    )
    get_event_bus().subscribe(
        "calendar.updated", _on_calendar_updated
    )
    get_event_bus().subscribe(
        "logic.reloaded", _on_logic_reloaded
    )
    get_event_bus().subscribe(
        "logic.reload_failed", _on_logic_reload_failed
    )
    get_event_bus().subscribe(
        "logic.change_detected", _on_logic_change_detected
    )
