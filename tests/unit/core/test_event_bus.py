"""EventBusユニットテスト"""

from __future__ import annotations

import asyncio

import pytest

from autotrader.core.event_bus import EventBus


@pytest.fixture
def bus():
    """テスト用EventBusインスタンス"""
    return EventBus()


class TestEventBus:
    """EventBusテスト"""

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self, bus):
        """subscribe->publishでハンドラーが呼ばれる"""
        received = []

        async def handler(data):
            received.append(data)

        bus.subscribe("test.topic", handler)
        await bus.publish("test.topic", {"key": "value"})

        assert len(received) == 1
        assert received[0] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_multiple_handlers(self, bus):
        """複数ハンドラーが全て呼ばれる"""
        results = []

        async def handler_a(data):
            results.append("a")

        async def handler_b(data):
            results.append("b")

        bus.subscribe("multi", handler_a)
        bus.subscribe("multi", handler_b)
        await bus.publish("multi", {})

        assert sorted(results) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus):
        """unsubscribeでハンドラーが解除される"""
        received = []

        async def handler(data):
            received.append(data)

        bus.subscribe("unsub", handler)
        bus.unsubscribe("unsub", handler)
        await bus.publish("unsub", {"x": 1})

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_publish_no_handlers(self, bus):
        """ハンドラーなしトピックへのpublishはエラーなし"""
        await bus.publish("empty.topic", {"data": True})

    @pytest.mark.asyncio
    async def test_handler_error_does_not_break(self, bus):
        """ハンドラーエラーで他ハンドラーが止まらない"""
        results = []

        async def bad_handler(data):
            raise ValueError("boom")

        async def good_handler(data):
            results.append("ok")

        bus.subscribe("error", bad_handler)
        bus.subscribe("error", good_handler)
        await bus.publish("error", {})

        assert results == ["ok"]

    @pytest.mark.asyncio
    async def test_publish_nowait(self, bus):
        """publish_nowaitがcreate_taskで非同期発行する"""
        received = []

        async def handler(data):
            received.append(data)

        bus.subscribe("nowait", handler)
        bus.publish_nowait("nowait", {"val": 42})

        # create_taskで投入されたので、awaitが必要
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0] == {"val": 42}

    @pytest.mark.asyncio
    async def test_different_topics_isolated(self, bus):
        """異なるトピックのハンドラーは分離される"""
        results_a = []
        results_b = []

        async def handler_a(data):
            results_a.append(data)

        async def handler_b(data):
            results_b.append(data)

        bus.subscribe("topic_a", handler_a)
        bus.subscribe("topic_b", handler_b)

        await bus.publish("topic_a", {"from": "a"})

        assert len(results_a) == 1
        assert len(results_b) == 0
