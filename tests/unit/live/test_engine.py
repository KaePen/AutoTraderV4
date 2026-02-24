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


class TestSyncPositions:
    """_sync_positions ゴーストクリーンアップ安全性テスト"""

    @pytest.mark.asyncio
    async def test_MT5取得失敗時ゴースト掃除スキップ(
        self, engine: LiveTradingEngine
    ) -> None:
        """positions_getがNone→ゴースト掃除もDB復元もスキップ"""
        engine._conn = MagicMock()
        engine._conn.connected = True
        engine._conn.session = MagicMock()

        # MT5接続エラー: Noneを返す
        engine._executor.get_open_positions_async = AsyncMock(
            return_value=None
        )

        with (
            patch.object(
                engine, "_close_ghost_db_records"
            ) as mock_ghost,
            patch.object(
                engine, "_restore_open_trades_from_db"
            ) as mock_restore,
        ):
            await engine._sync_positions()

        # どちらも呼ばれない
        mock_ghost.assert_not_called()
        mock_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_空リスト時ゴースト掃除実行(
        self, engine: LiveTradingEngine
    ) -> None:
        """positions_getが空リスト→ゴースト掃除は実行される"""
        engine._conn = MagicMock()
        engine._conn.connected = True
        engine._conn.session = MagicMock()

        # MT5正常: 0件
        engine._executor.get_open_positions_async = AsyncMock(
            return_value=[]
        )

        with (
            patch.object(
                engine, "_close_ghost_db_records"
            ) as mock_ghost,
            patch.object(
                engine, "_restore_open_trades_from_db"
            ) as mock_restore,
        ):
            await engine._sync_positions()

        # ゴースト掃除は空セットで呼ばれる
        mock_ghost.assert_called_once_with(set())
        # DB復元はスキップ（ポジション0件）
        mock_restore.assert_not_called()

    @pytest.mark.asyncio
    async def test_ポジション存在時ゴースト掃除とDB復元(
        self, engine: LiveTradingEngine
    ) -> None:
        """MT5にポジションあり→ゴースト掃除とDB復元の両方実行"""
        engine._conn = MagicMock()
        engine._conn.connected = True
        engine._conn.session = MagicMock()

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

        with (
            patch.object(
                engine, "_close_ghost_db_records"
            ) as mock_ghost,
            patch.object(
                engine, "_restore_open_trades_from_db"
            ) as mock_restore,
            patch.object(
                engine, "_load_position_states",
                return_value={},
            ),
        ):
            await engine._sync_positions()

        # ゴースト掃除はticketセットで呼ばれる
        mock_ghost.assert_called_once_with({12345})
        # DB復元も呼ばれる
        mock_restore.assert_called_once_with([12345])


class TestManagePositionsNullSafety:
    """_manage_positions None安全性テスト"""

    @pytest.mark.asyncio
    async def test_MT5取得失敗時管理スキップ(
        self, engine: LiveTradingEngine
    ) -> None:
        """positions_getがNone→ポジション管理をスキップ"""
        engine._conn = MagicMock()
        engine._conn.connected = True
        engine._conn.session = MagicMock()
        engine._enable_auto_trade = True

        engine._executor.get_open_positions_async = AsyncMock(
            return_value=None
        )

        # 例外が発生しないことを確認
        await engine._manage_positions()

        # キャッシュが変更されないこと
        engine._executor.get_open_positions_async \
            .assert_called_once()


class TestExecuteEntryNullSafety:
    """_execute_entry None安全性テスト"""

    @pytest.mark.asyncio
    async def test_MT5取得失敗時エントリースキップ(
        self, engine: LiveTradingEngine
    ) -> None:
        """positions_getがNone→エントリーを安全にスキップ"""
        engine._conn = MagicMock()
        engine._conn.connected = True
        engine._conn.session = MagicMock()
        engine._enable_auto_trade = True
        engine._account_info = AccountInfo(
            balance=1000000, equity=1000000
        )

        engine._executor.get_open_positions_async = AsyncMock(
            return_value=None
        )
        engine._executor.open_position_async = AsyncMock()

        signal = Signal(
            signal_id="test-001",
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            confidence=0.8,
            stop_loss=149.5,
            take_profit=150.5,
        )

        await engine._execute_entry(signal)

        # MT5発注が呼ばれないこと
        engine._executor.open_position_async \
            .assert_not_called()


