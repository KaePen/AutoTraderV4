"""WebSocketイベントハンドラー"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from autotrader.web.websocket.manager import ConnectionManager, EventType, manager


async def handle_market_websocket(
    websocket: WebSocket,
    symbol: str,
    connection_manager: ConnectionManager = manager,
) -> None:
    """市場データWebSocketハンドラー

    Args:
        websocket: WebSocketインスタンス
        symbol: 通貨ペア
        connection_manager: 接続マネージャー
    """
    channel = f"market:{symbol}"
    await connection_manager.connect(websocket, channel)
    try:
        while True:
            # クライアントからのメッセージを待機（接続維持）
            data = await websocket.receive_text()
            # pingに対してpongを返す
            if data == "ping":
                await connection_manager.send_personal(
                    websocket, EventType.HEARTBEAT, {"pong": True}
                )
    except WebSocketDisconnect:
        await connection_manager.disconnect(websocket, channel)


async def handle_signals_websocket(
    websocket: WebSocket,
    connection_manager: ConnectionManager = manager,
) -> None:
    """シグナルWebSocketハンドラー

    Args:
        websocket: WebSocketインスタンス
        connection_manager: 接続マネージャー
    """
    channel = "signals"
    await connection_manager.connect(websocket, channel)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await connection_manager.send_personal(
                    websocket, EventType.HEARTBEAT, {"pong": True}
                )
    except WebSocketDisconnect:
        await connection_manager.disconnect(websocket, channel)


async def handle_dashboard_websocket(
    websocket: WebSocket,
    connection_manager: ConnectionManager = manager,
) -> None:
    """ダッシュボードWebSocketハンドラー

    Args:
        websocket: WebSocketインスタンス
        connection_manager: 接続マネージャー
    """
    channel = "dashboard"
    await connection_manager.connect(websocket, channel)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await connection_manager.send_personal(
                    websocket, EventType.HEARTBEAT, {"pong": True}
                )
    except WebSocketDisconnect:
        await connection_manager.disconnect(websocket, channel)


async def broadcast_candle_update(
    symbol: str, candle_data: dict[str, Any]
) -> None:
    """ローソク足更新をブロードキャスト

    Args:
        symbol: 通貨ペア
        candle_data: ローソク足データ
    """
    channel = f"market:{symbol}"
    await manager.broadcast(EventType.CANDLE_UPDATE, candle_data, channel)


async def broadcast_signal_update(signal_data: dict[str, Any]) -> None:
    """シグナル更新をブロードキャスト

    Args:
        signal_data: シグナルデータ
    """
    await manager.broadcast(EventType.SIGNAL_UPDATE, signal_data, "signals")
    # ダッシュボードにも通知
    await manager.broadcast(EventType.SIGNAL_UPDATE, signal_data, "dashboard")


async def broadcast_position_update(position_data: dict[str, Any]) -> None:
    """ポジション更新をブロードキャスト

    Args:
        position_data: ポジションデータ
    """
    await manager.broadcast(EventType.POSITION_UPDATE, position_data, "dashboard")


async def broadcast_account_update(account_data: dict[str, Any]) -> None:
    """口座情報更新をブロードキャスト

    Args:
        account_data: 口座データ
    """
    await manager.broadcast(EventType.ACCOUNT_UPDATE, account_data, "dashboard")


async def broadcast_tick_update(tick_data: dict[str, Any]) -> None:
    """tick完了時の一括更新をブロードキャスト

    analysis / account / positions / radar / mode を
    1ペイロードでダッシュボードに送信する。

    Args:
        tick_data: tick更新データ
    """
    await manager.broadcast(EventType.TICK_UPDATE, tick_data, "dashboard")


async def broadcast_price_update(price_data: dict[str, Any]) -> None:
    """tick毎の価格更新をブロードキャスト

    bid/ask/time_ms を高頻度で配信する。
    フロントエンドはこれを受信してチャートのlastbarを更新する。

    Args:
        price_data: 価格データ（symbol, bid, ask, time_ms）
    """
    await manager.broadcast(EventType.PRICE_UPDATE, price_data, "dashboard")


async def broadcast_alert(alert_data: dict[str, Any]) -> None:
    """アラートをブロードキャスト

    Args:
        alert_data: アラートデータ
    """
    await manager.broadcast(EventType.ALERT, alert_data, "signals")
    await manager.broadcast(EventType.ALERT, alert_data, "dashboard")
