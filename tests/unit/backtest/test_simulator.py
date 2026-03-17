"""トレードシミュレーターのユニットテスト"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from autotrader.backtest.simulator import (
    TradeSimulator,
    SimulatorConfig,
)
from autotrader.core.entities import Candle, Signal
from autotrader.core.enums import Timeframe, SignalType, ExitReason
from autotrader.core.exceptions import BacktestError


def _make_candle(
    time: datetime,
    open: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1000,
) -> Candle:
    """テスト用Candle生成ヘルパー"""
    return Candle(
        symbol="USDJPY",
        timeframe=Timeframe.M15,
        time=time,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _make_signal(
    signal_type: SignalType,
    stop_loss: float,
    take_profit: float,
    time: datetime | None = None,
) -> Signal:
    """テスト用Signal生成ヘルパー"""
    return Signal(
        signal_id=str(uuid4()),
        symbol="USDJPY",
        timeframe=Timeframe.M15,
        signal_type=signal_type,
        confidence=0.75,
        stop_loss=stop_loss,
        take_profit=take_profit,
        created_at=time or datetime(2023, 6, 1, 10, 0),
    )


class TestSimulatorConfig:
    """SimulatorConfig のテスト"""

    def test_default_values(self) -> None:
        """デフォルト値の確認"""
        config = SimulatorConfig()
        assert config.initial_balance == 1_000_000.0
        assert config.spread_pips == 1.5
        assert config.pip_value == 100.0
        assert config.max_positions == 1
        assert config.default_volume == 0.1

    def test_custom_values(self) -> None:
        """カスタム値の確認"""
        config = SimulatorConfig(
            initial_balance=500_000.0,
            spread_pips=2.0,
            max_positions=3,
        )
        assert config.initial_balance == 500_000.0
        assert config.spread_pips == 2.0
        assert config.max_positions == 3


class TestTradeSimulator:
    """TradeSimulator のテスト

    sl_tp_in_pips=False, use_position_manager=False で
    基本動作（非PM経路）を検証する。
    """

    @pytest.fixture
    def simulator(self) -> TradeSimulator:
        """シミュレーターを作成（非PM、絶対価格SL/TP）"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=1.0,
            slippage_pips=0.0,
            pip_value=100.0,
            max_positions=1,
            default_volume=0.1,
            use_position_manager=False,
            sl_tp_in_pips=False,
        )
        return TradeSimulator(config=config)

    @pytest.fixture
    def buy_signal(self) -> Signal:
        """買いシグナル（絶対価格SL/TP）"""
        return _make_signal(
            SignalType.BUY,
            stop_loss=139.70,
            take_profit=140.70,
        )

    def test_initial_state(self, simulator: TradeSimulator) -> None:
        """初期状態の確認"""
        assert simulator.state.balance == 1_000_000.0
        assert simulator.state.equity == 1_000_000.0
        assert len(simulator.state.open_positions) == 0
        assert len(simulator.state.closed_trades) == 0

    def test_reset(self, simulator: TradeSimulator) -> None:
        """リセット機能のテスト"""
        simulator.state.balance = 500_000.0
        simulator.reset()
        assert simulator.state.balance == 1_000_000.0

    def test_process_candle_no_signal(
        self, simulator: TradeSimulator,
    ) -> None:
        """シグナルなしでの足処理"""
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        trades = simulator.process_candle(candle, None)
        assert len(trades) == 0
        assert len(simulator.state.open_positions) == 0

    def test_process_candle_with_buy_signal(
        self,
        simulator: TradeSimulator,
        buy_signal: Signal,
    ) -> None:
        """買いシグナルでの足処理（次足open約定）"""
        # シグナル足 → pendingに保存
        candle1 = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        trades = simulator.process_candle(candle1, buy_signal)
        assert len(trades) == 0
        assert len(simulator.state.open_positions) == 0
        assert simulator._pending_signal is not None

        # 次足 → pendingがopenで約定
        candle2 = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20, high=140.50,
            low=139.80, close=140.10,
        )
        trades = simulator.process_candle(candle2, None)
        assert len(trades) == 0
        assert len(simulator.state.open_positions) == 1

        position = simulator.state.open_positions[0]
        assert position.signal_type == SignalType.BUY
        assert position.volume == 0.1
        # spread=1.0pip=0.01, half=0.005, slip=0
        # BUY: open(140.20) + half_spread(0.005) = 140.205
        assert position.entry_price == pytest.approx(
            140.205, abs=0.001,
        )

    def test_stop_loss_trigger(
        self,
        simulator: TradeSimulator,
        buy_signal: Signal,
    ) -> None:
        """ストップロストリガー"""
        # シグナル足
        candle1 = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        simulator.process_candle(candle1, buy_signal)

        # 次足でpending約定（SLに到達しない足）
        candle2 = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20, high=140.30,
            low=140.00, close=140.10,
        )
        simulator.process_candle(candle2, None)
        assert len(simulator.state.open_positions) == 1

        # SLに到達する足（SL=139.70、low=139.60 < SL）
        sl_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=140.00, high=140.10,
            low=139.60, close=139.65,
        )
        trades = simulator.process_candle(sl_candle, None)
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.STOP_LOSS
        assert len(simulator.state.open_positions) == 0

    def test_take_profit_trigger(
        self,
        simulator: TradeSimulator,
        buy_signal: Signal,
    ) -> None:
        """テイクプロフィットトリガー"""
        # シグナル足
        candle1 = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        simulator.process_candle(candle1, buy_signal)

        # 次足でpending約定
        candle2 = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20, high=140.30,
            low=140.00, close=140.25,
        )
        simulator.process_candle(candle2, None)

        # TPに到達する足（TP=140.70、high=140.80 > TP）
        tp_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=140.50, high=140.80,
            low=140.40, close=140.75,
        )
        trades = simulator.process_candle(tp_candle, None)
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.TAKE_PROFIT
        assert trades[0].profit_loss > 0

    def test_signal_reversal(
        self,
        simulator: TradeSimulator,
        buy_signal: Signal,
    ) -> None:
        """シグナル反転での決済"""
        # 買いシグナル足
        candle1 = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        simulator.process_candle(candle1, buy_signal)

        # 次足でpending約定
        candle2 = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20, high=140.30,
            low=140.10, close=140.25,
        )
        simulator.process_candle(candle2, None)
        assert len(simulator.state.open_positions) == 1

        # 売りシグナル
        sell_signal = _make_signal(
            SignalType.SELL,
            stop_loss=140.70,
            take_profit=139.70,
            time=datetime(2023, 6, 1, 10, 30),
        )

        reversal_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=140.20, high=140.30,
            low=140.10, close=140.25,
        )
        trades = simulator.process_candle(
            reversal_candle, sell_signal,
        )
        # 買いポジション決済
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.SIGNAL_REVERSAL

        # 売りシグナルはpendingに保存
        assert simulator._pending_signal is not None
        assert (
            simulator._pending_signal.signal_type
            == SignalType.SELL
        )

        # 次足で売りポジションがopenで約定
        next_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 45),
            open=140.20, high=140.30,
            low=140.10, close=140.15,
        )
        trades = simulator.process_candle(next_candle, None)
        assert len(simulator.state.open_positions) == 1
        assert (
            simulator.state.open_positions[0].signal_type
            == SignalType.SELL
        )

    def test_force_close_all(
        self,
        simulator: TradeSimulator,
        buy_signal: Signal,
    ) -> None:
        """全ポジション強制決済"""
        # シグナル足
        candle1 = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        simulator.process_candle(candle1, buy_signal)

        # 次足でpending約定
        candle2 = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20, high=140.30,
            low=140.10, close=140.25,
        )
        simulator.process_candle(candle2, None)

        close_candle = _make_candle(
            time=datetime(2023, 6, 1, 11, 0),
            open=140.20, high=140.30,
            low=140.10, close=140.25,
        )
        trades = simulator.force_close_all(
            close_candle, ExitReason.FORCE_CLOSE,
        )
        assert len(trades) >= 1
        assert len(simulator.state.open_positions) == 0

    def test_equity_update(
        self,
        simulator: TradeSimulator,
        buy_signal: Signal,
    ) -> None:
        """評価額の更新テスト"""
        initial_equity = simulator.state.equity

        # シグナル足
        candle1 = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        simulator.process_candle(candle1, buy_signal)

        # 次足でpending約定
        candle2 = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20, high=140.30,
            low=140.10, close=140.25,
        )
        simulator.process_candle(candle2, None)

        # 価格上昇
        up_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=140.30, high=140.50,
            low=140.20, close=140.40,
        )
        simulator.process_candle(up_candle, None)

        # 含み益で評価額が増加
        assert simulator.state.equity > initial_equity

    def test_max_drawdown_tracking(
        self,
        simulator: TradeSimulator,
        buy_signal: Signal,
    ) -> None:
        """最大ドローダウン追跡"""
        # シグナル足
        candle1 = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        simulator.process_candle(candle1, buy_signal)

        # 次足でpending約定
        candle2 = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20, high=140.30,
            low=140.10, close=140.25,
        )
        simulator.process_candle(candle2, None)

        # 価格下落
        down_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=140.00, high=140.10,
            low=139.80, close=139.85,
        )
        simulator.process_candle(down_candle, None)

        assert simulator.state.current_drawdown > 0
        assert simulator.state.max_drawdown > 0


