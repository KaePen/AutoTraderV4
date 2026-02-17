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


async def broadcast_alert(alert_data: dict[str, Any]) -> None:
    """アラートをブロードキャスト

    Args:
        alert_data: アラートデータ
    """
    await manager.broadcast(EventType.ALERT, alert_data, "signals")
    await manager.broadcast(EventType.ALERT, alert_data, "dashboard")


async def handle_backtest_websocket(
    websocket: WebSocket,
    connection_manager: ConnectionManager = manager,
) -> None:
    """バックテストWebSocketハンドラー

    Args:
        websocket: WebSocketインスタンス
        connection_manager: 接続マネージャー
    """
    channel = "backtest"
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


async def broadcast_backtest_start(config_data: dict[str, Any]) -> None:
    """バックテスト開始をブロードキャスト

    Args:
        config_data: 設定データ
    """
    await manager.broadcast(EventType.BACKTEST_START, config_data, "backtest")


async def broadcast_backtest_end(result_data: dict[str, Any]) -> None:
    """バックテスト終了をブロードキャスト

    Args:
        result_data: 結果データ
    """
    await manager.broadcast(EventType.BACKTEST_END, result_data, "backtest")


async def broadcast_backtest_progress(progress_data: dict[str, Any]) -> None:
    """バックテスト進捗をブロードキャスト

    Args:
        progress_data: 進捗データ
    """
    await manager.broadcast(EventType.BACKTEST_PROGRESS, progress_data, "backtest")


async def broadcast_backtest_year_start(year_data: dict[str, Any]) -> None:
    """バックテスト年開始をブロードキャスト

    Args:
        year_data: 年データ
    """
    await manager.broadcast(EventType.BACKTEST_YEAR_START, year_data, "backtest")


async def broadcast_backtest_year_end(year_data: dict[str, Any]) -> None:
    """バックテスト年終了をブロードキャスト

    Args:
        year_data: 年データ
    """
    await manager.broadcast(EventType.BACKTEST_YEAR_END, year_data, "backtest")


async def broadcast_backtest_month_end(month_data: dict[str, Any]) -> None:
    """バックテスト月終了をブロードキャスト

    Args:
        month_data: 月データ
    """
    await manager.broadcast(EventType.BACKTEST_MONTH_END, month_data, "backtest")


async def broadcast_backtest_trade_open(trade_data: dict[str, Any]) -> None:
    """バックテストトレード開始をブロードキャスト

    Args:
        trade_data: トレードデータ
    """
    await manager.broadcast(EventType.BACKTEST_TRADE_OPEN, trade_data, "backtest")


async def broadcast_backtest_trade_close(trade_data: dict[str, Any]) -> None:
    """バックテストトレード終了をブロードキャスト

    Args:
        trade_data: トレードデータ
    """
    await manager.broadcast(EventType.BACKTEST_TRADE_CLOSE, trade_data, "backtest")


async def broadcast_backtest_signal(signal_data: dict[str, Any]) -> None:
    """バックテストシグナルをブロードキャスト

    Args:
        signal_data: シグナルデータ
    """
    await manager.broadcast(EventType.BACKTEST_SIGNAL, signal_data, "backtest")


async def broadcast_backtest_metrics(metrics_data: dict[str, Any]) -> None:
    """バックテストメトリクスをブロードキャスト

    Args:
        metrics_data: メトリクスデータ
    """
    await manager.broadcast(EventType.BACKTEST_METRICS, metrics_data, "backtest")
