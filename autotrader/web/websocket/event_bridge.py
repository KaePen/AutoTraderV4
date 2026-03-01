"""EventBus -> WebSocket ブリッジ

EventBus のトピックを購読し、ConnectionManager 経由で
WebSocketクライアントへ配信する。
"""

from __future__ import annotations

from typing import Any

from autotrader.core.event_bus import event_bus
from autotrader.web.websocket.manager import (
    EventType,
    manager,
)


async def _on_price_updated(data: dict[str, Any]) -> None:
    """価格更新 -> dashboard"""
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
    """tick完了 -> dashboard"""
    await manager.broadcast(
        EventType.TICK_UPDATE, data, "dashboard"
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


def setup_event_bridge() -> None:
    """EventBus -> WebSocket ブリッジを登録

    アプリケーション起動時（lifespan）で1回呼ぶ。
    """
    event_bus.subscribe(
        "price.updated", _on_price_updated
    )
    event_bus.subscribe(
        "signal.generated", _on_signal_generated
    )
    event_bus.subscribe(
        "tick.completed", _on_tick_completed
    )
    event_bus.subscribe(
        "position.opened", _on_position_opened
    )
    event_bus.subscribe(
        "position.closed", _on_position_closed
    )
    event_bus.subscribe(
        "news.received", _on_news_received
    )
    event_bus.subscribe(
        "calendar.updated", _on_calendar_updated
    )
