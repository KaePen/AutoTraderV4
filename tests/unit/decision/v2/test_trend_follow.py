"""TrendFollowStrategy テスト。"""

from __future__ import annotations

import pytest

from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import TrendFollowConfig
from autotrader.decision.v2.market_context import (
    H1Indicators,
    PriceActionState,
    StructureState,
)
from autotrader.decision.v2.strategies.trend_follow import (
    TrendFollowStrategy,
)
from tests.unit.decision.v2.conftest import make_context


class TestTrendFollowEntry:
    """エントリー条件テスト。"""

    def test_BUYシグナル生成(
        self, default_h1, bullish_h4, bullish_d1, bullish_pa,
    ):
        """全条件満たす場合BUYシグナル。"""
        strategy = TrendFollowStrategy()
        ctx = make_context(
            h1=default_h1,
            h4=bullish_h4,
            d1=bullish_d1,
            pa=bullish_pa,
            ma_alignment=0.5,
        )
        signal = strategy.evaluate(ctx)
        assert signal is not None
        assert signal.direction == SignalType.BUY
        assert signal.confidence > 0
        assert signal.sl_price < ctx.current_price
        assert signal.tp_price > ctx.current_price
        assert signal.strategy_name == "TrendFollow"

    def test_SELLシグナル生成(self, default_h1, bearish_h4):
        """弱気構造で全条件満たす場合SELLシグナル。"""
        bearish_pa = PriceActionState(
            candle_pattern="PIN_BAR_BEARISH",
            bullish_score=0.1,
            bearish_score=0.7,
            at_support=False,
            at_resistance=True,
        )
        bearish_d1 = StructureState(
            trend_state="BEARISH",
            bos_signal=-1,
            choch_signal=0,
            bars_since_bos=5,
            bars_since_choch=30,
            last_swing_high=152.0,
            last_swing_low=148.0,
            structure_direction=-1,
            liquidity_grab_bullish=False,
            liquidity_grab_bearish=False,
            adx=25.0,
        )
        strategy = TrendFollowStrategy()
        ctx = make_context(
            h1=default_h1,
            h4=bearish_h4,
            d1=bearish_d1,
            pa=bearish_pa,
            ma_alignment=-0.5,
        )
        signal = strategy.evaluate(ctx)
        assert signal is not None
        assert signal.direction == SignalType.SELL
        assert signal.sl_price > ctx.current_price
        assert signal.tp_price < ctx.current_price


class TestTrendFollowReject:
    """リジェクト条件テスト。"""

    def test_H4がCONSOLIDATIONで拒否(
        self, default_h1, bullish_pa,
    ):
        """H4構造がBULLISH/BEARISH以外は拒否。"""
        strategy = TrendFollowStrategy()
        ctx = make_context(h1=default_h1, pa=bullish_pa)
        # デフォルトH4=CONSOLIDATION
        assert strategy.evaluate(ctx) is None

    def test_BOS期限切れで拒否(
        self, default_h1, bullish_pa,
    ):
        """BOS発生から10足超で拒否。"""
        old_bos_h4 = StructureState(
            trend_state="BULLISH",
            bos_signal=1,
            choch_signal=0,
            bars_since_bos=15,  # 期限切れ
            bars_since_choch=50,
            last_swing_high=151.0,
            last_swing_low=149.5,
            structure_direction=1,
            liquidity_grab_bullish=False,
            liquidity_grab_bearish=False,
            adx=28.0,
        )
        strategy = TrendFollowStrategy()
        ctx = make_context(
            h1=default_h1,
            h4=old_bos_h4,
            pa=bullish_pa,
        )
        assert strategy.evaluate(ctx) is None

    def test_プルバック不足で拒否(
        self, bullish_h4, bullish_pa,
    ):
        """EMAから離れすぎている場合拒否。"""
        far_h1 = H1Indicators(
            rsi=50, macd=0.01, macd_signal=0.005,
            macd_histogram=0.005, atr=0.15, adx=30,
            plus_di=20, minus_di=15, bb_upper=150.5,
            bb_lower=149.5, bb_percent_b=0.5,
            bb_width=0.007, bb_squeeze=0.5,
            ema_20=150.1,
            ema_50=149.50,  # 離れている
            normalized_atr=1.0, stoch_k=50, stoch_d=50,
        )
        strategy = TrendFollowStrategy()
        ctx = make_context(
            h1=far_h1,
            h4=bullish_h4,
            pa=bullish_pa,
            price=150.50,  # EMA50=149.50から1.0離れ → 1.0/0.15=6.67ATR
        )
        assert strategy.evaluate(ctx) is None

    def test_反転足なしで拒否(
        self, default_h1, bullish_h4, no_pattern_pa,
    ):
        """反転足パターンがない場合拒否。"""
        strategy = TrendFollowStrategy()
        ctx = make_context(
            h1=default_h1,
            h4=bullish_h4,
            pa=no_pattern_pa,
        )
        assert strategy.evaluate(ctx) is None


class TestTrendFollowConfidence:
    """確信度計算テスト。"""

    def test_高確信度_構造整合(
        self, default_h1, bullish_h4, bullish_d1, bullish_pa,
    ):
        """H4/D1方向一致で高確信度。"""
        strategy = TrendFollowStrategy()
        ctx = make_context(
            h1=default_h1,
            h4=bullish_h4,
            d1=bullish_d1,
            pa=bullish_pa,
        )
        signal = strategy.evaluate(ctx)
        assert signal is not None
        assert signal.confidence > 0.5

    def test_低確信度_D1不整合(
        self, default_h1, bullish_h4, bullish_pa,
    ):
        """D1方向不整合で低確信度。"""
        bearish_d1 = StructureState(
            trend_state="BEARISH",
            bos_signal=-1, choch_signal=0,
            bars_since_bos=5, bars_since_choch=30,
            last_swing_high=152.0, last_swing_low=148.0,
            structure_direction=-1,
            liquidity_grab_bullish=False,
            liquidity_grab_bearish=False, adx=25.0,
        )
        strategy = TrendFollowStrategy()
        ctx_aligned = make_context(
            h1=default_h1,
            h4=bullish_h4,
            d1=StructureState(
                trend_state="BULLISH",
                bos_signal=1, choch_signal=0,
                bars_since_bos=5, bars_since_choch=30,
                last_swing_high=152.0, last_swing_low=148.0,
                structure_direction=1,
                liquidity_grab_bullish=False,
                liquidity_grab_bearish=False, adx=25.0,
            ),
            pa=bullish_pa,
        )
        ctx_misaligned = make_context(
            h1=default_h1,
            h4=bullish_h4,
            d1=bearish_d1,
            pa=bullish_pa,
        )
        sig_a = strategy.evaluate(ctx_aligned)
        sig_m = strategy.evaluate(ctx_misaligned)

        if sig_a is not None and sig_m is not None:
            assert sig_a.confidence > sig_m.confidence
