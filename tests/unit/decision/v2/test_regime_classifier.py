"""RegimeClassifier テスト。"""

from __future__ import annotations

import pytest

from autotrader.decision.v2.config import RegimeClassifierConfig
from autotrader.decision.v2.market_context import H1Indicators
from autotrader.decision.v2.regime_classifier import (
    MarketRegimeV2,
    RegimeClassifier,
)
from tests.unit.decision.v2.conftest import make_context


def _h1_with(
    adx: float = 30.0,
    normalized_atr: float = 1.0,
    **kwargs: float,
) -> H1Indicators:
    """指定パラメータでH1指標を生成。"""
    defaults = dict(
        rsi=50, macd=0.01, macd_signal=0.005,
        macd_histogram=0.005, atr=0.15, adx=adx,
        plus_di=20, minus_di=15, bb_upper=150.5,
        bb_lower=149.5, bb_percent_b=0.5,
        bb_width=0.007, bb_squeeze=0.5, ema_20=150.1,
        ema_50=150.0, normalized_atr=normalized_atr,
        stoch_k=50, stoch_d=50,
    )
    defaults.update(kwargs)
    return H1Indicators(**defaults)


class TestRegimeDetection:
    """候補レジーム検出テスト。"""

    def test_VOLATILE検出(self):
        clf = RegimeClassifier(
            RegimeClassifierConfig(
                quiet_to_volatile_bars=1,
            ),
        )
        h1 = _h1_with(normalized_atr=2.0)
        ctx = make_context(h1=h1, ma_alignment=0.0)
        result = clf.classify(ctx)
        assert result == MarketRegimeV2.VOLATILE

    def test_TRENDING検出(self):
        cfg = RegimeClassifierConfig(
            quiet_to_trending_bars=1,
        )
        clf = RegimeClassifier(cfg)
        h1 = _h1_with(adx=30.0, normalized_atr=1.0)
        ctx = make_context(h1=h1, ma_alignment=0.5)
        # 1回で遷移（1足確認）
        result = clf.classify(ctx)
        assert result == MarketRegimeV2.TRENDING

    def test_RANGING検出(self):
        cfg = RegimeClassifierConfig(
            quiet_to_ranging_bars=1,
        )
        clf = RegimeClassifier(cfg)
        h1 = _h1_with(adx=15.0, normalized_atr=1.0)
        ctx = make_context(h1=h1, ma_alignment=0.1)
        result = clf.classify(ctx)
        assert result == MarketRegimeV2.RANGING

    def test_QUIET_デフォルト(self):
        clf = RegimeClassifier()
        h1 = _h1_with(adx=15.0, normalized_atr=0.5)
        ctx = make_context(h1=h1, ma_alignment=0.1)
        result = clf.classify(ctx)
        # 初期状態QUIET、候補もQUIET → QUIET維持
        assert result == MarketRegimeV2.QUIET


class TestHysteresis:
    """ヒステリシス（遷移確認足数）テスト。"""

    def test_遷移に複数足必要(self):
        cfg = RegimeClassifierConfig(
            quiet_to_trending_bars=3,
        )
        clf = RegimeClassifier(cfg)
        h1 = _h1_with(adx=30.0)
        ctx = make_context(h1=h1, ma_alignment=0.5)

        # 1足目: まだQUIET
        assert clf.classify(ctx) == MarketRegimeV2.QUIET
        # 2足目: まだQUIET
        assert clf.classify(ctx) == MarketRegimeV2.QUIET
        # 3足目: TRENDING遷移
        assert clf.classify(ctx) == MarketRegimeV2.TRENDING

    def test_VOLATILE即時遷移(self):
        """VOLATILE は1足で遷移（デフォルト）。"""
        clf = RegimeClassifier()
        h1 = _h1_with(adx=30.0, normalized_atr=2.0)
        ctx = make_context(h1=h1, ma_alignment=0.5)
        result = clf.classify(ctx)
        assert result == MarketRegimeV2.VOLATILE

    def test_VOLATILE復帰は慎重(self):
        """VOLATILE → QUIET は3足必要。"""
        cfg = RegimeClassifierConfig(
            quiet_to_volatile_bars=1,
            volatile_to_quiet_bars=3,
        )
        clf = RegimeClassifier(cfg)

        # まずVOLATILEにする
        volatile_h1 = _h1_with(normalized_atr=2.0)
        ctx_v = make_context(
            h1=volatile_h1, ma_alignment=0.0,
        )
        clf.classify(ctx_v)
        assert clf.current_regime == MarketRegimeV2.VOLATILE

        # QUIET条件に戻す
        quiet_h1 = _h1_with(
            adx=15.0, normalized_atr=0.5,
        )
        ctx_q = make_context(
            h1=quiet_h1, ma_alignment=0.1,
        )

        # 1足目: まだVOLATILE
        assert clf.classify(ctx_q) == MarketRegimeV2.VOLATILE
        # 2足目: まだVOLATILE
        assert clf.classify(ctx_q) == MarketRegimeV2.VOLATILE
        # 3足目: QUIET遷移
        assert clf.classify(ctx_q) == MarketRegimeV2.QUIET

    def test_カウンタリセット(self):
        """異なる候補が来たらカウンタがリセット。"""
        cfg = RegimeClassifierConfig(
            quiet_to_trending_bars=3,
        )
        clf = RegimeClassifier(cfg)

        trending_h1 = _h1_with(adx=30.0)
        ctx_t = make_context(
            h1=trending_h1, ma_alignment=0.5,
        )
        # 2足TRENDING候補
        clf.classify(ctx_t)
        clf.classify(ctx_t)

        # 一旦QUIET条件に戻る
        quiet_h1 = _h1_with(
            adx=15.0, normalized_atr=0.5,
        )
        ctx_q = make_context(
            h1=quiet_h1, ma_alignment=0.1,
        )
        clf.classify(ctx_q)

        # TRENDING候補再開、1足目からカウント
        clf.classify(ctx_t)
        assert clf.current_regime == MarketRegimeV2.QUIET
        clf.classify(ctx_t)
        assert clf.current_regime == MarketRegimeV2.QUIET
        # 3足目で遷移
        clf.classify(ctx_t)
        assert clf.current_regime == MarketRegimeV2.TRENDING


class TestReset:
    """reset() テスト。"""

    def test_リセットでQUIETに戻る(self):
        cfg = RegimeClassifierConfig(
            quiet_to_trending_bars=1,
        )
        clf = RegimeClassifier(cfg)
        h1 = _h1_with(adx=30.0)
        ctx = make_context(h1=h1, ma_alignment=0.5)
        clf.classify(ctx)
        assert clf.current_regime == MarketRegimeV2.TRENDING

        clf.reset()
        assert clf.current_regime == MarketRegimeV2.QUIET