class TestProfitLossCalculation:
    """損益計算のテスト"""

    @pytest.fixture
    def simulator(self) -> TradeSimulator:
        """シミュレーター（非PM、スプレッド0）"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=0.0,
            slippage_pips=0.0,
            pip_value=100.0,
            default_volume=1.0,
            use_position_manager=False,
            sl_tp_in_pips=False,
        )
        return TradeSimulator(config=config)

    def test_buy_profit(self, simulator: TradeSimulator) -> None:
        """買いで利益"""
        entry_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.10,
            low=139.90, close=140.00,
        )
        buy_signal = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        simulator.process_candle(entry_candle, buy_signal)

        # 次足でpending約定（open=140.00 → entry=140.00）
        fill_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.00, high=140.10,
            low=139.90, close=140.05,
        )
        simulator.process_candle(fill_candle, None)

        # 100pips上昇して決済
        exit_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=141.00, high=141.10,
            low=140.90, close=141.00,
        )
        trades = simulator.force_close_all(exit_candle)
        assert len(trades) == 1
        assert trades[0].profit_loss_pips == pytest.approx(
            100.0, abs=1.0,
        )

    def test_sell_profit(self, simulator: TradeSimulator) -> None:
        """売りで利益"""
        entry_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.10,
            low=139.90, close=140.00,
        )
        sell_signal = _make_signal(
            SignalType.SELL,
            stop_loss=141.00,
            take_profit=139.00,
        )
        simulator.process_candle(entry_candle, sell_signal)

        # 次足でpending約定（open=140.00 → entry=140.00）
        fill_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.00, high=140.10,
            low=139.90, close=139.95,
        )
        simulator.process_candle(fill_candle, None)

        # 100pips下落して決済
        exit_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=139.00, high=139.10,
            low=138.90, close=139.00,
        )
        trades = simulator.force_close_all(exit_candle)
        assert len(trades) == 1
        assert trades[0].profit_loss_pips == pytest.approx(
            100.0, abs=1.0,
        )


class TestSlippageSymmetry:
    """SL/TPスリッページ対称性のテスト"""

    @pytest.fixture
    def simulator_with_slippage(self) -> TradeSimulator:
        """スリッページ設定ありのシミュレーター（非PM）"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=0.0,
            slippage_pips=0.5,
            pip_value=100.0,
            default_volume=1.0,
            use_position_manager=False,
            sl_tp_in_pips=False,
        )
        return TradeSimulator(config=config)

    def _entry_and_fill(
        self,
        simulator: TradeSimulator,
        signal: Signal,
        fill_open: float = 140.00,
    ) -> None:
        """シグナル→次足open約定のヘルパー"""
        entry_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.10,
            low=139.90, close=140.00,
        )
        simulator.process_candle(entry_candle, signal)

        fill_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=fill_open, high=fill_open + 0.10,
            low=fill_open - 0.10, close=fill_open,
        )
        simulator.process_candle(fill_candle, None)

    def test_tp_slippage_long_position(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """買いポジションTP決済時のスリッページ適用確認"""
        buy_signal = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        self._entry_and_fill(simulator_with_slippage, buy_signal)

        tp_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=140.90, high=141.10,
            low=140.80, close=141.00,
        )
        trades = simulator_with_slippage.process_candle(
            tp_candle, None,
        )
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.TAKE_PROFIT
        # TP 141.00 - slip 0.005 = 140.995
        assert trades[0].exit_price == pytest.approx(
            140.995, abs=0.001,
        )

    def test_tp_slippage_short_position(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """売りポジションTP決済時のスリッページ適用確認"""
        sell_signal = _make_signal(
            SignalType.SELL,
            stop_loss=141.00,
            take_profit=139.00,
        )
        self._entry_and_fill(simulator_with_slippage, sell_signal)

        tp_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=139.10, high=139.20,
            low=138.90, close=139.00,
        )
        trades = simulator_with_slippage.process_candle(
            tp_candle, None,
        )
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.TAKE_PROFIT
        # TP 139.00 + slip 0.005 = 139.005
        assert trades[0].exit_price == pytest.approx(
            139.005, abs=0.001,
        )

    def test_sl_slippage_long_position(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """買いポジションSL決済時のスリッページ適用確認"""
        buy_signal = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        self._entry_and_fill(simulator_with_slippage, buy_signal)

        sl_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=139.10, high=139.20,
            low=138.90, close=138.95,
        )
        trades = simulator_with_slippage.process_candle(
            sl_candle, None,
        )
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.STOP_LOSS
        # SL 139.00 - slip 0.005 = 138.995
        assert trades[0].exit_price == pytest.approx(
            138.995, abs=0.001,
        )

    def test_sl_slippage_short_position(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """売りポジションSL決済時のスリッページ適用確認"""
        sell_signal = _make_signal(
            SignalType.SELL,
            stop_loss=141.00,
            take_profit=139.00,
        )
        self._entry_and_fill(simulator_with_slippage, sell_signal)

        sl_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=140.90, high=141.10,
            low=140.80, close=141.00,
        )
        trades = simulator_with_slippage.process_candle(
            sl_candle, None,
        )
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.STOP_LOSS
        # SL 141.00 + slip 0.005 = 141.005
        assert trades[0].exit_price == pytest.approx(
            141.005, abs=0.001,
        )

    def test_slippage_symmetry_pnl_impact(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """スリッページがSL/TP両方で利益を減少させることを確認"""
        buy_signal = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        self._entry_and_fill(simulator_with_slippage, buy_signal)

        tp_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 30),
            open=140.90, high=141.10,
            low=140.80, close=141.00,
        )
        trades = simulator_with_slippage.process_candle(
            tp_candle, None,
        )
        # entry: fill_candle.open=140.00 + slip=0.005 = 140.005
        # TP: 141.00 - slip=0.005 = 140.995
        # pips = (140.995 - 140.005) / 0.01 = 99.0
        assert len(trades) == 1
        assert trades[0].profit_loss_pips == pytest.approx(
            99.0, abs=0.1,
        )


