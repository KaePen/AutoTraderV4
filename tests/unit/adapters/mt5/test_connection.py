"""MT5接続テスト

MockTransportを使用した接続管理テスト。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from autotrader.adapters.mt5.config import MT5Config
from autotrader.adapters.mt5.connection import (
    BridgeTransport,
    MT5ConnectionManager,
    MT5Transport,
)
from autotrader.adapters.mt5.exceptions import (
    MT5BridgeError,
    MT5ConnectionError,
)


class MockTransport(MT5Transport):
    """テスト用モックトランスポート"""

    def __init__(self, fail_init: bool = False) -> None:
        self.fail_init = fail_init
        self.initialized = False
        self.logged_in = False
        self.shutdown_called = False

    async def initialize(self) -> bool:
        if self.fail_init:
            raise MT5ConnectionError("初期化失敗")
        self.initialized = True
        return True

    async def login(
        self, login: int, password: str, server: str
    ) -> bool:
        self.logged_in = True
        return True

    async def shutdown(self) -> None:
        self.shutdown_called = True

    async def account_info(self) -> dict:
        return {
            "balance": 1000000,
            "equity": 1050000,
            "margin": 50000,
            "margin_free": 1000000,
            "margin_level": 2100,
            "profit": 50000,
        }

    async def symbol_info(self, symbol: str) -> dict:
        return {"name": symbol, "point": 0.001, "digits": 3}

    async def symbol_info_tick(self, symbol: str) -> dict:
        return {"ask": 150.123, "bid": 150.120, "last": 150.121}

    async def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> list[dict]:
        return [
            {
                "time": 1700000000 + i * 60,
                "open": 150.0 + i * 0.01,
                "high": 150.1 + i * 0.01,
                "low": 149.9 + i * 0.01,
                "close": 150.05 + i * 0.01,
                "tick_volume": 100,
            }
            for i in range(count)
        ]

    async def copy_rates_range(
        self, symbol: str, timeframe: int,
        date_from: int, date_to: int,
    ) -> list[dict]:
        return []

    async def order_send(self, request: dict) -> dict:
        return {"retcode": 10009, "order": 12345678}

    async def positions_get(
        self, symbol: str | None = None
    ) -> list[dict]:
        return []

    async def history_deals_get(
        self, date_from: int, date_to: int
    ) -> list[dict]:
        return []


class TestMT5ConnectionManager:
    """MT5ConnectionManager テスト"""

    @pytest.mark.asyncio
    async def test_正常接続(self) -> None:
        """正常にMT5に接続できる"""
        config = MT5Config(transport="direct")
        manager = MT5ConnectionManager(config)
        # トランスポートをモックに差替え
        mock_transport = MockTransport()
        manager._transport = mock_transport

        await manager.connect()

        assert manager.connected is True
        assert mock_transport.initialized is True

    @pytest.mark.asyncio
    async def test_接続失敗リトライ(self) -> None:
        """接続失敗時にリトライする"""
        config = MT5Config(
            transport="direct",
            retry_count=2,
            retry_delay_sec=0.01,
        )
        manager = MT5ConnectionManager(config)
        mock_transport = MockTransport(fail_init=True)
        manager._transport = mock_transport

        with pytest.raises(MT5ConnectionError):
            await manager.connect()

        assert manager.connected is False

    @pytest.mark.asyncio
    async def test_切断(self) -> None:
        """切断が正しく動作する"""
        config = MT5Config(transport="direct")
        manager = MT5ConnectionManager(config)
        mock_transport = MockTransport()
        manager._transport = mock_transport

        await manager.connect()
        await manager.disconnect()

        assert manager.connected is False
        assert mock_transport.shutdown_called is True

    @pytest.mark.asyncio
    async def test_ヘルスチェック_正常(self) -> None:
        """ヘルスチェックが正常に動作する"""
        config = MT5Config(transport="direct")
        manager = MT5ConnectionManager(config)
        mock_transport = MockTransport()
        manager._transport = mock_transport

        await manager.connect()
        result = await manager.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_ヘルスチェック_未接続(self) -> None:
        """未接続時のヘルスチェックはFalse"""
        config = MT5Config(transport="direct")
        manager = MT5ConnectionManager(config)

        result = await manager.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_セッションコンテキストマネージャ(self) -> None:
        """sessionコンテキストマネージャが動作する"""
        config = MT5Config(transport="direct")
        manager = MT5ConnectionManager(config)
        mock_transport = MockTransport()
        manager._transport = mock_transport

        await manager.connect()

        async with manager.session() as transport:
            info = await transport.account_info()
            assert info["balance"] == 1000000

    @pytest.mark.asyncio
    async def test_ensure_connected_再接続(self) -> None:
        """ensure_connectedで未接続時に再接続"""
        config = MT5Config(
            transport="direct",
            health_check_interval_sec=0,
        )
        manager = MT5ConnectionManager(config)
        mock_transport = MockTransport()
        manager._transport = mock_transport

        # 未接続状態からensure_connected
        await manager.ensure_connected()

        assert manager.connected is True


class TestBridgeTransport:
    """BridgeTransport テスト"""

    def test_初期化(self) -> None:
        """BridgeTransportが正しく初期化される"""
        transport = BridgeTransport(
            host="192.168.1.1", port=9999, timeout=5.0
        )
        assert transport._host == "192.168.1.1"
        assert transport._port == 9999
        assert transport._timeout == 5.0

    @pytest.mark.asyncio
    async def test_接続失敗(self) -> None:
        """存在しないホストへの接続はMT5BridgeErrorを送出"""
        transport = BridgeTransport(
            host="127.0.0.1", port=1, timeout=0.1
        )
        with pytest.raises(MT5BridgeError):
            await transport.initialize()


class TestMT5ConfigDefaults:
    """MT5Config デフォルト値テスト"""

    def test_デフォルト値(self) -> None:
        """デフォルト値が正しく設定される"""
        config = MT5Config()
        assert config.transport == "bridge"
        assert config.bridge_host == "localhost"
        assert config.bridge_port == 18812
        assert config.magic_number == 20240001
        assert config.retry_count == 3

    def test_frozenフィールド(self) -> None:
        """frozenなのでフィールド変更不可"""
        config = MT5Config()
        with pytest.raises(Exception):
            config.login = 12345  # type: ignore[misc]