class TestCloseGhostDbRecords:
    """_close_ghost_db_records テスト"""

    def test_ゴーストレコードをis_open_falseに更新(
        self, engine: LiveTradingEngine
    ) -> None:
        """MT5に存在しないDBレコードが決済済みになる"""
        mock_ghost = MagicMock()
        mock_ghost.ticket = 99999
        mock_ghost.trade_id = "ghost-001"
        mock_ghost.is_open = True

        mock_active = MagicMock()
        mock_active.ticket = 12345
        mock_active.trade_id = "active-001"
        mock_active.is_open = True

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = [
            mock_ghost, mock_active,
        ]
        mock_session = MagicMock()
        mock_session.query.return_value = mock_query

        with (
            patch(
                "autotrader.config.settings.get_settings"
            ) as mock_settings,
            patch(
                "autotrader.adapters.database.connection"
                ".get_session"
            ) as mock_get_session,
        ):
            mock_settings.return_value.database_url = (
                "sqlite://"
            )
            mock_get_session.return_value.__enter__ = (
                MagicMock(return_value=mock_session)
            )
            mock_get_session.return_value.__exit__ = (
                MagicMock(return_value=False)
            )

            # ticket=12345のみMT5に存在
            engine._close_ghost_db_records({12345})

        # ゴーストのみ更新される
        assert mock_ghost.is_open is False
        assert mock_ghost.exit_reason == "GHOST_CLEANUP"
        assert mock_ghost.closed_at is not None
        # アクティブは変更なし
        assert mock_active.is_open is True
        mock_session.flush.assert_called_once()

    def test_ゴーストなしの場合flushされない(
        self, engine: LiveTradingEngine
    ) -> None:
        """全レコードがMT5に存在する場合はflushなし"""
        mock_record = MagicMock()
        mock_record.ticket = 12345
        mock_record.is_open = True

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = [
            mock_record,
        ]
        mock_session = MagicMock()
        mock_session.query.return_value = mock_query

        with (
            patch(
                "autotrader.config.settings.get_settings"
            ) as mock_settings,
            patch(
                "autotrader.adapters.database.connection"
                ".get_session"
            ) as mock_get_session,
        ):
            mock_settings.return_value.database_url = (
                "sqlite://"
            )
            mock_get_session.return_value.__enter__ = (
                MagicMock(return_value=mock_session)
            )
            mock_get_session.return_value.__exit__ = (
                MagicMock(return_value=False)
            )

            engine._close_ghost_db_records({12345})

        mock_session.flush.assert_not_called()

    def test_空セットで全レコードがゴーストになる(
        self, engine: LiveTradingEngine
    ) -> None:
        """MT5にポジション0件→全DBレコードがゴースト"""
        mock_r1 = MagicMock()
        mock_r1.ticket = 11111
        mock_r1.trade_id = "ghost-a"
        mock_r1.is_open = True

        mock_r2 = MagicMock()
        mock_r2.ticket = 22222
        mock_r2.trade_id = "ghost-b"
        mock_r2.is_open = True

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = [
            mock_r1, mock_r2,
        ]
        mock_session = MagicMock()
        mock_session.query.return_value = mock_query

        with (
            patch(
                "autotrader.config.settings.get_settings"
            ) as mock_settings,
            patch(
                "autotrader.adapters.database.connection"
                ".get_session"
            ) as mock_get_session,
        ):
            mock_settings.return_value.database_url = (
                "sqlite://"
            )
            mock_get_session.return_value.__enter__ = (
                MagicMock(return_value=mock_session)
            )
            mock_get_session.return_value.__exit__ = (
                MagicMock(return_value=False)
            )

            engine._close_ghost_db_records(set())

        assert mock_r1.is_open is False
        assert mock_r2.is_open is False
        mock_session.flush.assert_called_once()

    def test_DB例外時にログ出力しスキップ(
        self, engine: LiveTradingEngine
    ) -> None:
        """DB接続エラーでも例外は伝播しない"""
        with (
            patch(
                "autotrader.config.settings.get_settings",
                side_effect=Exception("DB接続失敗"),
            ),
        ):
            # 例外が伝播しないことを確認
            engine._close_ghost_db_records({12345})


