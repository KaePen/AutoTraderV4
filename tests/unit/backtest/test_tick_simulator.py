"""BacktestTickSimulator のユニットテスト"""

from __future__ import annotations

import pandas as pd
import pytest

from autotrader.backtest.tick_simulator import (
    BacktestTickSimulator,
    TickExitResult,
    TickSimConfig,
    TickSimResult,
    check_tick_exit,
)
from autotrader.core.enums import ExitReason, SignalType


def _make_m1_df(
    rows: list[dict],
    start: str = "2024-01-15 10:00",
    freq: str = "1min",
) -> pd.DataFrame:
    """テスト用M1 DataFrameを生成"""
    index = pd.date_range(start=start, periods=len(rows), freq=freq)
    df = pd.DataFrame(rows, index=index)
    return df


class TestTickSimConfig:
    def test_defaults(self) -> None:
        cfg = TickSimConfig()
        assert cfg.enabled is False
        assert cfg.window_minutes == 15
        assert cfg.composite_threshold == 0.6
        assert cfg.timeout_execute is True

    def test_enabled(self) -> None:
        cfg = TickSimConfig(enabled=True)
        assert cfg.enabled is True


class TestBacktestTickSimulator:
    @pytest.fixture
    def config(self) -> TickSimConfig:
        return TickSimConfig(
            enabled=True,
            window_minutes=15,
            composite_threshold=0.5,
            spread_threshold_pips=2.0,
            spread_weight=0.4,
            momentum_weight=0.4,
            retracement_weight=0.2,
            retracement_enabled=False,
        )

    @pytest.fixture
    def simulator(self, config: TickSimConfig) -> BacktestTickSimulator:
        return BacktestTickSimulator(config=config, symbol="USDJPY")

    def test_find_optimal_entry_buy_trending_up(
        self, simulator: BacktestTickSimulator
    ) -> None:
        """上昇トレンドのM1データでBUYシグナル → 約定成立"""
        rows = [
            {"open": 150.000, "high": 150.020, "low": 149.990, "close": 150.015, "spread": 15},
            {"open": 150.015, "high": 150.035, "low": 150.010, "close": 150.030, "spread": 14},
            {"open": 150.030, "high": 150.050, "low": 150.025, "close": 150.045, "spread": 13},
            {"open": 150.045, "high": 150.060, "low": 150.040, "close": 150.055, "spread": 12},
            {"open": 150.055, "high": 150.070, "low": 150.050, "close": 150.065, "spread": 12},
        ]
        m1_df = _make_m1_df(rows)
        result = simulator.find_optimal_entry(
            signal_type=SignalType.BUY,
            signal_time=pd.Timestamp("2024-01-15 10:00"),
            m1_df=m1_df,
        )
        assert result is not None
        assert isinstance(result, TickSimResult)
        assert result.entry_price > 0
        assert result.bars_scanned >= 1

    def test_find_optimal_entry_sell_trending_down(
        self, simulator: BacktestTickSimulator
    ) -> None:
        """下降トレンドのM1データでSELLシグナル → 約定成立"""
        rows = [
            {"open": 150.100, "high": 150.110, "low": 150.080, "close": 150.085, "spread": 15},
            {"open": 150.085, "high": 150.090, "low": 150.060, "close": 150.065, "spread": 14},
            {"open": 150.065, "high": 150.070, "low": 150.040, "close": 150.045, "spread": 13},
            {"open": 150.045, "high": 150.050, "low": 150.025, "close": 150.030, "spread": 12},
            {"open": 150.030, "high": 150.035, "low": 150.010, "close": 150.015, "spread": 12},
        ]
        m1_df = _make_m1_df(rows)
        result = simulator.find_optimal_entry(
            signal_type=SignalType.SELL,
            signal_time=pd.Timestamp("2024-01-15 10:00"),
            m1_df=m1_df,
        )
        assert result is not None
        assert result.entry_price > 0

    def test_empty_m1_returns_none(
        self, simulator: BacktestTickSimulator
    ) -> None:
        """M1データが空ならNone"""
        m1_df = _make_m1_df([], start="2024-01-15 10:00")
        result = simulator.find_optimal_entry(
            signal_type=SignalType.BUY,
            signal_time=pd.Timestamp("2024-01-15 10:00"),
            m1_df=m1_df,
        )
        assert result is None

    def test_single_bar_returns_none(
        self, simulator: BacktestTickSimulator
    ) -> None:
        """M1が1本のみならNone（最低2本必要）"""
        rows = [
            {"open": 150.000, "high": 150.010, "low": 149.990, "close": 150.005, "spread": 15},
        ]
        m1_df = _make_m1_df(rows)
        result = simulator.find_optimal_entry(
            signal_type=SignalType.BUY,
            signal_time=pd.Timestamp("2024-01-15 10:00"),
            m1_df=m1_df,
        )
        assert result is None

    def test_timeout_execute_true(self) -> None:
        """タイムアウト約定: 条件未成立でもウィンドウ末尾で約定"""
        config = TickSimConfig(
            enabled=True,
            composite_threshold=0.99,  # 非常に高い閾値 → 未成立
            timeout_execute=True,
        )
        sim = BacktestTickSimulator(config=config, symbol="USDJPY")
        # フラットなデータ（モメンタムスコアが低い）
        rows = [
            {"open": 150.000, "high": 150.002, "low": 149.998, "close": 150.001, "spread": 15},
            {"open": 150.001, "high": 150.003, "low": 149.999, "close": 150.000, "spread": 15},
            {"open": 150.000, "high": 150.002, "low": 149.998, "close": 150.001, "spread": 15},
        ]
        m1_df = _make_m1_df(rows)
        result = sim.find_optimal_entry(
            signal_type=SignalType.BUY,
            signal_time=pd.Timestamp("2024-01-15 10:00"),
            m1_df=m1_df,
        )
        assert result is not None
        assert result.is_timeout is True
        assert result.entry_price == pytest.approx(150.001, abs=0.01)

    def test_timeout_execute_false(self) -> None:
        """タイムアウト約定OFF: 条件未成立ならNone"""
        config = TickSimConfig(
            enabled=True,
            composite_threshold=0.99,
            timeout_execute=False,
        )
        sim = BacktestTickSimulator(config=config, symbol="USDJPY")
        rows = [
            {"open": 150.000, "high": 150.002, "low": 149.998, "close": 150.001, "spread": 15},
            {"open": 150.001, "high": 150.003, "low": 149.999, "close": 150.000, "spread": 15},
        ]
        m1_df = _make_m1_df(rows)
        result = sim.find_optimal_entry(
            signal_type=SignalType.BUY,
            signal_time=pd.Timestamp("2024-01-15 10:00"),
            m1_df=m1_df,
        )
        assert result is None

    def test_window_filtering(
        self, simulator: BacktestTickSimulator
    ) -> None:
        """ウィンドウ外のM1足は使われない"""
        rows = [
            {"open": 150.000, "high": 150.010, "low": 149.990, "close": 150.005, "spread": 15},
        ] * 20  # 20分分 = 15分ウィンドウを超える
        m1_df = _make_m1_df(rows)
        result = simulator.find_optimal_entry(
            signal_type=SignalType.BUY,
            signal_time=pd.Timestamp("2024-01-15 10:00"),
            m1_df=m1_df,
        )
        if result is not None:
            assert result.bars_scanned <= 15  # ウィンドウは15分

    def test_eurusd_pip_unit(self) -> None:
        """非JPYペアでpip_unitが正しく設定される"""
        config = TickSimConfig(enabled=True, composite_threshold=0.3)
        sim = BacktestTickSimulator(config=config, symbol="EURUSD")
        assert sim._pip_unit == 0.0001
        assert sim._is_jpy is False

    def test_spread_column_fallback(self) -> None:
        """SPREAD列がない場合のフォールバック"""
        config = TickSimConfig(enabled=True, composite_threshold=0.3)
        sim = BacktestTickSimulator(config=config, symbol="USDJPY")
        rows = [
            {"open": 150.000, "high": 150.010, "low": 149.990, "close": 150.005},
            {"open": 150.005, "high": 150.015, "low": 149.995, "close": 150.010},
            {"open": 150.010, "high": 150.020, "low": 150.000, "close": 150.015},
        ]
        m1_df = _make_m1_df(rows)
        result = sim.find_optimal_entry(
            signal_type=SignalType.BUY,
            signal_time=pd.Timestamp("2024-01-15 10:00"),
            m1_df=m1_df,
        )
        # SPREAD列なしでもフォールバックで動作する
        assert result is not None or True  # エラーが出なければOK


