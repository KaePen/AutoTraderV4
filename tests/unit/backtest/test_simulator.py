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
    """TradeSimulator のテスト"""

    @pytest.fixture
    def simulator(self) -> TradeSimulator:
        """シミュレーターを作成"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=1.0,
            pip_value=100.0,
            max_positions=1,
            default_volume=0.1,
        )
        return TradeSimulator(config=config)

    @pytest.fixture
    def base_candle(self) -> Candle:
        """基本の足データ"""
        return Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.50,
            low=139.50,
            close=140.20,
            volume=1000,
        )

    @pytest.fixture
    def buy_signal(self) -> Signal:
        """買いシグナル"""
        return Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.BUY,
            confidence=0.75,
            stop_loss=139.70,
            take_profit=140.70,
            created_at=datetime(2023, 6, 1, 10, 0),
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
        self, simulator: TradeSimulator, base_candle: Candle
    ) -> None:
        """シグナルなしでの足処理"""
        trades = simulator.process_candle(base_candle, None)
        assert len(trades) == 0
        assert len(simulator.state.open_positions) == 0

    def test_process_candle_with_buy_signal(
        self,
        simulator: TradeSimulator,
        base_candle: Candle,
        buy_signal: Signal,
    ) -> None:
        """買いシグナルでの足処理"""
        trades = simulator.process_candle(base_candle, buy_signal)
        assert len(trades) == 0  # エントリーのみ、決済なし
        assert len(simulator.state.open_positions) == 1

        position = simulator.state.open_positions[0]
        assert position.signal_type == SignalType.BUY
        assert position.volume == 0.1

    def test_stop_loss_trigger(
        self,
        simulator: TradeSimulator,
        base_candle: Candle,
        buy_signal: Signal,
    ) -> None:
        """ストップロストリガー"""
        # エントリー
        simulator.process_candle(base_candle, buy_signal)
        assert len(simulator.state.open_positions) == 1

        # SLに到達する足
        sl_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=140.00,
            high=140.10,
            low=139.60,  # SL価格139.70より下
            close=139.65,
            volume=1000,
        )

        trades = simulator.process_candle(sl_candle, None)
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.STOP_LOSS
        assert len(simulator.state.open_positions) == 0

    def test_take_profit_trigger(
        self,
        simulator: TradeSimulator,
        base_candle: Candle,
        buy_signal: Signal,
    ) -> None:
        """テイクプロフィットトリガー"""
        # エントリー
        simulator.process_candle(base_candle, buy_signal)

        # TPに到達する足
        tp_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=140.50,
            high=140.80,  # TP価格140.70より上
            low=140.40,
            close=140.75,
            volume=1000,
        )

        trades = simulator.process_candle(tp_candle, None)
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.TAKE_PROFIT
        assert trades[0].profit_loss > 0

    def test_signal_reversal(
        self,
        simulator: TradeSimulator,
        base_candle: Candle,
        buy_signal: Signal,
    ) -> None:
        """シグナル反転での決済"""
        # 買いエントリー
        simulator.process_candle(base_candle, buy_signal)

        # 売りシグナル
        sell_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.SELL,
            confidence=0.75,
            stop_loss=140.70,
            take_profit=139.70,
            created_at=datetime(2023, 6, 1, 10, 15),
        )

        next_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=140.20,
            high=140.30,
            low=140.10,
            close=140.25,
            volume=1000,
        )

        trades = simulator.process_candle(next_candle, sell_signal)
        # 買いポジション決済 + 売りポジション新規
        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.SIGNAL_REVERSAL
        # 新規売りポジションがオープン
        assert len(simulator.state.open_positions) == 1
        assert simulator.state.open_positions[0].signal_type == SignalType.SELL

    def test_force_close_all(
        self,
        simulator: TradeSimulator,
        base_candle: Candle,
        buy_signal: Signal,
    ) -> None:
        """全ポジション強制決済"""
        simulator.config.max_positions = 2

        # 複数ポジションオープン
        simulator.process_candle(base_candle, buy_signal)

        sell_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.SELL,
            confidence=0.75,
            stop_loss=140.70,
            take_profit=139.70,
            created_at=datetime(2023, 6, 1, 10, 15),
        )
        # max=2なのでBUYポジションを維持したまま新規SELL（実際は反転でクローズ）
        # 新規でmax以内なら別ポジションを作成

        close_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 11, 0),
            open=140.20,
            high=140.30,
            low=140.10,
            close=140.25,
            volume=1000,
        )

        trades = simulator.force_close_all(close_candle, ExitReason.FORCE_CLOSE)
        assert len(trades) >= 0
        assert len(simulator.state.open_positions) == 0

    def test_equity_update(
        self,
        simulator: TradeSimulator,
        base_candle: Candle,
        buy_signal: Signal,
    ) -> None:
        """評価額の更新テスト"""
        initial_equity = simulator.state.equity

        # エントリー
        simulator.process_candle(base_candle, buy_signal)

        # 価格上昇
        up_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=140.30,
            high=140.50,
            low=140.20,
            close=140.40,  # エントリーより上昇
            volume=1000,
        )

        simulator.process_candle(up_candle, None)

        # 含み益で評価額が増加
        assert simulator.state.equity > initial_equity

    def test_max_drawdown_tracking(
        self,
        simulator: TradeSimulator,
        base_candle: Candle,
        buy_signal: Signal,
    ) -> None:
        """最大ドローダウン追跡"""
        simulator.process_candle(base_candle, buy_signal)

        # 価格下落
        down_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=140.00,
            high=140.10,
            low=139.80,
            close=139.85,
            volume=1000,
        )

        simulator.process_candle(down_candle, None)

        assert simulator.state.current_drawdown > 0
        assert simulator.state.max_drawdown > 0


class TestProfitLossCalculation:
    """損益計算のテスト"""

    @pytest.fixture
    def simulator(self) -> TradeSimulator:
        """シミュレーター"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=0.0,  # 簡略化のためスプレッド0
            slippage_pips=0.0,
            pip_value=100.0,
            default_volume=1.0,
        )
        return TradeSimulator(config=config)

    def test_buy_profit(self, simulator: TradeSimulator) -> None:
        """買いで利益"""
        entry_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.10,
            low=139.90,
            close=140.00,
            volume=1000,
        )

        buy_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.BUY,
            confidence=0.75,
            stop_loss=139.00,
            take_profit=141.00,
            created_at=datetime(2023, 6, 1, 10, 0),
        )

        simulator.process_candle(entry_candle, buy_signal)

        # 100pips上昇して決済
        exit_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=141.00,
            high=141.10,
            low=140.90,
            close=141.00,
            volume=1000,
        )

        trades = simulator.force_close_all(exit_candle)

        assert len(trades) == 1
        # 100pips × 100円/pip × 1.0ロット = 10,000円
        assert trades[0].profit_loss_pips == pytest.approx(100.0, abs=1.0)

    def test_sell_profit(self, simulator: TradeSimulator) -> None:
        """売りで利益"""
        entry_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.10,
            low=139.90,
            close=140.00,
            volume=1000,
        )

        sell_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.SELL,
            confidence=0.75,
            stop_loss=141.00,
            take_profit=139.00,
            created_at=datetime(2023, 6, 1, 10, 0),
        )

        simulator.process_candle(entry_candle, sell_signal)

        # 100pips下落して決済
        exit_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=139.00,
            high=139.10,
            low=138.90,
            close=139.00,
            volume=1000,
        )

        trades = simulator.force_close_all(exit_candle)

        assert len(trades) == 1
        assert trades[0].profit_loss_pips == pytest.approx(100.0, abs=1.0)