class TestPMFillPrice:
    """PM経路の決済価格テスト"""

    @pytest.fixture
    def simulator(self) -> TradeSimulator:
        """スリッページあり・スプレッドありシミュレーター"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=1.0,
            slippage_pips=0.5,
            pip_value=100.0,
            default_volume=0.1,
        )
        return TradeSimulator(config=config)

    @pytest.fixture
    def candle(self) -> Candle:
        """テスト用足データ"""
        return _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )

    def test_pm_sl_fill_buy(
        self,
        simulator: TradeSimulator,
        candle: Candle,
    ) -> None:
        """BUY SL hit → fill = sl - slip"""
        sl_price = 139.50
        fill = simulator._calc_pm_fill_price(
            SignalType.BUY, candle,
            sl_price, ExitReason.STOP_LOSS,
        )
        expected = sl_price - 0.005
        assert fill == pytest.approx(expected, abs=0.0001)

    def test_pm_sl_fill_sell(
        self,
        simulator: TradeSimulator,
        candle: Candle,
    ) -> None:
        """SELL SL hit → fill = sl + slip"""
        sl_price = 140.50
        fill = simulator._calc_pm_fill_price(
            SignalType.SELL, candle,
            sl_price, ExitReason.STOP_LOSS,
        )
        expected = sl_price + 0.005
        assert fill == pytest.approx(expected, abs=0.0001)

    def test_pm_tp_fill_buy(
        self,
        simulator: TradeSimulator,
        candle: Candle,
    ) -> None:
        """BUY TP hit → fill = tp - slip"""
        tp_price = 141.00
        fill = simulator._calc_pm_fill_price(
            SignalType.BUY, candle,
            tp_price, ExitReason.TAKE_PROFIT,
        )
        expected = tp_price - 0.005
        assert fill == pytest.approx(expected, abs=0.0001)

    def test_pm_time_exit_uses_market(
        self,
        simulator: TradeSimulator,
        candle: Candle,
    ) -> None:
        """TIME_EXIT → fill = close ± half_spread（成行）"""
        fill = simulator._calc_pm_fill_price(
            SignalType.BUY, candle,
            0.0, ExitReason.TIME_EXIT,
        )
        half_spread = 0.005
        expected = candle.close - half_spread
        assert fill == pytest.approx(expected, abs=0.0001)

    def test_pm_signal_reversal_uses_market(
        self,
        simulator: TradeSimulator,
        candle: Candle,
    ) -> None:
        """SIGNAL_REVERSAL → fill = close ± half_spread"""
        fill = simulator._calc_pm_fill_price(
            SignalType.SELL, candle,
            0.0, ExitReason.SIGNAL_REVERSAL,
        )
        half_spread = 0.005
        expected = candle.close + half_spread
        assert fill == pytest.approx(expected, abs=0.0001)

    def test_pm_partial_1r_fill(
        self,
        simulator: TradeSimulator,
        candle: Candle,
    ) -> None:
        """1R部分利確 → fill = 1r_price ± slip"""
        _1r_price = 140.80
        fill = simulator._calc_pm_fill_price(
            SignalType.BUY, candle,
            _1r_price, ExitReason.TAKE_PROFIT_1R,
        )
        expected = _1r_price - 0.005
        assert fill == pytest.approx(expected, abs=0.0001)

    def test_pm_be_hit_fill(
        self,
        simulator: TradeSimulator,
        candle: Candle,
    ) -> None:
        """BE_HIT → fill = be_price ± slip"""
        be_price = 140.25
        fill = simulator._calc_pm_fill_price(
            SignalType.BUY, candle,
            be_price, ExitReason.BREAKEVEN,
        )
        expected = be_price - 0.005
        assert fill == pytest.approx(expected, abs=0.0001)

    def test_pm_be_hit_zero_pnl(
        self,
        simulator: TradeSimulator,
    ) -> None:
        """BE_HIT → profit_loss = 0"""
        entry_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.10,
            low=139.90, close=140.00,
        )
        buy_signal = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        simulator.process_candle(entry_candle, buy_signal)

        # 次足でpending約定
        fill_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.00, high=140.10,
            low=139.90, close=140.05,
        )
        simulator.process_candle(fill_candle, None)
        assert len(simulator.state.open_positions) == 1

        pos = simulator.state.open_positions[0]
        trade = simulator._close_position(
            position=pos,
            exit_price=pos.entry_price,
            exit_time=datetime(2023, 6, 1, 11, 0),
            exit_reason=ExitReason.BREAKEVEN,
            trigger_price=pos.entry_price,
        )
        commission = (
            simulator.config.commission_per_lot
            * pos.volume
        )
        assert trade.profit_loss == pytest.approx(
            -commission, abs=0.01,
        )
        assert trade.profit_loss_pips == pytest.approx(
            0.0, abs=0.01,
        )

    def test_pm_fill_guard_low_price(
        self,
        simulator: TradeSimulator,
        candle: Candle,
    ) -> None:
        """trigger_price=0.0 → BacktestError"""
        with pytest.raises(
            BacktestError, match="異常な決済価格",
        ):
            simulator._calc_pm_fill_price(
                SignalType.BUY, candle,
                0.0, ExitReason.TAKE_PROFIT_EARLY,
            )

    def test_pm_fill_guard_large_slip(
        self,
        candle: Candle,
    ) -> None:
        """異常なスリッページ → BacktestError"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=1.0,
            slippage_pips=600.0,
            pip_value=100.0,
            default_volume=0.1,
        )
        sim = TradeSimulator(config=config)
        with pytest.raises(
            BacktestError, match="異常なスリッページ",
        ):
            sim._calc_pm_fill_price(
                SignalType.BUY, candle,
                140.00, ExitReason.STOP_LOSS,
            )


