"""WebSocket接続管理"""

from __future__ import annotations

import asyncio
import json
import time as _time
from collections import defaultdict
from datetime import UTC, datetime
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
    TICK_UPDATE = "tick_update"  # 旧 (state_tick へ移行中)
    STATE_TICK = "state_tick"
    ANALYSIS_UPDATE = "analysis_update"
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
        # チャネル別接続セット
        self._connections: dict[str, set[WebSocket]] = {}
        # グローバル接続セット
        self._all_connections: set[WebSocket] = set()
        # ロック
        self._lock = asyncio.Lock()
        # 診断カウンター
        self._broadcast_counts: dict[str, int] = defaultdict(
            int,
        )
        self._last_broadcast_time: dict[str, float] = {}
        self._send_errors: int = 0

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
            self._all_connections.add(websocket)
            if channel:
                if channel not in self._connections:
                    self._connections[channel] = set()
                self._connections[channel].add(websocket)

    async def disconnect(
        self, websocket: WebSocket, channel: str | None = None
    ) -> None:
        """WebSocket接続を切断

        Args:
            websocket: WebSocketインスタンス
            channel: 購読チャネル
        """
        async with self._lock:
            self._all_connections.discard(websocket)
            if channel and channel in self._connections:
                self._connections[channel].discard(websocket)

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
            "timestamp": datetime.now(UTC).isoformat(),
        }
        message_json = json.dumps(message, default=str)

        # 診断カウンター更新
        key = event_type.value
        self._broadcast_counts[key] += 1
        self._last_broadcast_time[key] = _time.monotonic()

        async with self._lock:
            if channel and channel in self._connections:
                targets = self._connections[channel]
            else:
                targets = self._all_connections

            disconnected: set[WebSocket] = set()
            for connection in list(targets):
                try:
                    await connection.send_text(message_json)
                except Exception:
                    self._send_errors += 1
                    disconnected.add(connection)

            # 切断された接続を削除（O(1)）
            if disconnected:
                self._all_connections -= disconnected
                for ch_conns in self._connections.values():
                    ch_conns -= disconnected

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
            "timestamp": datetime.now(UTC).isoformat(),
        }
        try:
            await websocket.send_text(json.dumps(message, default=str))
        except Exception:
            await self.disconnect(websocket)

    def get_connection_count(
        self, channel: str | None = None,
    ) -> int:
        """接続数を取得

        Args:
            channel: チャネル名

        Returns:
            int: 接続数
        """
        if channel and channel in self._connections:
            return len(self._connections[channel])
        return len(self._all_connections)

    def get_stats(self) -> dict[str, Any]:
        """診断統計を取得

        Returns:
            dict[str, Any]: 接続数・イベントカウント等
        """
        now = _time.monotonic()
        channels = {}
        for ch, conns in self._connections.items():
            channels[ch] = len(conns)
        last_seen: dict[str, float | None] = {}
        for evt, t in self._last_broadcast_time.items():
            last_seen[evt] = round(now - t, 1)
        return {
            "total_connections": len(self._all_connections),
            "channels": channels,
            "broadcast_counts": dict(self._broadcast_counts),
            "last_broadcast_ago_sec": last_seen,
            "send_errors": self._send_errors,
        }


# シングルトンインスタンス
manager = ConnectionManager()
