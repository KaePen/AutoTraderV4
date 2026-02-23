"""ライブトレーディングエンジンテスト

MockDataProvider/TradeExecutorでのエンジンテスト。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from autotrader.adapters.mt5.config import MT5Config
from autotrader.core.entities import AccountInfo, Position, Signal
from autotrader.core.enums import SignalType
from autotrader.core.interfaces.position_sizing import SizingResult
from autotrader.decision.unified.position_manager import (
    ManagementAction,
)
from autotrader.decision.unified.signal_consolidator import (
    ConsolidatedSignal,
)
from autotrader.live.config import LiveTradingConfig
from autotrader.live.engine import LiveTradingEngine


@pytest.fixture
def mock_config() -> LiveTradingConfig:
    """テスト用LiveTradingConfig"""
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
def engine(mock_config: LiveTradingConfig) -> LiveTradingEngine:
    """テスト用エンジン（接続なし）"""
    return LiveTradingEngine(mock_config)


class TestLiveTradingEngine:
    """LiveTradingEngine テスト"""

    def test_初期状態(self, engine: LiveTradingEngine) -> None:
        """初期状態が正しい"""
        assert engine.connected is False
        assert engine.running is False
        assert engine.account_info is None
        assert engine.enable_auto_trade is False

    def test_auto_trade設定(
        self, engine: LiveTradingEngine
    ) -> None:
        """auto_tradeのON/OFF切替"""
        engine.enable_auto_trade = True
        assert engine.enable_auto_trade is True
        engine.enable_auto_trade = False
        assert engine.enable_auto_trade is False

    @pytest.mark.asyncio
    async def test_エンジン開始停止(
        self, engine: LiveTradingEngine
    ) -> None:
        """エンジンの開始・停止が動作する"""
        # MT5接続をモック
        engine._conn = MagicMock()
        engine._conn.connect = AsyncMock()
        engine._conn.connected = True
        engine._conn.disconnect = AsyncMock()
        engine._conn.session = MagicMock()

        # データプロバイダをモック
        engine._data_provider.get_account_info = AsyncMock(
            return_value=AccountInfo(
                balance=1000000, equity=1000000
            )
        )
        engine._data_provider.get_candles_from_pos = AsyncMock(
            return_value=pd.DataFrame(
                columns=["time", "open", "high", "low",
                         "close", "volume"]
            )
        )

        # TradeExecutorをモック
        engine._executor.get_open_positions_async = AsyncMock(
            return_value=[]
        )

        await engine.start()
        assert engine.running is True
        assert engine.account_info is not None
        assert engine.account_info.balance == 1000000

        await engine.stop()
        assert engine.running is False

    @pytest.mark.asyncio
    async def test_ティック処理(
        self, engine: LiveTradingEngine
    ) -> None:
        """_tick()でシグナル生成とポジション管理が呼ばれる"""
        engine._conn = MagicMock()
        engine._conn.connected = True
        engine._conn.session = MagicMock()

        engine._data_provider.get_account_info = AsyncMock(
            return_value=AccountInfo(
                balance=1000000, equity=1000000
            )
        )
        engine._data_provider.get_candles_from_pos = AsyncMock(
            return_value=pd.DataFrame(
                columns=["time", "open", "high", "low",
                         "close", "volume"]
            )
        )
        engine._executor.get_open_positions_async = AsyncMock(
            return_value=[]
        )

        # generate_signalをモック（HOLD）
        engine._bot.generate_signal = MagicMock(
            return_value=ConsolidatedSignal(
                direction=SignalType.HOLD,
                confidence=0.0,
                primary_tf="M15",
                aligned_tfs=[],
                sl_pips=0.0,
                tp_pips=0.0,
                rationale="テスト",
            )
        )

        await engine._tick()

        # 口座情報が更新される
        assert engine.account_info is not None

    @pytest.mark.asyncio
    async def test_エントリー実行(
        self, engine: LiveTradingEngine
    ) -> None:
        """_execute_entry()でMT5発注が呼ばれる"""
        engine._conn = MagicMock()
        engine._conn.connected = True
        engine._conn.session = MagicMock()

        engine._account_info = AccountInfo(
            balance=1000000, equity=1000000
        )

        # ポジションなし
        engine._executor.get_open_positions_async = AsyncMock(
            return_value=[]
        )
        # ティック取得
        engine._data_provider.get_tick = AsyncMock(
            return_value={"ask": 150.123, "bid": 150.120}
        )
        # サイザーをモック
        engine._sizer.calculate = MagicMock(
            return_value=SizingResult(
                lot=1.0,
                risk_budget=20000,
                risk_adjust=1.0,
                reasoning="テスト",
                blocked=False,
            )
        )
        # 発注成功
        from autotrader.core.interfaces.trade_executor import (
            ExecutionResult,
        )
        engine._executor.open_position_async = AsyncMock(
            return_value=ExecutionResult(
                success=True, ticket=12345678
            )
        )

        signal = Signal(
            signal_id="test-001",
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            confidence=0.8,
            stop_loss=149.5,
            take_profit=150.5,
        )

        engine.enable_auto_trade = True
        with patch.object(
            engine, "_write_entry_to_db", return_value="test-id"
        ):
            await engine._execute_entry(signal)

        engine._executor.open_position_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_ポジション管理_HOLD(
        self, engine: LiveTradingEngine
    ) -> None:
        """HOLD時はMT5操作なし"""
        engine._conn = MagicMock()
        engine._conn.connected = True
        engine._conn.session = MagicMock()

        # ポジションなし
        engine._executor.get_open_positions_async = AsyncMock(
            return_value=[]
        )

        await engine._manage_positions()

        # 何も実行されない
        engine._executor.get_open_positions_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_ポジション管理_UPDATE_SL(
        self, engine: LiveTradingEngine
    ) -> None:
        """UPDATE_SL時にmodify_positionが呼ばれる"""
        engine._conn = MagicMock()
        engine._conn.connected = True
        engine._conn.session = MagicMock()
        engine._enable_auto_trade = True

        position = Position(
            position_id="12345",
            ticket=12345,
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            volume=1.0,
            entry_price=150.0,
            stop_loss=149.5,
            take_profit=150.5,
            opened_at=datetime.now(timezone.utc),
        )

        engine._executor.get_open_positions_async = AsyncMock(
            return_value=[position]
        )
        engine._data_provider.get_latest_candle_async = AsyncMock(
            return_value=pd.Series(
                {"high": 150.5, "low": 149.8}
            )
        )
        engine._data_provider.get_tick = AsyncMock(
            return_value={"ask": 150.3, "bid": 150.297}
        )
        from autotrader.core.interfaces.trade_executor import (
            ExecutionResult,
        )
        engine._executor.modify_position_async = AsyncMock(
            return_value=ExecutionResult(success=True, ticket=12345)
        )

        # PMにポジションを登録してUPDATE_SLを返すようモック
        engine._pm.evaluate = MagicMock(
            return_value=ManagementAction.update_sl(
                149.8, "トレーリング"
            )
        )
        engine._pm.get_position = MagicMock(
            return_value=MagicMock()
        )

        await engine._manage_positions()

        engine._executor.modify_position_async.assert_called_once()


class TestLiveTradingConfig:
    """LiveTradingConfig テスト"""

    def test_デフォルト値(self) -> None:
        """デフォルト値が正しい"""
        config = LiveTradingConfig()
        assert config.symbol == "USDJPY"
        assert config.candle_lookback == 500
        assert config.enable_auto_trade is False
        assert config.require_confirmation is True

    def test_frozen(self) -> None:
        """frozen dataclass"""
        config = LiveTradingConfig()
        with pytest.raises(Exception):
            config.symbol = "EURUSD"  # type: ignore[misc]