class TestPendingSignalEntry:
    """次足open約定（pending signal）のテスト"""

    @pytest.fixture
    def simulator(self) -> TradeSimulator:
        """シミュレーター（非PM、スプレッド0）"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=0.0,
            slippage_pips=0.0,
            pip_value=100.0,
            max_positions=1,
            default_volume=0.1,
            use_position_manager=False,
            sl_tp_in_pips=False,
        )
        return TradeSimulator(config=config)

    def test_pending_not_executed_on_signal_bar(
        self, simulator: TradeSimulator,
    ) -> None:
        """シグナル足ではポジションが作られない"""
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        signal = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        simulator.process_candle(candle, signal)
        assert len(simulator.state.open_positions) == 0
        assert simulator._pending_signal is not None

    def test_pending_executed_on_next_bar_open(
        self, simulator: TradeSimulator,
    ) -> None:
        """pendingシグナルが次足openで約定"""
        candle1 = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        signal = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        simulator.process_candle(candle1, signal)

        candle2 = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.30, high=140.50,
            low=140.00, close=140.20,
        )
        simulator.process_candle(candle2, None)

        assert len(simulator.state.open_positions) == 1
        pos = simulator.state.open_positions[0]
        # entry = candle2.open = 140.30（spread=0, slip=0）
        assert pos.entry_price == pytest.approx(
            140.30, abs=0.001,
        )
        assert simulator._pending_signal is None

    def test_pending_cleared_on_force_close(
        self, simulator: TradeSimulator,
    ) -> None:
        """force_close_allでpendingがクリアされる"""
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        signal = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        simulator.process_candle(candle, signal)
        assert simulator._pending_signal is not None

        close_candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20, high=140.30,
            low=140.10, close=140.15,
        )
        simulator.force_close_all(close_candle)
        assert simulator._pending_signal is None

    def test_pending_cleared_on_reset(
        self, simulator: TradeSimulator,
    ) -> None:
        """resetでpendingがクリアされる"""
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        signal = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        simulator.process_candle(candle, signal)
        assert simulator._pending_signal is not None

        simulator.reset()
        assert simulator._pending_signal is None

    def test_pending_respects_max_positions(
        self,
    ) -> None:
        """pendingエントリーがmax_positions制約を守る"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=0.0,
            slippage_pips=0.0,
            pip_value=100.0,
            max_positions=1,
            default_volume=0.1,
            use_position_manager=False,
            sl_tp_in_pips=False,
        )
        simulator = TradeSimulator(config=config)

        # 1つ目のシグナル
        candle1 = _make_candle(
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        signal1 = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=141.00,
        )
        simulator.process_candle(candle1, signal1)

        # 次足で約定 + 新しいシグナル（同方向）
        candle2 = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20, high=140.50,
            low=140.00, close=140.30,
        )
        signal2 = _make_signal(
            SignalType.BUY,
            stop_loss=139.00,
            take_profit=142.00,
            time=datetime(2023, 6, 1, 10, 15),
        )
        simulator.process_candle(candle2, signal2)

        # max_positions=1なので2つ目はpendingにならない
        assert len(simulator.state.open_positions) == 1