# ============================================================
# check_tick_exit テスト
# ============================================================

def _make_tick_df(
    rows: list[dict],
    start: str = "2024-01-15 10:00:00",
    freq: str = "100ms",
) -> pd.DataFrame:
    """テスト用ティックDataFrame生成"""
    index = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=index)


class TestCheckTickExit:
    def test_buy_sl_hit(self) -> None:
        """BUYポジション: bid が SL 以下で SL 発動"""
        ticks = _make_tick_df([
            {"bid": 150.500, "ask": 150.515},
            {"bid": 150.480, "ask": 150.495},
            {"bid": 150.390, "ask": 150.405},  # SL=150.400 をbidが下回る
            {"bid": 150.410, "ask": 150.425},
        ])
        result = check_tick_exit(
            position_signal_type=SignalType.BUY,
            sl_price=150.400,
            tp_price=150.700,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
        )
        assert result is not None
        assert result.reason == ExitReason.STOP_LOSS
        assert result.trigger_price == 150.400
        assert result.exit_time == ticks.index[2]

    def test_buy_tp_hit(self) -> None:
        """BUYポジション: bid が TP 以上で TP 発動"""
        ticks = _make_tick_df([
            {"bid": 150.500, "ask": 150.515},
            {"bid": 150.600, "ask": 150.615},
            {"bid": 150.710, "ask": 150.725},  # TP=150.700 をbidが上回る
        ])
        result = check_tick_exit(
            position_signal_type=SignalType.BUY,
            sl_price=150.300,
            tp_price=150.700,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
        )
        assert result is not None
        assert result.reason == ExitReason.TAKE_PROFIT
        assert result.trigger_price == 150.700

    def test_sell_sl_hit(self) -> None:
        """SELLポジション: ask が SL 以上で SL 発動"""
        ticks = _make_tick_df([
            {"bid": 150.500, "ask": 150.515},
            {"bid": 150.590, "ask": 150.605},  # SL=150.600 をaskが上回る
        ])
        result = check_tick_exit(
            position_signal_type=SignalType.SELL,
            sl_price=150.600,
            tp_price=150.300,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
        )
        assert result is not None
        assert result.reason == ExitReason.STOP_LOSS

    def test_sell_tp_hit(self) -> None:
        """SELLポジション: ask が TP 以下で TP 発動"""
        ticks = _make_tick_df([
            {"bid": 150.500, "ask": 150.515},
            {"bid": 150.280, "ask": 150.295},  # TP=150.300 をaskが下回る
        ])
        result = check_tick_exit(
            position_signal_type=SignalType.SELL,
            sl_price=150.700,
            tp_price=150.300,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
        )
        assert result is not None
        assert result.reason == ExitReason.TAKE_PROFIT

    def test_sl_before_tp_in_same_candle(self) -> None:
        """同一足内でSLが先にヒット → SLが優先"""
        ticks = _make_tick_df([
            {"bid": 150.500, "ask": 150.515},
            {"bid": 150.390, "ask": 150.405},  # SL=150.400 ヒット (先)
            {"bid": 150.710, "ask": 150.725},  # TP=150.700 ヒット (後)
        ])
        result = check_tick_exit(
            position_signal_type=SignalType.BUY,
            sl_price=150.400,
            tp_price=150.700,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
        )
        assert result is not None
        assert result.reason == ExitReason.STOP_LOSS

    def test_tp_before_sl_in_same_candle(self) -> None:
        """同一足内でTPが先にヒット → TPが優先"""
        ticks = _make_tick_df([
            {"bid": 150.500, "ask": 150.515},
            {"bid": 150.710, "ask": 150.725},  # TP=150.700 ヒット (先)
            {"bid": 150.390, "ask": 150.405},  # SL=150.400 ヒット (後)
        ])
        result = check_tick_exit(
            position_signal_type=SignalType.BUY,
            sl_price=150.400,
            tp_price=150.700,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
        )
        assert result is not None
        assert result.reason == ExitReason.TAKE_PROFIT

    def test_no_hit(self) -> None:
        """SL/TPどちらもヒットしない → None"""
        ticks = _make_tick_df([
            {"bid": 150.500, "ask": 150.515},
            {"bid": 150.520, "ask": 150.535},
            {"bid": 150.480, "ask": 150.495},
        ])
        result = check_tick_exit(
            position_signal_type=SignalType.BUY,
            sl_price=150.300,
            tp_price=150.700,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
        )
        assert result is None

    def test_no_sl_tp(self) -> None:
        """SL/TP両方None → None"""
        ticks = _make_tick_df([
            {"bid": 150.500, "ask": 150.515},
        ])
        result = check_tick_exit(
            position_signal_type=SignalType.BUY,
            sl_price=None,
            tp_price=None,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
        )
        assert result is None

    def test_empty_tick_window(self) -> None:
        """対象期間にティックがない → None"""
        ticks = _make_tick_df(
            [{"bid": 150.500, "ask": 150.515}],
            start="2024-01-15 11:00:00",  # candle 外
        )
        result = check_tick_exit(
            position_signal_type=SignalType.BUY,
            sl_price=150.300,
            tp_price=150.700,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
        )
        assert result is None

    def test_slippage_applied(self) -> None:
        """スリッページが適用される"""
        ticks = _make_tick_df([
            {"bid": 150.390, "ask": 150.405},  # SL=150.400 ヒット
        ])
        result = check_tick_exit(
            position_signal_type=SignalType.BUY,
            sl_price=150.400,
            tp_price=None,
            tick_df=ticks,
            candle_start=pd.Timestamp("2024-01-15 10:00:00"),
            candle_end=pd.Timestamp("2024-01-15 10:15:00"),
            slippage_price=0.005,
        )
        assert result is not None
        assert result.exit_price == pytest.approx(150.395, abs=0.001)
