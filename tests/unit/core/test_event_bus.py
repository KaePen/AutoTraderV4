"""EventBusユニットテスト"""

from __future__ import annotations

import asyncio

import pytest

from autotrader.core.event_bus import (
    EventBus,
    get_event_bus,
    reset_event_bus,
    set_event_bus,
)
from autotrader.core.events import (
    ErrorEvent,
    SignalGeneratedEvent,
    TradeClosedEvent,
    TradeOpenedEvent,
)


@pytest.fixture
def bus():
    """テスト用EventBusインスタンス"""
    return EventBus()


class TestEventBus:
    """EventBus文字列ベースAPIテスト"""

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
    async def test_handler_error_does_not_break(
        self, bus
    ):
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


class TestTypedEventBus:
    """EventBus型付きAPIテスト"""

    @pytest.mark.asyncio
    async def test_subscribe_typed_and_publish(
        self, bus
    ):
        """subscribe_typed->publish_typedで呼ばれる"""
        received = []

        async def handler(event: TradeOpenedEvent):
            received.append(event)

        bus.subscribe_typed(TradeOpenedEvent, handler)

        evt = TradeOpenedEvent(
            trade_id="T001",
            symbol="USDJPY",
            direction="BUY",
            volume=0.1,
            entry_price=150.0,
            sl_price=149.5,
            tp_price=151.0,
        )
        await bus.publish_typed(evt)

        assert len(received) == 1
        assert received[0].trade_id == "T001"
        assert received[0].symbol == "USDJPY"
        assert received[0].direction == "BUY"
        assert received[0].volume == 0.1

    @pytest.mark.asyncio
    async def test_typed_multiple_handlers(self, bus):
        """型付き: 複数ハンドラーが全て呼ばれる"""
        results = []

        async def handler_a(event: TradeClosedEvent):
            results.append("a")

        async def handler_b(event: TradeClosedEvent):
            results.append("b")

        bus.subscribe_typed(TradeClosedEvent, handler_a)
        bus.subscribe_typed(TradeClosedEvent, handler_b)

        await bus.publish_typed(
            TradeClosedEvent(
                trade_id="T002",
                pnl=100.0,
                close_reason="tp",
            )
        )

        assert sorted(results) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_typed_unsubscribe(self, bus):
        """unsubscribe_typedでハンドラーが解除される"""
        received = []

        async def handler(
            event: SignalGeneratedEvent,
        ):
            received.append(event)

        sub_id = bus.subscribe_typed(
            SignalGeneratedEvent, handler
        )
        bus.unsubscribe_typed(sub_id)

        await bus.publish_typed(
            SignalGeneratedEvent(
                symbol="EURJPY", direction="SELL"
            )
        )

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_typed_different_types_isolated(
        self, bus
    ):
        """異なるイベント型のハンドラーは分離される"""
        trade_events = []
        error_events = []

        async def trade_handler(
            event: TradeOpenedEvent,
        ):
            trade_events.append(event)

        async def error_handler(event: ErrorEvent):
            error_events.append(event)

        bus.subscribe_typed(
            TradeOpenedEvent, trade_handler
        )
        bus.subscribe_typed(ErrorEvent, error_handler)

        await bus.publish_typed(
            TradeOpenedEvent(
                trade_id="T003", symbol="USDJPY"
            )
        )

        assert len(trade_events) == 1
        assert len(error_events) == 0

    @pytest.mark.asyncio
    async def test_typed_handler_error_isolated(
        self, bus
    ):
        """型付き: ハンドラーエラーで他が止まらない"""
        results = []

        async def bad_handler(event: ErrorEvent):
            raise RuntimeError("boom")

        async def good_handler(event: ErrorEvent):
            results.append("ok")

        bus.subscribe_typed(ErrorEvent, bad_handler)
        bus.subscribe_typed(ErrorEvent, good_handler)

        await bus.publish_typed(
            ErrorEvent(
                source="test",
                error_type="RuntimeError",
                message="boom",
            )
        )

        assert results == ["ok"]

    @pytest.mark.asyncio
    async def test_typed_no_handlers(self, bus):
        """ハンドラーなし型へのpublishはエラーなし"""
        await bus.publish_typed(
            TradeOpenedEvent(trade_id="T999")
        )

    @pytest.mark.asyncio
    async def test_unsubscribe_typed_nonexistent(
        self, bus
    ):
        """存在しないIDのunsubscribe_typedはエラーなし"""
        bus.unsubscribe_typed("nonexistent-id")

    @pytest.mark.asyncio
    async def test_typed_event_has_timestamp(self, bus):
        """型付きイベントにtimestampが自動設定される"""
        received = []

        async def handler(event: TradeOpenedEvent):
            received.append(event)

        bus.subscribe_typed(TradeOpenedEvent, handler)
        await bus.publish_typed(
            TradeOpenedEvent(trade_id="T004")
        )

        assert len(received) == 1
        assert received[0].timestamp is not None

    @pytest.mark.asyncio
    async def test_typed_events_are_frozen(self):
        """型付きイベントはfrozen（不変）"""
        evt = TradeOpenedEvent(trade_id="T005")
        with pytest.raises(AttributeError):
            evt.trade_id = "modified"  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_subscribe_typed_returns_unique_ids(
        self, bus
    ):
        """subscribe_typedは一意な購読IDを返す"""

        async def handler(event: TradeOpenedEvent):
            pass

        id1 = bus.subscribe_typed(
            TradeOpenedEvent, handler
        )
        id2 = bus.subscribe_typed(
            TradeOpenedEvent, handler
        )

        assert id1 != id2

    @pytest.mark.asyncio
    async def test_mixed_apis_independent(self, bus):
        """文字列APIと型付きAPIは独立して動作する"""
        str_results = []
        typed_results = []

        async def str_handler(data):
            str_results.append(data)

        async def typed_handler(
            event: TradeOpenedEvent,
        ):
            typed_results.append(event)

        bus.subscribe("trade.opened", str_handler)
        bus.subscribe_typed(
            TradeOpenedEvent, typed_handler
        )

        # 文字列APIで発行 → 文字列ハンドラーのみ
        await bus.publish(
            "trade.opened", {"trade_id": "T006"}
        )
        assert len(str_results) == 1
        assert len(typed_results) == 0

        # 型付きAPIで発行 → 型付きハンドラーのみ
        await bus.publish_typed(
            TradeOpenedEvent(trade_id="T007")
        )
        assert len(str_results) == 1
        assert len(typed_results) == 1


class TestEventBusSingleton:
    """get_event_bus / set_event_bus / reset_event_bus テスト"""

    def setup_method(self) -> None:
        """各テスト前に EventBus をリセット"""
        reset_event_bus()

    def teardown_method(self) -> None:
        """各テスト後にリセット"""
        reset_event_bus()

    def test_get_event_bus_returns_same_instance(self) -> None:
        """get_event_bus は同一インスタンスを返す"""
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2

    def test_set_event_bus_replaces_instance(self) -> None:
        """set_event_bus で差し替えたインスタンスが返される"""
        custom = EventBus()
        set_event_bus(custom)
        assert get_event_bus() is custom

    def test_reset_event_bus_creates_new(self) -> None:
        """reset 後は新しいインスタンスが生成される"""
        old = get_event_bus()
        reset_event_bus()
        new = get_event_bus()
        assert old is not new

    def test_set_none_resets(self) -> None:
        """set_event_bus(None) でリセットされる"""
        old = get_event_bus()
        set_event_bus(None)
        new = get_event_bus()
        assert old is not new