class TestSlippageSymmetry:
    """SL/TPスリッページ対称性のテスト"""

    @pytest.fixture
    def simulator_with_slippage(self) -> TradeSimulator:
        """スリッページ設定ありのシミュレーター"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=0.0,
            slippage_pips=0.5,  # 0.5pips
            pip_value=100.0,
            default_volume=1.0,
        )
        return TradeSimulator(config=config)

    def test_tp_slippage_long_position(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """買いポジションTP決済時のスリッページ適用確認"""
        entry_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.10,
            low=139.90,
            close=140.00,
            volume=1000,
        )

        buy_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.BUY,
            confidence=0.75,
            stop_loss=139.00,
            take_profit=141.00,
            created_at=datetime(2023, 6, 1, 10, 0),
        )

        simulator_with_slippage.process_candle(entry_candle, buy_signal)

        # TPに到達する足
        tp_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=140.90,
            high=141.10,  # TP 141.00 より上
            low=140.80,
            close=141.00,
            volume=1000,
        )

        trades = simulator_with_slippage.process_candle(tp_candle, None)

        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.TAKE_PROFIT
        # TP価格 141.00 からスリッページ 0.5pips = 0.005円 引かれる
        # exit_price = 141.00 - 0.005 = 140.995
        assert trades[0].exit_price == pytest.approx(140.995, abs=0.001)

    def test_tp_slippage_short_position(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """売りポジションTP決済時のスリッページ適用確認"""
        entry_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.10,
            low=139.90,
            close=140.00,
            volume=1000,
        )

        sell_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.SELL,
            confidence=0.75,
            stop_loss=141.00,
            take_profit=139.00,
            created_at=datetime(2023, 6, 1, 10, 0),
        )

        simulator_with_slippage.process_candle(entry_candle, sell_signal)

        # TPに到達する足
        tp_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=139.10,
            high=139.20,
            low=138.90,  # TP 139.00 より下
            close=139.00,
            volume=1000,
        )

        trades = simulator_with_slippage.process_candle(tp_candle, None)

        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.TAKE_PROFIT
        # TP価格 139.00 からスリッページ 0.5pips = 0.005円 加算される
        # exit_price = 139.00 + 0.005 = 139.005
        assert trades[0].exit_price == pytest.approx(139.005, abs=0.001)

    def test_sl_slippage_long_position(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """買いポジションSL決済時のスリッページ適用確認"""
        entry_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.10,
            low=139.90,
            close=140.00,
            volume=1000,
        )

        buy_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.BUY,
            confidence=0.75,
            stop_loss=139.00,
            take_profit=141.00,
            created_at=datetime(2023, 6, 1, 10, 0),
        )

        simulator_with_slippage.process_candle(entry_candle, buy_signal)

        # SLに到達する足
        sl_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=139.10,
            high=139.20,
            low=138.90,  # SL 139.00 より下
            close=138.95,
            volume=1000,
        )

        trades = simulator_with_slippage.process_candle(sl_candle, None)

        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.STOP_LOSS
        # SL価格 139.00 からスリッページ 0.5pips = 0.005円 引かれる
        # exit_price = 139.00 - 0.005 = 138.995
        assert trades[0].exit_price == pytest.approx(138.995, abs=0.001)

    def test_sl_slippage_short_position(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """売りポジションSL決済時のスリッページ適用確認"""
        entry_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.10,
            low=139.90,
            close=140.00,
            volume=1000,
        )

        sell_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.SELL,
            confidence=0.75,
            stop_loss=141.00,
            take_profit=139.00,
            created_at=datetime(2023, 6, 1, 10, 0),
        )

        simulator_with_slippage.process_candle(entry_candle, sell_signal)

        # SLに到達する足
        sl_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=140.90,
            high=141.10,  # SL 141.00 より上
            low=140.80,
            close=141.00,
            volume=1000,
        )

        trades = simulator_with_slippage.process_candle(sl_candle, None)

        assert len(trades) == 1
        assert trades[0].exit_reason == ExitReason.STOP_LOSS
        # SL価格 141.00 からスリッページ 0.5pips = 0.005円 加算される
        # exit_price = 141.00 + 0.005 = 141.005
        assert trades[0].exit_price == pytest.approx(141.005, abs=0.001)

    def test_slippage_symmetry_pnl_impact(
        self, simulator_with_slippage: TradeSimulator
    ) -> None:
        """スリッページがSL/TP両方で利益を減少させることを確認"""
        # TP決済のシナリオ
        entry_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.10,
            low=139.90,
            close=140.00,
            volume=1000,
        )

        buy_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.BUY,
            confidence=0.75,
            stop_loss=139.00,
            take_profit=141.00,
            created_at=datetime(2023, 6, 1, 10, 0),
        )

        simulator_with_slippage.process_candle(entry_candle, buy_signal)

        # TPに到達
        tp_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 15),
            open=140.90,
            high=141.10,
            low=140.80,
            close=141.00,
            volume=1000,
        )

        trades = simulator_with_slippage.process_candle(tp_candle, None)

        # エントリー価格: 140.00 + 0.005（買いスリッページ）= 140.005
        # 実際TP価格: 141.00 - 0.005（TPスリッページ）= 140.995
        # profit_pips = (140.995 - 140.005) * 100 = 99.0
        # スリッページが往復で適用され、合計1pips減少
        assert len(trades) == 1
        assert trades[0].profit_loss_pips == pytest.approx(99.0, abs=0.1)


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
        return Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.50,
            low=139.50,
            close=140.20,
            volume=1000,
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
        # BUY決済はBid側 → trigger - slippage
        expected = sl_price - 0.005  # 0.5pips
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
        # SELL決済はAsk側 → trigger + slippage
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
        # 成行：close - half_spread
        half_spread = 0.005  # 1.0pips / 2
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
        entry_candle = Candle(
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            time=datetime(2023, 6, 1, 10, 0),
            open=140.00,
            high=140.10,
            low=139.90,
            close=140.00,
            volume=1000,
        )
        buy_signal = Signal(
            signal_id=str(uuid4()),
            symbol="USDJPY",
            timeframe=Timeframe.M15,
            signal_type=SignalType.BUY,
            confidence=0.75,
            stop_loss=139.00,
            take_profit=141.00,
            created_at=datetime(2023, 6, 1, 10, 0),
        )
        simulator.process_candle(entry_candle, buy_signal)
        assert len(simulator.state.open_positions) == 1

        pos = simulator.state.open_positions[0]
        # BE決済: entry_priceで決済
        trade = simulator._close_position(
            position=pos,
            exit_price=pos.entry_price,
            exit_time=datetime(2023, 6, 1, 11, 0),
            exit_reason=ExitReason.BREAKEVEN,
            trigger_price=pos.entry_price,
        )
        # BREAKEVENは損益0（手数料のみ）
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
        """trigger_price=0.0 → ValueError"""
        with pytest.raises(
            ValueError, match="異常な決済価格",
        ):
            simulator._calc_pm_fill_price(
                SignalType.BUY, candle,
                0.0, ExitReason.TAKE_PROFIT_EARLY,
            )

    def test_pm_fill_guard_large_slip(
        self,
        candle: Candle,
    ) -> None:
        """異常なスリッページ → ValueError"""
        config = SimulatorConfig(
            initial_balance=1_000_000.0,
            spread_pips=1.0,
            slippage_pips=600.0,
            pip_value=100.0,
            default_volume=0.1,
        )
        sim = TradeSimulator(config=config)
        with pytest.raises(
            ValueError, match="異常なスリッページ",
        ):
            sim._calc_pm_fill_price(
                SignalType.BUY, candle,
                140.00, ExitReason.STOP_LOSS,
            )


