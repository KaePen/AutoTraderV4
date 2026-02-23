"""EngineManager テスト"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autotrader.adapters.mt5.config import MT5Config
from autotrader.live.config import LiveTradingConfig
from autotrader.live.engine_manager import EngineManager


@pytest.fixture
def mt5_config() -> MT5Config:
    """テスト用MT5Config"""
    return MT5Config(
        transport="direct",
        retry_count=1,
        retry_delay_sec=0.01,
    )


@pytest.fixture
def manager(mt5_config: MT5Config) -> EngineManager:
    """テスト用EngineManager"""
    return EngineManager(mt5_config)


@pytest.fixture
def usdjpy_config() -> LiveTradingConfig:
    """USDJPY用LiveTradingConfig"""
    return LiveTradingConfig(
        symbol="USDJPY",
        check_interval_sec=0.01,
        candle_lookback=10,
        mt5_config=MT5Config(
            transport="direct",
            retry_count=1,
            retry_delay_sec=0.01,
        ),
    )


@pytest.fixture
def eurusd_config() -> LiveTradingConfig:
    """EURUSD用LiveTradingConfig"""
    return LiveTradingConfig(
        symbol="EURUSD",
        check_interval_sec=0.01,
        candle_lookback=10,
        mt5_config=MT5Config(
            transport="direct",
            retry_count=1,
            retry_delay_sec=0.01,
        ),
    )


class TestEngineManagerInit:
    """初期化テスト"""

    def test_初期状態(
        self, manager: EngineManager,
    ) -> None:
        """初期状態が正しい"""
        assert manager.connected is False
        assert manager.engines == {}
        assert manager.symbols == []
        assert manager.account_info is None

    def test_all_cached_positions_空(
        self, manager: EngineManager,
    ) -> None:
        """エンジンなし時のポジション集約"""
        assert manager.all_cached_positions == []

    def test_symbol_auto_trade_states_空(
        self, manager: EngineManager,
    ) -> None:
        """エンジンなし時の自動取引状態"""
        assert manager.symbol_auto_trade_states == {}


class TestAddRemoveSymbol:
    """エンジン追加・除去テスト"""

    @pytest.mark.asyncio
    async def test_add_symbol(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
    ) -> None:
        """シンボル追加でエンジンが作成される"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            engine = await manager.add_symbol(
                usdjpy_config
            )
            assert "USDJPY" in manager.symbols
            assert engine is not None
            assert not engine._owns_connection

    @pytest.mark.asyncio
    async def test_add_symbol_duplicate(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
    ) -> None:
        """同じシンボルの重複追加は既存を返す"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            engine1 = await manager.add_symbol(
                usdjpy_config
            )
            engine2 = await manager.add_symbol(
                usdjpy_config
            )
            assert engine1 is engine2
            assert len(manager.symbols) == 1

    @pytest.mark.asyncio
    async def test_add_multiple_symbols(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
        eurusd_config: LiveTradingConfig,
    ) -> None:
        """複数シンボル追加"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            await manager.add_symbol(usdjpy_config)
            await manager.add_symbol(eurusd_config)
            assert len(manager.symbols) == 2
            assert "USDJPY" in manager.symbols
            assert "EURUSD" in manager.symbols

    @pytest.mark.asyncio
    async def test_remove_symbol(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
    ) -> None:
        """シンボル除去でエンジンが削除される"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            await manager.add_symbol(usdjpy_config)
            assert "USDJPY" in manager.symbols

            await manager.remove_symbol("USDJPY")
            assert "USDJPY" not in manager.symbols

    @pytest.mark.asyncio
    async def test_remove_nonexistent(
        self, manager: EngineManager,
    ) -> None:
        """存在しないシンボルの除去は何もしない"""
        await manager.remove_symbol("GBPJPY")
        assert manager.symbols == []


class TestGetEngine:
    """get_engine テスト"""

    @pytest.mark.asyncio
    async def test_get_engine_exists(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
    ) -> None:
        """登録済みシンボルのエンジンを取得"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            added = await manager.add_symbol(
                usdjpy_config
            )
            got = manager.get_engine("USDJPY")
            assert got is added

    def test_get_engine_not_exists(
        self, manager: EngineManager,
    ) -> None:
        """未登録シンボルはNoneを返す"""
        assert manager.get_engine("GBPJPY") is None


class TestSharedConnection:
    """共有接続テスト"""

    @pytest.mark.asyncio
    async def test_engines_share_connection(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
        eurusd_config: LiveTradingConfig,
    ) -> None:
        """全エンジンが同一接続インスタンスを共有"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            e1 = await manager.add_symbol(usdjpy_config)
            e2 = await manager.add_symbol(eurusd_config)
            assert e1._conn is e2._conn
            assert e1._data_provider is e2._data_provider

    @pytest.mark.asyncio
    async def test_shared_engine_does_not_own_conn(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
    ) -> None:
        """共有接続時は_owns_connection=False"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            engine = await manager.add_symbol(
                usdjpy_config
            )
            assert engine._owns_connection is False


class TestConnectDisconnect:
    """接続・切断テスト"""

    @pytest.mark.asyncio
    async def test_connect(
        self, manager: EngineManager,
    ) -> None:
        """MT5接続"""
        manager._conn = MagicMock()
        manager._conn.connected = False
        manager._conn.connect = AsyncMock()
        await manager.connect()
        manager._conn.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect(
        self, manager: EngineManager,
    ) -> None:
        """MT5切断"""
        manager._conn = MagicMock()
        manager._conn.connected = True
        manager._conn.disconnect = AsyncMock()
        await manager.disconnect()
        manager._conn.disconnect.assert_called_once()


class TestAggregation:
    """集約プロパティテスト"""

    @pytest.mark.asyncio
    async def test_symbol_auto_trade_states(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
        eurusd_config: LiveTradingConfig,
    ) -> None:
        """シンボル別自動取引状態の集約"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            e1 = await manager.add_symbol(usdjpy_config)
            e2 = await manager.add_symbol(eurusd_config)
            e1.enable_auto_trade = True
            e2.enable_auto_trade = False

            states = manager.symbol_auto_trade_states
            assert states["USDJPY"] is True
            assert states["EURUSD"] is False

    @pytest.mark.asyncio
    async def test_all_cached_positions(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
        eurusd_config: LiveTradingConfig,
    ) -> None:
        """全エンジンのポジション集約"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            e1 = await manager.add_symbol(usdjpy_config)
            e2 = await manager.add_symbol(eurusd_config)
            e1._cached_positions = [
                {"symbol": "USDJPY", "ticket": 1}
            ]
            e2._cached_positions = [
                {"symbol": "EURUSD", "ticket": 2}
            ]

            positions = manager.all_cached_positions
            assert len(positions) == 2
            symbols = {p["symbol"] for p in positions}
            assert symbols == {"USDJPY", "EURUSD"}

    @pytest.mark.asyncio
    async def test_trade_history(
        self,
        manager: EngineManager,
        usdjpy_config: LiveTradingConfig,
    ) -> None:
        """トレード履歴の集約"""
        with patch.object(
            manager, "_conn", MagicMock(connected=False)
        ):
            e1 = await manager.add_symbol(usdjpy_config)
            e1._closed_trades = [
                {"trade_id": "t1", "ticket": 1}
            ]
            assert len(manager.trade_history) == 1
