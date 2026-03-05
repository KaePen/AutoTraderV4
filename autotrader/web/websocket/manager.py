"""WebSocket接続管理"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from enum import Enum
from typing import Any

from fastapi import WebSocket


class EventType(str, Enum):
    """WebSocketイベント種別"""

    CANDLE_UPDATE = "candle_update"
    SIGNAL_UPDATE = "signal_update"
    POSITION_UPDATE = "position_update"
    INDICATOR_UPDATE = "indicator_update"
    ACCOUNT_UPDATE = "account_update"
    TICK_UPDATE = "tick_update"
    PRICE_UPDATE = "price_update"
    NEWS_UPDATE = "news_update"
    CALENDAR_UPDATE = "calendar_update"
    ALERT = "alert"
    HEARTBEAT = "heartbeat"
    LOGIC_RELOAD = "logic_reload"
    LOGIC_CHANGE_DETECTED = "logic_change_detected"


class ConnectionManager:
    """WebSocket接続管理

    複数のチャネルを管理し、購読者にイベントを配信する。
    """

    def __init__(self) -> None:
        """初期化"""
        # チャネル別接続リスト
        self._connections: dict[str, list[WebSocket]] = {}
        # グローバル接続リスト
        self._all_connections: list[WebSocket] = []
        # ロック
        self._lock = asyncio.Lock()

    async def connect(
        self, websocket: WebSocket, channel: str | None = None
    ) -> None:
        """WebSocket接続を確立

        Args:
            websocket: WebSocketインスタンス
            channel: 購読チャネル（Noneの場合はグローバル）
        """
        await websocket.accept()
        async with self._lock:
            self._all_connections.append(websocket)
            if channel:
                if channel not in self._connections:
                    self._connections[channel] = []
                self._connections[channel].append(websocket)

    async def disconnect(
        self, websocket: WebSocket, channel: str | None = None
    ) -> None:
        """WebSocket接続を切断

        Args:
            websocket: WebSocketインスタンス
            channel: 購読チャネル
        """
        async with self._lock:
            if websocket in self._all_connections:
                self._all_connections.remove(websocket)
            if channel and channel in self._connections:
                if websocket in self._connections[channel]:
                    self._connections[channel].remove(websocket)

    async def broadcast(
        self,
        event_type: EventType,
        data: dict[str, Any],
        channel: str | None = None,
    ) -> None:
        """イベントをブロードキャスト

        Args:
            event_type: イベント種別
            data: イベントデータ
            channel: 対象チャネル（Noneの場合は全接続）
        """
        message = {
            "type": event_type.value,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        message_json = json.dumps(message, default=str)

        async with self._lock:
            if channel and channel in self._connections:
                targets = self._connections[channel]
            else:
                targets = self._all_connections

            disconnected = []
            for connection in targets:
                try:
                    await connection.send_text(message_json)
                except Exception:
                    disconnected.append(connection)

            # 切断された接続を削除
            for conn in disconnected:
                if conn in self._all_connections:
                    self._all_connections.remove(conn)
                for ch_conns in self._connections.values():
                    if conn in ch_conns:
                        ch_conns.remove(conn)

    async def send_personal(
        self, websocket: WebSocket, event_type: EventType, data: dict[str, Any]
    ) -> None:
        """個別にメッセージ送信

        Args:
            websocket: 対象WebSocket
            event_type: イベント種別
            data: イベントデータ
        """
        message = {
            "type": event_type.value,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }
        try:
            await websocket.send_text(json.dumps(message, default=str))
        except Exception:
            await self.disconnect(websocket)

    def get_connection_count(self, channel: str | None = None) -> int:
        """接続数を取得

        Args:
            channel: チャネル名

        Returns:
            int: 接続数
        """
        if channel and channel in self._connections:
            return len(self._connections[channel])
        return len(self._all_connections)


# シングルトンインスタンス
manager = ConnectionManager()