class TestPMIntrabarSLTP:
    """PM経路のintrabar SL/TP判定テスト"""

    @pytest.fixture
    def simulator(self) -> TradeSimulator:
        """シミュレーター"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=0.0,
            slippage_pips=0.5,
            pip_value=100.0,
            max_positions=1,
            default_volume=0.1,
        )
        return TradeSimulator(config=config)

    def test_intrabar_sl_buy(
        self, simulator: TradeSimulator,
    ) -> None:
        """BUY: bar内でSLに到達（low <= sl）"""
        from autotrader.core.entities import Position

        pos = Position(
            position_id="test-1",
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            volume=0.1,
            entry_price=140.00,
            stop_loss=139.50,
            take_profit=141.00,
            opened_at=datetime(2023, 6, 1, 10, 0),
        )
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=139.80, high=139.90,
            low=139.40, close=139.70,
        )
        result = simulator._check_intrabar_sl_tp(pos, candle)
        assert result is not None
        fill, reason, trigger = result
        assert reason == ExitReason.STOP_LOSS
        assert trigger == 139.50
        # fill = sl - slip = 139.50 - 0.005
        assert fill == pytest.approx(139.495, abs=0.001)

    def test_intrabar_sl_buy_gap(
        self, simulator: TradeSimulator,
    ) -> None:
        """BUY: openがSL以下（ギャップダウン）"""
        from autotrader.core.entities import Position

        pos = Position(
            position_id="test-2",
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            volume=0.1,
            entry_price=140.00,
            stop_loss=139.50,
            take_profit=141.00,
            opened_at=datetime(2023, 6, 1, 10, 0),
        )
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=139.30, high=139.40,
            low=139.20, close=139.35,
        )
        result = simulator._check_intrabar_sl_tp(pos, candle)
        assert result is not None
        fill, reason, _ = result
        assert reason == ExitReason.STOP_LOSS
        # ギャップ: fill = open - slip
        assert fill == pytest.approx(139.295, abs=0.001)

    def test_intrabar_tp_sell(
        self, simulator: TradeSimulator,
    ) -> None:
        """SELL: bar内でTPに到達（low <= tp）"""
        from autotrader.core.entities import Position

        pos = Position(
            position_id="test-3",
            symbol="USDJPY",
            signal_type=SignalType.SELL,
            volume=0.1,
            entry_price=140.00,
            stop_loss=141.00,
            take_profit=139.00,
            opened_at=datetime(2023, 6, 1, 10, 0),
        )
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=139.20, high=139.30,
            low=138.90, close=139.10,
        )
        result = simulator._check_intrabar_sl_tp(pos, candle)
        assert result is not None
        fill, reason, trigger = result
        assert reason == ExitReason.TAKE_PROFIT
        assert trigger == 139.00
        # fill = tp + slip
        assert fill == pytest.approx(139.005, abs=0.001)

    def test_intrabar_no_breach(
        self, simulator: TradeSimulator,
    ) -> None:
        """SLにもTPにも到達しない場合はNone"""
        from autotrader.core.entities import Position

        pos = Position(
            position_id="test-4",
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            volume=0.1,
            entry_price=140.00,
            stop_loss=139.00,
            take_profit=141.00,
            opened_at=datetime(2023, 6, 1, 10, 0),
        )
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.00, high=140.50,
            low=139.50, close=140.20,
        )
        result = simulator._check_intrabar_sl_tp(pos, candle)
        assert result is None


def _make_position(
    signal_type: SignalType,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
) -> "Position":
    """テスト用Position生成ヘルパー"""
    from autotrader.core.entities import Position
    return Position(
        position_id=str(uuid4()),
        symbol="USDJPY",
        signal_type=signal_type,
        volume=0.1,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        opened_at=datetime(2023, 6, 1, 10, 0),
    )


class TestSlExitSpread:
    """SL約定時スプレッド不利分のテスト"""

    def _make_simulator(
        self, *, enabled: bool, factor: float = 0.5,
    ) -> TradeSimulator:
        """SL exit spread設定付きシミュレーター"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=2.0,
            pip_value=100.0,
            pip_unit=0.01,
            max_positions=1,
            default_volume=0.1,
            slippage_pips=0.5,
            use_position_manager=False,
            sl_exit_spread_enabled=enabled,
            sl_exit_spread_factor=factor,
        )
        return TradeSimulator(config)

    def test_sl_exit_spread_disabled(self):
        """OFF時はSL fillにスプレッド不利分なし"""
        sim = self._make_simulator(enabled=False)
        pos = _make_position(
            signal_type=SignalType.BUY,
            entry_price=140.00,
            stop_loss=139.50,
            take_profit=141.00,
        )
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=139.60, high=139.60,
            low=139.40, close=139.45,
        )
        sim._current_candle_spread = 0.02  # 2pips
        result = sim._check_exit_conditions(pos, candle)
        assert result is not None
        fill, reason, trigger = result
        assert reason == ExitReason.STOP_LOSS
        # fill = sl - slip のみ（spread不利なし）
        expected = 139.50 - 0.005  # sl - slippage
        assert abs(fill - expected) < 1e-6

    def test_sl_exit_spread_buy_enabled(self):
        """BUY SL: fill = sl - slip - half_spread*factor"""
        sim = self._make_simulator(
            enabled=True, factor=0.5,
        )
        pos = _make_position(
            signal_type=SignalType.BUY,
            entry_price=140.00,
            stop_loss=139.50,
            take_profit=141.00,
        )
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=139.60, high=139.60,
            low=139.40, close=139.45,
        )
        sim._current_candle_spread = 0.02  # 2pips
        result = sim._check_exit_conditions(pos, candle)
        assert result is not None
        fill, reason, trigger = result
        assert reason == ExitReason.STOP_LOSS
        # fill = sl - slip - (half_spread * factor)
        # = 139.50 - 0.005 - (0.01 * 0.5)
        expected = 139.50 - 0.005 - 0.005
        assert abs(fill - expected) < 1e-6

    def test_sl_exit_spread_sell_enabled(self):
        """SELL SL: fill = sl + slip + half_spread*factor"""
        sim = self._make_simulator(
            enabled=True, factor=0.5,
        )
        pos = _make_position(
            signal_type=SignalType.SELL,
            entry_price=140.00,
            stop_loss=140.50,
            take_profit=139.00,
        )
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.40, high=140.60,
            low=140.30, close=140.55,
        )
        sim._current_candle_spread = 0.02  # 2pips
        result = sim._check_exit_conditions(pos, candle)
        assert result is not None
        fill, reason, trigger = result
        assert reason == ExitReason.STOP_LOSS
        # fill = sl + slip + (half_spread * factor)
        # = 140.50 + 0.005 + (0.01 * 0.5)
        expected = 140.50 + 0.005 + 0.005
        assert abs(fill - expected) < 1e-6

    def test_tp_not_affected(self):
        """TP約定にはスプレッド不利分が加算されない"""
        sim = self._make_simulator(
            enabled=True, factor=0.5,
        )
        pos = _make_position(
            signal_type=SignalType.BUY,
            entry_price=140.00,
            stop_loss=139.50,
            take_profit=141.00,
        )
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=140.90, high=141.10,
            low=140.80, close=141.05,
        )
        sim._current_candle_spread = 0.02  # 2pips
        result = sim._check_exit_conditions(pos, candle)
        assert result is not None
        fill, reason, trigger = result
        assert reason == ExitReason.TAKE_PROFIT
        # fill = tp - slip のみ（spread不利なし）
        expected = 141.00 - 0.005
        assert abs(fill - expected) < 1e-6

    def test_intrabar_sl_spread(self):
        """_check_intrabar_sl_tp でも同様にスプレッド不利"""
        sim = self._make_simulator(
            enabled=True, factor=0.5,
        )
        pos = _make_position(
            signal_type=SignalType.BUY,
            entry_price=140.00,
            stop_loss=139.50,
            take_profit=141.00,
        )
        candle = _make_candle(
            time=datetime(2023, 6, 1, 10, 15),
            open=139.60, high=139.60,
            low=139.40, close=139.45,
        )
        sim._current_candle_spread = 0.02
        result = sim._check_intrabar_sl_tp(pos, candle)
        assert result is not None
        fill, reason, trigger = result
        assert reason == ExitReason.STOP_LOSS
        expected = 139.50 - 0.005 - 0.005
        assert abs(fill - expected) < 1e-6
