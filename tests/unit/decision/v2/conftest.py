"""V2テスト共通フィクスチャ。"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.decision.v2.config import V2BotConfig
from autotrader.decision.v2.market_context import (
    H1Indicators,
    MarketContext,
    PriceActionState,
    StructureState,
)


@pytest.fixture()
def default_h1() -> H1Indicators:
    """標準的なH1指標。"""
    return H1Indicators(
        rsi=50.0,
        macd=0.01,
        macd_signal=0.005,
        macd_histogram=0.005,
        atr=0.15,
        adx=30.0,
        plus_di=20.0,
        minus_di=15.0,
        bb_upper=150.50,
        bb_lower=149.50,
        bb_percent_b=0.50,
        bb_width=0.0067,
        bb_squeeze=0.5,
        ema_20=150.10,
        ema_50=150.00,
        normalized_atr=1.0,
        stoch_k=50.0,
        stoch_d=50.0,
    )


@pytest.fixture()
def bullish_h4() -> StructureState:
    """強気H4構造。"""
    return StructureState(
        trend_state="BULLISH",
        bos_signal=1,
        choch_signal=0,
        bars_since_bos=3,
        bars_since_choch=50,
        last_swing_high=151.00,
        last_swing_low=149.70,
        structure_direction=1,
        liquidity_grab_bullish=False,
        liquidity_grab_bearish=False,
        adx=28.0,
    )


@pytest.fixture()
def bearish_h4() -> StructureState:
    """弱気H4構造。"""
    return StructureState(
        trend_state="BEARISH",
        bos_signal=-1,
        choch_signal=0,
        bars_since_bos=5,
        bars_since_choch=40,
        last_swing_high=150.30,
        last_swing_low=149.50,
        structure_direction=-1,
        liquidity_grab_bullish=False,
        liquidity_grab_bearish=False,
        adx=25.0,
    )


@pytest.fixture()
def neutral_d1() -> StructureState:
    """中立D1構造。"""
    return StructureState(
        trend_state="CONSOLIDATION",
        bos_signal=0,
        choch_signal=0,
        bars_since_bos=20,
        bars_since_choch=30,
        last_swing_high=152.00,
        last_swing_low=148.00,
        structure_direction=0,
        liquidity_grab_bullish=False,
        liquidity_grab_bearish=False,
        adx=18.0,
    )


@pytest.fixture()
def bullish_d1() -> StructureState:
    """強気D1構造。"""
    return StructureState(
        trend_state="BULLISH",
        bos_signal=1,
        choch_signal=0,
        bars_since_bos=5,
        bars_since_choch=30,
        last_swing_high=152.00,
        last_swing_low=148.00,
        structure_direction=1,
        liquidity_grab_bullish=False,
        liquidity_grab_bearish=False,
        adx=25.0,
    )


@pytest.fixture()
def bullish_pa() -> PriceActionState:
    """強気プライスアクション。"""
    return PriceActionState(
        candle_pattern="PIN_BAR_BULLISH",
        bullish_score=0.7,
        bearish_score=0.1,
        at_support=True,
        at_resistance=False,
    )


@pytest.fixture()
def no_pattern_pa() -> PriceActionState:
    """パターンなし。"""
    return PriceActionState(
        candle_pattern="NONE",
        bullish_score=0.0,
        bearish_score=0.0,
        at_support=False,
        at_resistance=False,
    )


def make_context(
    h1: H1Indicators | None = None,
    h4: StructureState | None = None,
    d1: StructureState | None = None,
    pa: PriceActionState | None = None,
    price: float = 150.00,
    ma_alignment: float = 0.5,
    spread_pips: float = 1.5,
) -> MarketContext:
    """テスト用MarketContextを構築。"""
    _h1 = h1 or H1Indicators(
        rsi=50, macd=0.01, macd_signal=0.005,
        macd_histogram=0.005, atr=0.15, adx=30,
        plus_di=20, minus_di=15, bb_upper=150.5,
        bb_lower=149.5, bb_percent_b=0.5, bb_width=0.007,
        bb_squeeze=0.5, ema_20=150.1, ema_50=150.0,
        normalized_atr=1.0, stoch_k=50, stoch_d=50,
    )
    _h4 = h4 or StructureState(
        trend_state="CONSOLIDATION", bos_signal=0,
        choch_signal=0, bars_since_bos=9999,
        bars_since_choch=9999, last_swing_high=151.0,
        last_swing_low=149.0, structure_direction=0,
        liquidity_grab_bullish=False,
        liquidity_grab_bearish=False, adx=20.0,
    )
    _d1 = d1 or StructureState(
        trend_state="CONSOLIDATION", bos_signal=0,
        choch_signal=0, bars_since_bos=9999,
        bars_since_choch=9999, last_swing_high=152.0,
        last_swing_low=148.0, structure_direction=0,
        liquidity_grab_bullish=False,
        liquidity_grab_bearish=False, adx=18.0,
    )
    _pa = pa or PriceActionState(
        candle_pattern="NONE", bullish_score=0.0,
        bearish_score=0.0, at_support=False,
        at_resistance=False,
    )
    return MarketContext(
        current_price=price,
        current_time=datetime(2024, 6, 15, 12, 0),
        h1=_h1,
        h4=_h4,
        d1=_d1,
        price_action=_pa,
        ma_alignment=ma_alignment,
        volatility_trend=0.0,
        spread_pips=spread_pips,
    )
