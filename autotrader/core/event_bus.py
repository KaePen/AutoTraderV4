"""非同期イベントバス

Engine <-> Web の疎結合化のためのシンプルな pub/sub。
型付きイベント (subscribe_typed / publish_typed) と
従来の文字列ベース (subscribe / publish) の両方をサポート。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import Any, Awaitable, Callable, Coroutine, TypeVar

from autotrader.core.events import BaseEvent

logger = logging.getLogger(__name__)

# 文字列ベースハンドラー型: async def handler(data: dict) -> None
EventHandler = Callable[
    [dict[str, Any]], Coroutine[Any, Any, None]
]

T = TypeVar("T", bound=BaseEvent)
TypedEventHandler = Callable[[Any], Awaitable[None]]


class EventBus:
    """非同期イベントバス

    トピックベースの pub/sub でモジュール間を疎結合にする。
    文字列ベース API（後方互換）と型付き API の両方を提供。
    """

    def __init__(self) -> None:
        """初期化"""
        # 既存: 文字列ベース
        self._handlers: dict[
            str, list[EventHandler]
        ] = defaultdict(list)
        # 新規: 型ベース
        self._typed_handlers: dict[
            type, dict[str, TypedEventHandler]
        ] = defaultdict(dict)

    # === 既存API（後方互換） ===

    def subscribe(
        self, topic: str, handler: EventHandler
    ) -> None:
        """トピックにハンドラーを登録

        Args:
            topic (str): トピック名
            handler (EventHandler): 非同期ハンドラー
        """
        self._handlers[topic].append(handler)

    def unsubscribe(
        self, topic: str, handler: EventHandler
    ) -> None:
        """トピックからハンドラーを解除

        Args:
            topic (str): トピック名
            handler (EventHandler): 解除するハンドラー
        """
        handlers = self._handlers.get(topic)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def publish(
        self, topic: str, data: dict[str, Any]
    ) -> None:
        """トピックにデータを発行（同期的に全ハンドラー実行）

        Args:
            topic (str): トピック名
            data (dict[str, Any]): イベントデータ
        """
        for handler in self._handlers.get(topic, []):
            try:
                await handler(data)
            except Exception:
                logger.exception(
                    "EventBus handler error: topic=%s",
                    topic,
                )

    def publish_nowait(
        self, topic: str, data: dict[str, Any]
    ) -> None:
        """トピックにデータを発行（create_taskで非ブロッキング）

        Args:
            topic (str): トピック名
            data (dict[str, Any]): イベントデータ
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(topic, data))
        except RuntimeError:
            pass  # イベントループなし（テスト時等）

    # === 新API（型安全） ===

    def subscribe_typed(
        self,
        event_type: type[T],
        handler: Callable[[T], Awaitable[None]],
    ) -> str:
        """型付きイベントを購読

        Args:
            event_type (type[T]): BaseEvent サブクラス
            handler: 非同期ハンドラー

        Returns:
            str: 購読ID（unsubscribe_typed で解除用）
        """
        sub_id = str(uuid.uuid4())
        self._typed_handlers[event_type][sub_id] = handler
        return sub_id

    def unsubscribe_typed(
        self, subscription_id: str
    ) -> None:
        """購読IDで型付きハンドラーを解除

        Args:
            subscription_id (str): subscribe_typed の戻り値
        """
        for handlers in self._typed_handlers.values():
            if subscription_id in handlers:
                del handlers[subscription_id]
                return

    async def publish_typed(self, event: BaseEvent) -> None:
        """型付きイベントを発行

        Args:
            event (BaseEvent): 発行するイベント
        """
        event_type = type(event)
        handlers = self._typed_handlers.get(event_type, {})
        for handler in handlers.values():
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "型付きイベントハンドラーエラー: %s",
                    event_type.__name__,
                )


# --- singleton 管理 ---
# 後方互換: `from autotrader.core.event_bus import event_bus` を維持
# テスト時は set_event_bus() で差し替え可能

_default_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """デフォルト EventBus を取得

    テスト時は set_event_bus() で差し替え可能。

    Returns:
        EventBus: イベントバスインスタンス
    """
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


def set_event_bus(bus: EventBus | None) -> None:
    """EventBus を差し替え（テスト用）

    Args:
        bus: 差し替えるインスタンス（Noneでリセット）
    """
    global _default_bus
    _default_bus = bus


def reset_event_bus() -> None:
    """EventBus をリセットし新規インスタンスを生成"""
    global _default_bus
    _default_bus = None


# 後方互換エイリアス — 既存 import を壊さない
event_bus: EventBus = get_event_bus()  # type: ignore[assignment]
