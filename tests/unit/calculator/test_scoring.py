"""calculator/scoring.py のユニットテスト"""

from __future__ import annotations

import pytest

from autotrader.calculator.scoring import (
    normalize_atr_by_price,
    score_rsi_continuous,
    score_rsi_discrete,
)


class TestScoreRsiDiscrete:
    """score_rsi_discrete のテスト"""

    def test_extreme_oversold(self) -> None:
        """RSI < oversold - 10 で +3.0"""
        score, reason = score_rsi_discrete(15.0)
        assert score == 3.0
        assert "RSI極低" in reason

    def test_oversold(self) -> None:
        """oversold - 10 <= RSI < oversold - 5 で +2.0"""
        score, reason = score_rsi_discrete(22.0)
        assert score == 2.0
        assert "RSI低" in reason

    def test_near_oversold(self) -> None:
        """oversold - 5 <= RSI < oversold で +1.0"""
        score, reason = score_rsi_discrete(27.0)
        assert score == 1.0
        assert reason == ""

    def test_neutral(self) -> None:
        """中立帯でスコア0"""
        score, reason = score_rsi_discrete(50.0)
        assert score == 0.0
        assert reason == ""

    def test_near_overbought(self) -> None:
        """overbought < RSI <= overbought + 5 で -1.0"""
        score, reason = score_rsi_discrete(73.0)
        assert score == -1.0
        assert reason == ""

    def test_overbought(self) -> None:
        """overbought + 5 < RSI <= overbought + 10 で -2.0"""
        score, reason = score_rsi_discrete(78.0)
        assert score == -2.0
        assert "RSI高" in reason

    def test_extreme_overbought(self) -> None:
        """RSI > overbought + 10 で -3.0"""
        score, reason = score_rsi_discrete(85.0)
        assert score == -3.0
        assert "RSI極高" in reason

    def test_custom_thresholds(self) -> None:
        """カスタム閾値でのスコアリング"""
        # oversold=20 で RSI=5 → 極端な売られすぎ
        score, _ = score_rsi_discrete(5.0, oversold=20.0, overbought=80.0)
        assert score == 3.0

    def test_boundary_oversold(self) -> None:
        """閾値ちょうどの値"""
        score, _ = score_rsi_discrete(30.0)
        # 30 < oversold(30) は False なので中立
        assert score == 0.0


class TestScoreRsiContinuous:
    """score_rsi_continuous のテスト"""

    def test_oversold_returns_positive(self) -> None:
        """売られすぎ域で正の値"""
        score = score_rsi_continuous(20.0)
        assert score > 0

    def test_overbought_returns_negative(self) -> None:
        """買われすぎ域で負の値"""
        score = score_rsi_continuous(80.0)
        assert score < 0

    def test_neutral_low(self) -> None:
        """中立帯低め（40）で小さな正の値"""
        score = score_rsi_continuous(40.0)
        assert 0 < score < 0.5

    def test_neutral_high(self) -> None:
        """中立帯高め（60）で小さな負の値"""
        score = score_rsi_continuous(60.0)
        assert -0.5 < score < 0

    def test_rsi_50_returns_zero(self) -> None:
        """RSI=50 でゼロ"""
        score = score_rsi_continuous(50.0)
        assert score == 0.0

    def test_extreme_oversold(self) -> None:
        """RSI=0 で最大買いシグナル（1.0）"""
        score = score_rsi_continuous(0.0)
        assert score == pytest.approx(1.0)

    def test_extreme_overbought(self) -> None:
        """RSI=100 で最大売りシグナル（-1.0）"""
        score = score_rsi_continuous(100.0)
        assert score == pytest.approx(-1.0)

    def test_custom_thresholds(self) -> None:
        """カスタム閾値"""
        score = score_rsi_continuous(
            10.0, oversold=20.0, overbought=80.0
        )
        assert score > 0


class TestNormalizeAtrByPrice:
    """normalize_atr_by_price のテスト"""

    def test_typical_fx_atr(self) -> None:
        """典型的なFXのATR（価格の1%程度）"""
        result = normalize_atr_by_price(1.5, 150.0)
        # 1.5/150 = 0.01 → 0.01/0.02 = 0.5
        assert result == pytest.approx(0.5)

    def test_high_volatility(self) -> None:
        """高ボラティリティ（2%以上）で1.0にキャップ"""
        result = normalize_atr_by_price(5.0, 150.0)
        assert result == 1.0

    def test_zero_price(self) -> None:
        """価格0で0.0を返す"""
        result = normalize_atr_by_price(1.0, 0.0)
        assert result == 0.0

    def test_negative_price(self) -> None:
        """負の価格で0.0を返す"""
        result = normalize_atr_by_price(1.0, -100.0)
        assert result == 0.0

    def test_zero_atr(self) -> None:
        """ATR=0 で0.0を返す"""
        result = normalize_atr_by_price(0.0, 150.0)
        assert result == pytest.approx(0.0)

    def test_custom_scale_factor(self) -> None:
        """カスタムスケールファクター"""
        result = normalize_atr_by_price(1.5, 150.0, scale_factor=0.01)
        # 1.5/150 = 0.01 → 0.01/0.01 = 1.0
        assert result == pytest.approx(1.0)