class TestWriteCloseToDbPopTiming:
    """_write_close_to_db popタイミング修正テスト"""

    def test_DB書き込み失敗時にtrade_idが保持される(
        self, engine: LiveTradingEngine
    ) -> None:
        """DB書き込みエラー時、_open_tradesにtrade_idが残る"""
        engine._open_trades[12345] = "test-trade-id"
        engine._pm.get_position = MagicMock(
            return_value=None
        )

        with (
            patch(
                "autotrader.config.settings.get_settings",
                side_effect=Exception("DB接続失敗"),
            ),
        ):
            engine._write_close_to_db(
                ticket=12345,
                current_price=150.0,
                action_reason="SL_HIT",
            )

        # DB書き込み失敗時もtrade_idが残る
        assert 12345 in engine._open_trades
        assert engine._open_trades[12345] == "test-trade-id"

    def test_DB書き込み成功時にtrade_idがpopされる(
        self, engine: LiveTradingEngine
    ) -> None:
        """DB書き込み成功後、_open_tradesからtrade_idが除去"""
        engine._open_trades[12345] = "test-trade-id"
        engine._pm.get_position = MagicMock(
            return_value=None
        )

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = None
        mock_session = MagicMock()

        with (
            patch(
                "autotrader.config.settings.get_settings"
            ) as mock_settings,
            patch(
                "autotrader.adapters.database.connection"
                ".get_session"
            ) as mock_get_session,
            patch(
                "autotrader.adapters.database.repositories"
                ".TradeRepository",
                return_value=mock_repo,
            ),
        ):
            mock_settings.return_value.database_url = (
                "sqlite://"
            )
            mock_get_session.return_value.__enter__ = (
                MagicMock(return_value=mock_session)
            )
            mock_get_session.return_value.__exit__ = (
                MagicMock(return_value=False)
            )

            engine._write_close_to_db(
                ticket=12345,
                current_price=150.0,
                action_reason="SL_HIT",
            )

        # DB書き込み成功後にpopされる
        assert 12345 not in engine._open_trades


class TestChangeSymbol:
    """change_symbol() テスト"""

    def test_active_symbolが変更される(
        self, engine: LiveTradingEngine
    ) -> None:
        """active_symbolが初期値から変更される"""
        assert engine.active_symbol == "USDJPY"
        # change_symbolはasyncだがsymbol更新だけならsyncで検証
        engine._active_symbol = "EURUSD"
        assert engine.active_symbol == "EURUSD"

    @pytest.mark.asyncio
    async def test_change_symbol_sizer再構築(
        self, engine: LiveTradingEngine
    ) -> None:
        """change_symbolでPositionSizerが新シンボル用に再構築"""
        old_sizer = engine._sizer
        await engine.change_symbol("EURUSD")
        assert engine._sizer is not old_sizer
        assert engine.active_symbol == "EURUSD"

    @pytest.mark.asyncio
    async def test_change_symbol_tick_optimizer再構築(
        self, engine: LiveTradingEngine
    ) -> None:
        """change_symbolでTickEntryOptimizerが新シンボルで再作成"""
        old_optimizer = engine._tick_optimizer
        await engine.change_symbol("GBPUSD")
        assert engine._tick_optimizer is not old_optimizer
        assert engine._tick_optimizer._symbol == "GBPUSD"

    @pytest.mark.asyncio
    async def test_change_symbolキャッシュリセット(
        self, engine: LiveTradingEngine
    ) -> None:
        """change_symbolでlast_signal等がリセットされる"""
        engine._last_signal = MagicMock()
        engine._last_analysis = MagicMock()
        engine._last_tick_data = {"bid": 1.0}
        await engine.change_symbol("EURUSD")
        assert engine._last_signal is None
        assert engine._last_analysis is None
        assert engine._last_tick_data is None

    @pytest.mark.asyncio
    async def test_change_symbol_同一シンボルはスキップ(
        self, engine: LiveTradingEngine
    ) -> None:
        """同一シンボルではchange_symbolが何もしない"""
        old_sizer = engine._sizer
        await engine.change_symbol("USDJPY")
        assert engine._sizer is old_sizer

    @pytest.mark.asyncio
    async def test_set_symbol_auto_trade_異なるシンボル(
        self, engine: LiveTradingEngine
    ) -> None:
        """異なるシンボルでset_symbol_auto_tradeがchange_symbolを呼ぶ"""
        with patch.object(
            engine, "change_symbol", new_callable=AsyncMock
        ) as mock_change:
            await engine.set_symbol_auto_trade(
                "EURUSD", True
            )
            mock_change.assert_called_once_with("EURUSD")
        assert engine.enable_auto_trade is True

    @pytest.mark.asyncio
    async def test_set_symbol_auto_trade_同一シンボル(
        self, engine: LiveTradingEngine
    ) -> None:
        """同一シンボルではchange_symbolが呼ばれない"""
        with patch.object(
            engine, "change_symbol", new_callable=AsyncMock
        ) as mock_change:
            await engine.set_symbol_auto_trade(
                "USDJPY", True
            )
            mock_change.assert_not_called()
        assert engine.enable_auto_trade is True


