"""RangeRevertStrategy テスト。"""

from __future__ import annotations

import pytest

from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import RangeRevertConfig
from autotrader.decision.v2.market_context import (
    H1Indicators,
    PriceActionState,
    StructureState,
)
from autotrader.decision.v2.strategies.range_revert import (
    RangeRevertStrategy,
)
from tests.unit.decision.v2.conftest import make_context


def _oversold_h1() -> H1Indicators:
    """売られすぎH1指標。"""
    return H1Indicators(
        rsi=25.0, macd=-0.02, macd_signal=-0.01,
        macd_histogram=-0.01, atr=0.15, adx=18.0,
        plus_di=12.0, minus_di=22.0,
        bb_upper=150.50, bb_lower=149.50,
        bb_percent_b=0.05,  # 極値域
        bb_width=0.007, bb_squeeze=0.4,
        ema_20=150.0, ema_50=150.1,
        normalized_atr=1.0, stoch_k=15.0, stoch_d=18.0,
    )


def _overbought_h1() -> H1Indicators:
    """買われすぎH1指標。"""
    return H1Indicators(
        rsi=75.0, macd=0.02, macd_signal=0.01,
        macd_histogram=0.01, atr=0.15, adx=18.0,
        plus_di=22.0, minus_di=12.0,
        bb_upper=150.50, bb_lower=149.50,
        bb_percent_b=0.95,  # 極値域
        bb_width=0.007, bb_squeeze=0.4,
        ema_20=150.0, ema_50=149.9,
        normalized_atr=1.0, stoch_k=85.0, stoch_d=82.0,
    )


class TestRangeRevertEntry:
    """エントリー条件テスト。"""

    def test_BUYシグナル_BB極値_サポート到達(self):
        """BB%B低値+サポート到達でBUY。"""
        h1 = _oversold_h1()
        pa = PriceActionState(
            candle_pattern="HAMMER",
            bullish_score=0.6,
            bearish_score=0.1,
            at_support=True,
            at_resistance=False,
        )
        strategy = RangeRevertStrategy()
        ctx = make_context(
            h1=h1, pa=pa,
            price=149.55,
            ma_alignment=0.0,
        )
        signal = strategy.evaluate(ctx)
        assert signal is not None
        assert signal.direction == SignalType.BUY
        assert signal.sl_price < ctx.current_price
        assert signal.tp_price > ctx.current_price

    def test_SELLシグナル_BB極値_レジスタンス到達(self):
        """BB%B高値+レジスタンス到達でSELL。"""
        h1 = _overbought_h1()
        pa = PriceActionState(
            candle_pattern="SHOOTING_STAR",
            bullish_score=0.1,
            bearish_score=0.6,
            at_support=False,
            at_resistance=True,
        )
        strategy = RangeRevertStrategy()
        ctx = make_context(
            h1=h1, pa=pa,
            price=150.45,
            ma_alignment=0.0,
        )
        signal = strategy.evaluate(ctx)
        assert signal is not None
        assert signal.direction == SignalType.SELL

    def test_流動性グラブでも有効(self):
        """流動性グラブ+BB極値でエントリー。"""
        h1 = _oversold_h1()
        # サポート到達なしだが流動性グラブあり
        pa = PriceActionState(
            candle_pattern="NONE",
            bullish_score=0.3,
            bearish_score=0.0,
            at_support=False,
            at_resistance=False,
        )
        h4 = StructureState(
            trend_state="CONSOLIDATION",
            bos_signal=0, choch_signal=0,
            bars_since_bos=9999, bars_since_choch=9999,
            last_swing_high=151.0, last_swing_low=149.0,
            structure_direction=0,
            liquidity_grab_bullish=True,  # 流動性グラブ
            liquidity_grab_bearish=False,
            adx=15.0,
        )
        strategy = RangeRevertStrategy()
        ctx = make_context(
            h1=h1, h4=h4, pa=pa,
            price=149.55,
        )
        signal = strategy.evaluate(ctx)
        assert signal is not None
        assert signal.direction == SignalType.BUY


class TestRangeRevertReject:
    """リジェクト条件テスト。"""

    def test_BB中間域で拒否(self, default_h1, bullish_pa):
        """BB%Bが中間域では拒否。"""
        strategy = RangeRevertStrategy()
        ctx = make_context(h1=default_h1, pa=bullish_pa)
        assert strategy.evaluate(ctx) is None

    def test_サポレジ到達なしで拒否(self):
        """BB極値だがサポレジ到達なしで拒否。"""
        h1 = _oversold_h1()
        pa = PriceActionState(
            candle_pattern="HAMMER",
            bullish_score=0.5,
            bearish_score=0.0,
            at_support=False,
            at_resistance=False,
        )
        strategy = RangeRevertStrategy()
        ctx = make_context(h1=h1, pa=pa, price=149.55)
        # 流動性グラブもなし → 拒否
        assert strategy.evaluate(ctx) is None
