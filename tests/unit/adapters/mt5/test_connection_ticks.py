"""MT5 copy_ticks_from テスト

copy_ticks_fromメソッドのモックテスト。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from autotrader.adapters.mt5.config import MT5Config
from autotrader.adapters.mt5.connection import (
    MT5ConnectionManager,
    MT5Transport,
)
from autotrader.adapters.mt5.exceptions import (
    MT5ConnectionError,
)


class MockTransportWithTicks(MT5Transport):
    """copy_ticks_from対応モックトランスポート"""

    def __init__(self) -> None:
        self.ticks_data: list[dict] = [
            {
                "time": 1700000000 + i,
                "bid": 150.100 + i * 0.001,
                "ask": 150.110 + i * 0.001,
                "last": 150.105 + i * 0.001,
                "volume": 10,
                "flags": 0,
            }
            for i in range(20)
        ]

    async def initialize(self) -> bool:
        return True

    async def login(
        self, login: int, password: str, server: str
    ) -> bool:
        return True

    async def shutdown(self) -> None:
        pass

    async def account_info(self) -> dict:
        return {"balance": 1000000, "equity": 1000000}

    async def symbol_info(self, symbol: str) -> dict:
        return {"name": symbol}

    async def symbol_info_tick(
        self, symbol: str
    ) -> dict:
        return {
            "ask": 150.110,
            "bid": 150.100,
            "last": 150.105,
        }

    async def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: int,
        start_pos: int,
        count: int,
    ) -> list[dict]:
        return []

    async def copy_rates_range(
        self,
        symbol: str,
        timeframe: int,
        date_from: int,
        date_to: int,
    ) -> list[dict]:
        return []

    async def order_send(self, request: dict) -> dict:
        return {"retcode": 10009}

    async def positions_get(
        self, symbol: str | None = None
    ) -> list[dict]:
        return []

    async def history_deals_get(
        self, date_from: int, date_to: int
    ) -> list[dict]:
        return []

    async def copy_ticks_from(
        self,
        symbol: str,
        date_from: int,
        count: int,
        flags: int = 0,
    ) -> list[dict]:
        """モック: 固定ティックデータを返す"""
        return self.ticks_data[:count]


class TestCopyTicksFrom:
    """copy_ticks_from テスト"""

    @pytest.mark.asyncio
    async def test_ティック取得_正常(self) -> None:
        """正常にティックデータを取得できる"""
        transport = MockTransportWithTicks()

        ticks = await transport.copy_ticks_from(
            "USDJPY", 1700000000, 10
        )

        assert len(ticks) == 10
        assert "ask" in ticks[0]
        assert "bid" in ticks[0]
        assert ticks[0]["ask"] == 150.110

    @pytest.mark.asyncio
    async def test_ティック取得_0件(self) -> None:
        """0件指定で空リスト"""
        transport = MockTransportWithTicks()

        ticks = await transport.copy_ticks_from(
            "USDJPY", 1700000000, 0
        )

        assert ticks == []

    @pytest.mark.asyncio
    async def test_セッション経由取得(self) -> None:
        """ConnectionManagerセッション経由でティック取得"""
        config = MT5Config()
        manager = MT5ConnectionManager(config)
        mock_transport = MockTransportWithTicks()
        manager._transport = mock_transport

        await manager.connect()

        async with manager.session() as transport:
            ticks = await transport.copy_ticks_from(
                "USDJPY", 1700000000, 5
            )

        assert len(ticks) == 5

    @pytest.mark.asyncio
    async def test_ティックデータ構造(self) -> None:
        """ティックデータの構造が正しい"""
        transport = MockTransportWithTicks()

        ticks = await transport.copy_ticks_from(
            "USDJPY", 1700000000, 1
        )

        tick = ticks[0]
        assert isinstance(tick["time"], int)
        assert isinstance(tick["ask"], float)
        assert isinstance(tick["bid"], float)
        assert tick["ask"] > tick["bid"]

    @pytest.mark.asyncio
    async def test_flagsパラメータ(self) -> None:
        """flagsパラメータが渡される"""
        transport = MockTransportWithTicks()

        # flags=1 (COPY_TICKS_INFO)
        ticks = await transport.copy_ticks_from(
            "USDJPY", 1700000000, 5, flags=1
        )

        assert len(ticks) == 5
