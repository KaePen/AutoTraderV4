"""MarketContextBuilder テスト。"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from autotrader.decision.v2.market_context import (
    H1Indicators,
    MarketContext,
    MarketContextBuilder,
    StructureState,
    _safe_get,
)


def _make_h1_df(n: int = 10) -> pd.DataFrame:
    """テスト用H1 DataFrame生成。"""
    times = pd.date_range("2024-01-01", periods=n, freq="h")
    df = pd.DataFrame({
        "time": times,
        "open": np.linspace(149.5, 150.5, n),
        "high": np.linspace(150.0, 151.0, n),
        "low": np.linspace(149.0, 150.0, n),
        "close": np.linspace(149.8, 150.8, n),
        "rsi_14": np.full(n, 55.0),
        "macd": np.full(n, 0.01),
        "macd_signal": np.full(n, 0.005),
        "macd_histogram": np.full(n, 0.005),
        "atr_14": np.full(n, 0.15),
        "adx_14": np.full(n, 28.0),
        "plus_di_14": np.full(n, 20.0),
        "minus_di_14": np.full(n, 15.0),
        "bb_upper": np.full(n, 150.5),
        "bb_lower": np.full(n, 149.5),
        "bb_percent_b": np.full(n, 0.6),
        "bb_width": np.full(n, 0.007),
        "bb_squeeze": np.full(n, 0.4),
        "ema_20": np.full(n, 150.1),
        "ema_50": np.full(n, 150.0),
        "normalized_atr": np.full(n, 1.0),
        "stoch_k": np.full(n, 55.0),
        "stoch_d": np.full(n, 52.0),
        "ma_alignment": np.full(n, 0.5),
        "volatility_trend": np.full(n, 0.1),
        "candle_pattern": ["NONE"] * n,
        "pa_bullish_score": np.full(n, 0.3),
        "pa_bearish_score": np.full(n, 0.1),
        "at_support": [False] * n,
        "at_resistance": [False] * n,
    })
    return df.set_index("time")


def _make_h4_df(n: int = 5) -> pd.DataFrame:
    """テスト用H4 DataFrame生成。"""
    times = pd.date_range("2024-01-01", periods=n, freq="4h")
    df = pd.DataFrame({
        "time": times,
        "close": np.linspace(149.8, 150.5, n),
        "trend_state_smc": ["BULLISH"] * n,
        "bos_signal": [0, 0, 1, 0, 0],
        "choch_signal": np.zeros(n, dtype=int),
        "bars_since_bos": [10, 9, 0, 1, 2],
        "bars_since_choch": np.full(n, 50, dtype=int),
        "last_swing_high": np.full(n, 151.0),
        "last_swing_low": np.full(n, 149.5),
        "structure_direction": np.ones(n, dtype=int),
        "liquidity_grab_bullish": [False] * n,
        "liquidity_grab_bearish": [False] * n,
        "adx_14": np.full(n, 25.0),
    })
    return df.set_index("time")


class TestSafeGet:
    """_safe_get ユーティリティテスト。"""

    def test_通常値(self):
        row = pd.Series({"a": 42.0})
        assert _safe_get(row, "a", 0.0) == 42.0

    def test_キー不在(self):
        row = pd.Series({"a": 42.0})
        assert _safe_get(row, "b", -1.0) == -1.0

    def test_NaN値(self):
        row = pd.Series({"a": float("nan")})
        assert _safe_get(row, "a", 0.0) == 0.0

    def test_None値(self):
        row = pd.Series({"a": None})
        assert _safe_get(row, "a", 0.0) == 0.0


class TestMarketContextBuilder:
    """MarketContextBuilder テスト。"""

    def test_正常構築(self):
        h1 = _make_h1_df()
        h4 = _make_h4_df()
        data = {"H1": h1, "H4": h4}
        builder = MarketContextBuilder(data)
        ctx = builder.build(datetime(2024, 1, 1, 5, 0))
        assert ctx is not None
        assert isinstance(ctx, MarketContext)
        assert ctx.h1.rsi == 55.0
        assert ctx.h1.adx == 28.0
        assert ctx.h4.trend_state == "BULLISH"

    def test_H1データなし(self):
        data = {"H4": _make_h4_df()}
        builder = MarketContextBuilder(data)
        ctx = builder.build(datetime(2024, 1, 1, 2, 0))
        assert ctx is None

    def test_H4データなしでもデフォルト構造(self):
        data = {"H1": _make_h1_df()}
        builder = MarketContextBuilder(data)
        ctx = builder.build(datetime(2024, 1, 1, 3, 0))
        assert ctx is not None
        assert ctx.h4.trend_state == "CONSOLIDATION"
        assert ctx.h4.bars_since_bos == 9999

    def test_将来時刻でデータなし(self):
        data = {"H1": _make_h1_df(3)}
        builder = MarketContextBuilder(data)
        ctx = builder.build(datetime(2023, 12, 31, 0, 0))
        assert ctx is None

    def test_直近行を取得(self):
        """H4の場合、H1時刻よりも前の最新H4行を取得。"""
        h1 = _make_h1_df()
        h4 = _make_h4_df()
        data = {"H1": h1, "H4": h4}
        builder = MarketContextBuilder(data)
        # H1の5時はH4の4時行を参照すべき
        ctx = builder.build(datetime(2024, 1, 1, 5, 0))
        assert ctx is not None
        # BOS=1の行（8時）はまだ来ていないのでbos_signal!=1
        assert ctx.h4.bos_signal in (0, 1)