class TestSharedConnection:
    """共有接続モードテスト"""

    def test_shared_connなしは自前接続(
        self, mock_config: LiveTradingConfig
    ) -> None:
        """shared_conn=Noneで_owns_connection=True"""
        engine = LiveTradingEngine(mock_config)
        assert engine._owns_connection is True

    def test_shared_conn渡しは非所有(
        self, mock_config: LiveTradingConfig
    ) -> None:
        """shared_conn渡しで_owns_connection=False"""
        shared = MagicMock()
        engine = LiveTradingEngine(
            mock_config, shared_conn=shared
        )
        assert engine._owns_connection is False
        assert engine._conn is shared

    def test_shared_data_provider渡し(
        self, mock_config: LiveTradingConfig
    ) -> None:
        """shared_data_provider渡しで共有"""
        shared_dp = MagicMock()
        engine = LiveTradingEngine(
            mock_config, shared_data_provider=shared_dp
        )
        assert engine._data_provider is shared_dp

    @pytest.mark.asyncio
    async def test_start_共有接続時はconnectスキップ(
        self, mock_config: LiveTradingConfig
    ) -> None:
        """共有接続時はstart()でconnect()を呼ばない"""
        shared = MagicMock()
        shared.connect = AsyncMock()
        shared.connected = True
        engine = LiveTradingEngine(
            mock_config, shared_conn=shared
        )
        engine._data_provider.get_account_info = AsyncMock(
            return_value=AccountInfo(
                balance=1000000, equity=1000000
            )
        )
        engine._data_provider.get_candles_from_pos = (
            AsyncMock(
                return_value=pd.DataFrame(
                    columns=[
                        "time", "open", "high",
                        "low", "close", "volume",
                    ]
                )
            )
        )
        engine._executor.get_open_positions_async = (
            AsyncMock(return_value=[])
        )

        with patch.object(
            engine, "_main_loop", new_callable=AsyncMock
        ):
            await engine.start()
            # connectは呼ばれない
            shared.connect.assert_not_called()
            assert engine.running is True

        # クリーンアップ
        engine._running = False

    @pytest.mark.asyncio
    async def test_stop_共有接続時はdisconnectスキップ(
        self, mock_config: LiveTradingConfig
    ) -> None:
        """共有接続時はstop()でdisconnect()を呼ばない"""
        shared = MagicMock()
        shared.connect = AsyncMock()
        shared.disconnect = AsyncMock()
        shared.connected = True
        engine = LiveTradingEngine(
            mock_config, shared_conn=shared
        )
        engine._running = True
        engine._task = None

        await engine.stop()
        # disconnectは呼ばれない
        shared.disconnect.assert_not_called()


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
