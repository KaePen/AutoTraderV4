"""ストレステスト用シミュレーターパラメータのユニットテスト"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from autotrader.backtest.simulator import (
    SimulatorConfig,
    TradeSimulator,
)
from autotrader.core.entities import Candle, Signal
from autotrader.core.enums import SignalType, Timeframe


def _make_candle(
    time: datetime,
    open_: float,
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
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _make_signal(
    signal_type: SignalType,
    stop_loss: float = 10.0,
    take_profit: float = 20.0,
    time: datetime | None = None,
    lot: float | None = None,
    consensus_score: float | None = None,
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
        lot=lot,
        consensus_score=consensus_score,
    )


def _base_config(**kwargs: object) -> SimulatorConfig:
    """テスト用の基本設定"""
    defaults: dict[str, object] = {
        "initial_balance": 1_000_000.0,
        "spread_pips": 1.5,
        "slippage_pips": 0.5,
        "pip_unit": 0.01,
        "pip_value": 1000.0,
        "max_positions": 1,
        "use_position_manager": False,
        "use_dynamic_lot": False,
        "default_volume": 0.1,
        "sl_tp_in_pips": True,
    }
    defaults.update(kwargs)
    return SimulatorConfig(**defaults)  # type: ignore[arg-type]


BASE_TIME = datetime(2023, 6, 1, 10, 0)


def _candles(n: int, start_price: float = 150.0) -> list[Candle]:
    """連続するN本のCandle生成"""
    result = []
    for i in range(n):
        t = BASE_TIME + timedelta(minutes=15 * i)
        p = start_price + i * 0.01
        result.append(
            _make_candle(t, p, p + 0.05, p - 0.05, p + 0.005),
        )
    return result


class TestStressValidation:
    """ストレスパラメータのバリデーションテスト"""

    def test_fill_failure_rate_out_of_range(self) -> None:
        """fill_failure_rate が [0,1] 範囲外でエラー"""
        with pytest.raises(ValueError, match="fill_failure_rate"):
            _base_config(fill_failure_rate=1.5)
        with pytest.raises(ValueError, match="fill_failure_rate"):
            _base_config(fill_failure_rate=-0.1)

    def test_partial_fill_ratio_out_of_range(self) -> None:
        """partial_fill_ratio が (0,1] 範囲外でエラー"""
        with pytest.raises(ValueError, match="partial_fill_ratio"):
            _base_config(partial_fill_ratio=0.0)
        with pytest.raises(ValueError, match="partial_fill_ratio"):
            _base_config(partial_fill_ratio=-0.5)

    def test_signal_skip_rate_out_of_range(self) -> None:
        """signal_skip_rate が [0,1] 範囲外でエラー"""
        with pytest.raises(ValueError, match="signal_skip_rate"):
            _base_config(signal_skip_rate=2.0)

    def test_entry_delay_bars_negative(self) -> None:
        """entry_delay_bars が負でエラー"""
        with pytest.raises(ValueError, match="entry_delay_bars"):
            _base_config(entry_delay_bars=-1)

    def test_valid_boundary_values(self) -> None:
        """境界値でエラーにならないこと"""
        # 全てエラーなし
        _base_config(fill_failure_rate=0.0)
        _base_config(fill_failure_rate=1.0)
        _base_config(partial_fill_ratio=0.01)
        _base_config(partial_fill_ratio=1.0)
        _base_config(signal_skip_rate=0.0)
        _base_config(signal_skip_rate=1.0)
        _base_config(entry_delay_bars=0)


class TestStressDefaults:
    """ストレスパラメータのデフォルト値テスト"""

    def test_defaults_are_inactive(self) -> None:
        """全デフォルト値が無効状態であること"""
        config = SimulatorConfig()
        assert config.slippage_extra_pips == 0.0
        assert config.slippage_random_max_pips == 0.0
        assert config.fill_failure_rate == 0.0
        assert config.partial_fill_ratio == 1.0
        assert config.entry_delay_bars == 0
        assert config.price_noise_pips == 0.0
        assert config.signal_skip_rate == 0.0

    def test_default_config_no_behavior_change(self) -> None:
        """デフォルト設定でトレード結果が変わらないこと"""
        config = _base_config()
        sim = TradeSimulator(config)
        candles = _candles(5)
        sig = _make_signal(SignalType.BUY)

        # シグナル足 → 次足で約定
        sim.process_candle(candles[0], sig)
        sim.process_candle(candles[1])

        assert len(sim.state.open_positions) == 1


class TestFillFailureRate:
    """約定失敗率のテスト"""

    def test_fill_failure_rate_1_no_positions(self) -> None:
        """fill_failure_rate=1.0で全約定失敗→ポジション0件"""
        config = _base_config(fill_failure_rate=1.0)
        sim = TradeSimulator(config)
        candles = _candles(20)

        for i in range(0, 18, 3):
            sig = _make_signal(
                SignalType.BUY,
                time=candles[i].time,
            )
            sim.process_candle(candles[i], sig)
            sim.process_candle(candles[i + 1])
            sim.process_candle(candles[i + 2])

        assert len(sim.state.open_positions) == 0
        assert len(sim.state.closed_trades) == 0

    def test_fill_failure_rate_0_all_succeed(self) -> None:
        """fill_failure_rate=0.0で全約定成功"""
        config = _base_config(fill_failure_rate=0.0)
        sim = TradeSimulator(config)
        candles = _candles(4)
        sig = _make_signal(SignalType.BUY, time=candles[0].time)

        sim.process_candle(candles[0], sig)
        sim.process_candle(candles[1])

        assert len(sim.state.open_positions) == 1


class TestPartialFillRatio:
    """部分約定率のテスト"""

    def test_partial_fill_half_volume(self) -> None:
        """partial_fill_ratio=0.5でvolume半減"""
        config = _base_config(
            partial_fill_ratio=0.5,
            use_dynamic_lot=True,
        )
        sim = TradeSimulator(config)
        candles = _candles(4)
        sig = _make_signal(
            SignalType.BUY,
            lot=1.0,
            time=candles[0].time,
        )

        sim.process_candle(candles[0], sig)
        sim.process_candle(candles[1])

        assert len(sim.state.open_positions) == 1
        pos = sim.state.open_positions[0]
        assert pos.volume == pytest.approx(0.5, abs=0.01)

    def test_partial_fill_ratio_1_full_volume(self) -> None:
        """partial_fill_ratio=1.0でvolume維持"""
        config = _base_config(
            partial_fill_ratio=1.0,
            use_dynamic_lot=True,
        )
        sim = TradeSimulator(config)
        candles = _candles(4)
        sig = _make_signal(
            SignalType.BUY,
            lot=1.0,
            time=candles[0].time,
        )

        sim.process_candle(candles[0], sig)
        sim.process_candle(candles[1])

        assert len(sim.state.open_positions) == 1
        pos = sim.state.open_positions[0]
        assert pos.volume == pytest.approx(1.0, abs=0.01)


class TestSignalSkipRate:
    """シグナルスキップ率のテスト"""

    def test_signal_skip_rate_1_no_trades(self) -> None:
        """signal_skip_rate=1.0で全シグナルスキップ→トレード0件"""
        config = _base_config(signal_skip_rate=1.0)
        sim = TradeSimulator(config)
        candles = _candles(20)

        for i in range(0, 18, 3):
            sig = _make_signal(
                SignalType.BUY,
                time=candles[i].time,
            )
            sim.process_candle(candles[i], sig)
            sim.process_candle(candles[i + 1])
            sim.process_candle(candles[i + 2])

        assert len(sim.state.open_positions) == 0

    def test_signal_skip_rate_0_all_pass(self) -> None:
        """signal_skip_rate=0.0で全シグナル通過"""
        config = _base_config(signal_skip_rate=0.0)
        sim = TradeSimulator(config)
        candles = _candles(4)
        sig = _make_signal(SignalType.BUY, time=candles[0].time)

        sim.process_candle(candles[0], sig)
        sim.process_candle(candles[1])

        assert len(sim.state.open_positions) == 1


class TestEntryDelayBars:
    """エントリー遅延のテスト（bar数ベース）"""

    def test_entry_delay_3_bars(self) -> None:
        """entry_delay_bars=3でシグナルから3bar後にpending設定

        bar 0: シグナル → キューに入る (queued_bar=0)
        bar 1: シグナルなし、bar_count=1 - 0 = 1 < 3
        bar 2: シグナルなし、bar_count=2 - 0 = 2 < 3
        bar 3: シグナルなし、bar_count=3 - 0 = 3 >= 3 → pending
        bar 4: pending約定
        """
        config = _base_config(entry_delay_bars=3)
        sim = TradeSimulator(config)
        candles = _candles(8)

        # 足0: シグナル → キューに入る
        sig = _make_signal(SignalType.BUY, time=candles[0].time)
        sim.process_candle(candles[0], sig)
        assert len(sim.state.open_positions) == 0
        assert sim._pending_signal is None

        # 足1: まだ1bar経過（< 3）
        sim.process_candle(candles[1])
        assert sim._pending_signal is None

        # 足2: 2bar経過（< 3）
        sim.process_candle(candles[2])
        assert sim._pending_signal is None

        # 足3: 3bar経過（>= 3）→ シグナルがpendingに
        sim.process_candle(candles[3])
        assert sim._pending_signal is not None

        # 足4: pendingが約定
        sim.process_candle(candles[4])
        assert len(sim.state.open_positions) == 1

    def test_entry_delay_1_bar(self) -> None:
        """entry_delay_bars=1で1bar後にpending設定

        通常の次足約定に加えてさらに1bar遅延 = 合計2bar後に約定。
        """
        config = _base_config(entry_delay_bars=1)
        sim = TradeSimulator(config)
        candles = _candles(6)

        sig = _make_signal(SignalType.BUY, time=candles[0].time)
        sim.process_candle(candles[0], sig)
        # キューに入った、まだpendingではない
        assert sim._pending_signal is None

        # 足1: 1bar経過 → pending設定
        sim.process_candle(candles[1])
        assert sim._pending_signal is not None

        # 足2: pending約定
        sim.process_candle(candles[2])
        assert len(sim.state.open_positions) == 1

    def test_entry_delay_0_immediate(self) -> None:
        """entry_delay_bars=0で即座にpending（通常動作）"""
        config = _base_config(entry_delay_bars=0)
        sim = TradeSimulator(config)
        candles = _candles(4)
        sig = _make_signal(SignalType.BUY, time=candles[0].time)

        sim.process_candle(candles[0], sig)
        # pending保存済み
        assert sim._pending_signal is not None

        sim.process_candle(candles[1])
        assert len(sim.state.open_positions) == 1


class TestSlippageExtra:
    """追加スリッページのテスト"""

    def test_extra_slippage_increases_buy_price(self) -> None:
        """追加スリッページでBUYエントリー価格が上昇"""
        candle = _make_candle(
            BASE_TIME, 150.0, 150.1, 149.9, 150.05,
        )
        # ベースライン（追加スリッページなし）
        config_base = _base_config()
        sim_base = TradeSimulator(config_base)
        price_base = sim_base._get_entry_price(
            SignalType.BUY, candle, at_open=True,
        )
        # 追加スリッページあり
        config_stress = _base_config(slippage_extra_pips=2.0)
        sim_stress = TradeSimulator(config_stress)
        price_stress = sim_stress._get_entry_price(
            SignalType.BUY, candle, at_open=True,
        )
        # 2pips = 0.02円分高くなる
        expected_diff = 2.0 * 0.01  # pips * pip_unit
        assert price_stress - price_base == pytest.approx(
            expected_diff, abs=1e-6,
        )

    def test_extra_slippage_decreases_sell_price(self) -> None:
        """追加スリッページでSELLエントリー価格が低下"""
        candle = _make_candle(
            BASE_TIME, 150.0, 150.1, 149.9, 150.05,
        )
        config_base = _base_config()
        sim_base = TradeSimulator(config_base)
        price_base = sim_base._get_entry_price(
            SignalType.SELL, candle, at_open=True,
        )
        config_stress = _base_config(slippage_extra_pips=2.0)
        sim_stress = TradeSimulator(config_stress)
        price_stress = sim_stress._get_entry_price(
            SignalType.SELL, candle, at_open=True,
        )
        expected_diff = 2.0 * 0.01
        assert price_base - price_stress == pytest.approx(
            expected_diff, abs=1e-6,
        )


class TestSlippageRandom:
    """ランダムスリッページのテスト"""

    def test_random_slippage_varies_price(self) -> None:
        """ランダムスリッページでエントリー価格が毎回異なる"""
        config = _base_config(slippage_random_max_pips=2.0)
        sim = TradeSimulator(config)
        candle = _make_candle(
            BASE_TIME, 150.0, 150.1, 149.9, 150.05,
        )

        prices = []
        for _ in range(20):
            prices.append(
                sim._get_entry_price(
                    SignalType.BUY, candle, at_open=True,
                ),
            )

        # 全て同じではないこと（RNGで変動する）
        assert len(set(prices)) > 1


class TestPriceNoise:
    """価格ノイズのテスト"""

    def test_price_noise_varies_entry(self) -> None:
        """価格ノイズでエントリー価格が変動する"""
        config = _base_config(price_noise_pips=1.0)
        sim = TradeSimulator(config)
        candle = _make_candle(
            BASE_TIME, 150.0, 150.1, 149.9, 150.05,
        )

        prices = []
        for _ in range(20):
            prices.append(
                sim._get_entry_price(
                    SignalType.BUY, candle, at_open=True,
                ),
            )

        # 変動幅が±1pip(0.01)以内
        assert max(prices) - min(prices) <= 0.02 + 1e-6
        # 複数の異なる価格が存在
        assert len(set(prices)) > 1


class TestRngReproducibility:
    """RNG再現性のテスト"""

    def test_reset_reproduces_results(self) -> None:
        """reset()後に同じ結果が再現される"""
        config = _base_config(
            slippage_random_max_pips=1.0,
            price_noise_pips=0.5,
        )
        sim = TradeSimulator(config)
        candle = _make_candle(
            BASE_TIME, 150.0, 150.1, 149.9, 150.05,
        )

        # 1回目
        prices_1 = [
            sim._get_entry_price(
                SignalType.BUY, candle, at_open=True,
            )
            for _ in range(10)
        ]

        # リセット
        sim.reset()

        # 2回目（同じシーケンスになるはず）
        prices_2 = [
            sim._get_entry_price(
                SignalType.BUY, candle, at_open=True,
            )
            for _ in range(10)
        ]

        assert prices_1 == prices_2


class TestCombinedStress:
    """複合ストレステスト"""

    def test_all_stress_params_together(self) -> None:
        """全ストレスパラメータを同時に有効化しても動作する"""
        config = _base_config(
            slippage_extra_pips=1.0,
            slippage_random_max_pips=0.5,
            fill_failure_rate=0.1,
            partial_fill_ratio=0.8,
            entry_delay_bars=1,
            price_noise_pips=0.5,
            signal_skip_rate=0.1,
            use_dynamic_lot=True,
        )
        sim = TradeSimulator(config)
        candles = _candles(30)

        # エラーなく30本処理できること
        for i, candle in enumerate(candles):
            sig = None
            if i % 5 == 0:
                sig = _make_signal(
                    SignalType.BUY,
                    lot=0.5,
                    time=candle.time,
                )
            sim.process_candle(candle, sig)

        # 一部は約定失敗やスキップがあるため、
        # 0〜6件の間でポジション/トレードが存在
        total = (
            len(sim.state.open_positions)
            + len(sim.state.closed_trades)
        )
        assert total >= 0  # エラーなく完了
