"""BreakoutStrategy テスト。"""

from __future__ import annotations

import pytest

from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import BreakoutConfig
from autotrader.decision.v2.market_context import (
    H1Indicators,
    StructureState,
)
from autotrader.decision.v2.strategies.breakout import (
    BreakoutStrategy,
)
from tests.unit.decision.v2.conftest import make_context


def _squeeze_release_h1(adx: float = 25.0) -> H1Indicators:
    """スクイーズ解消後のH1指標。"""
    return H1Indicators(
        rsi=55.0, macd=0.02, macd_signal=0.01,
        macd_histogram=0.01, atr=0.20, adx=adx,
        plus_di=25.0, minus_di=15.0,
        bb_upper=150.80, bb_lower=149.20,
        bb_percent_b=0.75,
        bb_width=0.011,  # 幅が拡大
        bb_squeeze=0.3,  # スクイーズ解消
        ema_20=150.2, ema_50=150.0,
        normalized_atr=1.2, stoch_k=60.0, stoch_d=55.0,
    )


def _bos_h4(direction: int = 1) -> StructureState:
    """BOS確認済みH4構造。"""
    state = "BULLISH" if direction > 0 else "BEARISH"
    return StructureState(
        trend_state=state,
        bos_signal=direction,
        choch_signal=0,
        bars_since_bos=2,
        bars_since_choch=20,
        last_swing_high=151.50,
        last_swing_low=148.50,
        structure_direction=direction,
        liquidity_grab_bullish=False,
        liquidity_grab_bearish=False,
        adx=25.0,
    )


class TestBreakoutEntry:
    """エントリー条件テスト。"""

    def test_BUYブレイクアウト(self):
        """QUIET十分 + スクイーズ解消 + BOS + ADXでBUY。"""
        strategy = BreakoutStrategy(quiet_bars_counter=7)
        h1 = _squeeze_release_h1(adx=25.0)
        h4 = _bos_h4(direction=1)
        ctx = make_context(
            h1=h1, h4=h4,
            price=150.60,
        )
        signal = strategy.evaluate(ctx)
        assert signal is not None
        assert signal.direction == SignalType.BUY
        assert signal.strategy_name == "Breakout"

    def test_SELLブレイクアウト(self):
        """弱気BOS + スクイーズ解消でSELL。"""
        strategy = BreakoutStrategy(quiet_bars_counter=7)
        h1 = _squeeze_release_h1(adx=25.0)
        h4 = _bos_h4(direction=-1)
        ctx = make_context(
            h1=h1, h4=h4,
            price=149.40,
        )
        signal = strategy.evaluate(ctx)
        assert signal is not None
        assert signal.direction == SignalType.SELL


class TestBreakoutReject:
    """リジェクト条件テスト。"""

    def test_QUIET不足で拒否(self):
        """QUIET足数が不足で拒否。"""
        strategy = BreakoutStrategy(quiet_bars_counter=2)
        h1 = _squeeze_release_h1()
        h4 = _bos_h4()
        ctx = make_context(h1=h1, h4=h4, price=150.6)
        assert strategy.evaluate(ctx) is None

    def test_スクイーズ未解消で拒否(self):
        """BBスクイーズが高い（未解消）で拒否。"""
        strategy = BreakoutStrategy(quiet_bars_counter=7)
        h1 = H1Indicators(
            rsi=55, macd=0.02, macd_signal=0.01,
            macd_histogram=0.01, atr=0.20, adx=25.0,
            plus_di=25, minus_di=15,
            bb_upper=150.8, bb_lower=149.2,
            bb_percent_b=0.75, bb_width=0.011,
            bb_squeeze=0.85,  # まだスクイーズ中
            ema_20=150.2, ema_50=150.0,
            normalized_atr=1.2, stoch_k=60, stoch_d=55,
        )
        h4 = _bos_h4()
        ctx = make_context(h1=h1, h4=h4, price=150.6)
        assert strategy.evaluate(ctx) is None

    def test_ADX不足で拒否(self):
        """ADX < 閾値で拒否。"""
        strategy = BreakoutStrategy(quiet_bars_counter=7)
        h1 = _squeeze_release_h1(adx=15.0)  # ADX低い
        h4 = _bos_h4()
        ctx = make_context(h1=h1, h4=h4, price=150.6)
        assert strategy.evaluate(ctx) is None


class TestQuietBarsCounter:
    """QUIET足カウンタテスト。"""

    def test_カウンタ増加(self):
        strategy = BreakoutStrategy()
        assert strategy._quiet_bars == 0
        strategy.update_quiet_bars(True)
        assert strategy._quiet_bars == 1
        strategy.update_quiet_bars(True)
        assert strategy._quiet_bars == 2

    def test_カウンタリセット(self):
        strategy = BreakoutStrategy(quiet_bars_counter=5)
        strategy.update_quiet_bars(False)
        assert strategy._quiet_bars == 0
