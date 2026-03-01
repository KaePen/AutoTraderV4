"""非同期イベントバス

Engine <-> Web の疎結合化のためのシンプルな pub/sub。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# ハンドラー型: async def handler(data: dict) -> None
EventHandler = Callable[
    [dict[str, Any]], Coroutine[Any, Any, None]
]


class EventBus:
    """非同期イベントバス

    トピックベースの pub/sub でモジュール間を疎結合にする。
    """

    def __init__(self) -> None:
        """初期化"""
        self._handlers: dict[
            str, list[EventHandler]
        ] = defaultdict(list)

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


# モジュールレベルシングルトン
event_bus = EventBus()
