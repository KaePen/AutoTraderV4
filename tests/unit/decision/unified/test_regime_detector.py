"""MarketRegimeDetectorのユニットテスト"""

from __future__ import annotations

import pandas as pd
import pytest

from autotrader.core.enums import MarketRegime
from autotrader.calculator.features.regime_detector import (
    MarketRegimeDetector,
    RegimeDetectorConfig,
)


class TestMarketRegimeDetector:
    """MarketRegimeDetectorのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.detector = MarketRegimeDetector()

    def test_high_vol_detection(self) -> None:
        """高ボラティリティ検出"""
        result = self.detector.detect(
            normalized_atr=2.0,  # 高ATR
            adx=15.0,           # 低ADX
            ma_alignment=0.1,
        )

        assert result.regime == MarketRegime.HIGH_VOL
        assert result.volatility_level == 2.0
        assert "高ボラ" in result.reasoning

    def test_trend_detection_strong(self) -> None:
        """強トレンド検出"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=35.0,           # 高ADX
            ma_alignment=0.5,   # MA整列
        )

        assert result.regime == MarketRegime.TREND
        assert "強トレンド" in result.reasoning

    def test_trend_detection_moderate(self) -> None:
        """中程度トレンド検出"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=25.0,           # 中程度ADX
            ma_alignment=0.4,   # MA整列
        )

        assert result.regime == MarketRegime.TREND
        assert "トレンド" in result.reasoning

    def test_low_vol_detection(self) -> None:
        """低ボラティリティ検出"""
        result = self.detector.detect(
            normalized_atr=0.5,  # 低ATR
            adx=18.0,
            ma_alignment=0.1,
        )

        assert result.regime == MarketRegime.LOW_VOL
        assert "低ボラ" in result.reasoning

    def test_range_detection(self) -> None:
        """レンジ検出"""
        result = self.detector.detect(
            normalized_atr=1.0,  # 通常ATR
            adx=18.0,           # 低ADX
            ma_alignment=0.1,   # MA非整列
        )

        assert result.regime == MarketRegime.RANGE
        assert "レンジ" in result.reasoning

    def test_nan_handling(self) -> None:
        """NaN値のハンドリング"""
        result = self.detector.detect(
            normalized_atr=float("nan"),
            adx=20.0,
            ma_alignment=0.5,
        )

        assert result.regime == MarketRegime.RANGE
        assert result.confidence == 0.0
        assert "データ不足" in result.reasoning

    def test_detect_from_row(self) -> None:
        """DataFrameの行からの検出"""
        row = pd.Series({
            "normalized_atr": 1.0,
            "adx": 30.0,
            "ma_alignment": 0.5,
        })

        result = self.detector.detect_from_row(row)

        assert result.regime == MarketRegime.TREND

    def test_detect_from_row_alt_columns(self) -> None:
        """代替カラム名での検出"""
        row = pd.Series({
            "norm_atr": 0.5,     # 代替名
            "ADX": 15.0,        # 代替名
            "trend_alignment": 0.1,  # 代替名
        })

        result = self.detector.detect_from_row(row)

        assert result.regime == MarketRegime.LOW_VOL

    def test_detect_from_row_missing_columns(self) -> None:
        """カラム欠損時のデフォルト値"""
        row = pd.Series({
            "close": 150.0,
        })

        result = self.detector.detect_from_row(row)

        # デフォルト値で判定
        assert result.regime is not None

    def test_trend_strength_calculation(self) -> None:
        """トレンド強度計算"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=40.0,  # 高ADX
            ma_alignment=0.5,
        )

        assert result.trend_strength == 1.0  # max

        result2 = self.detector.detect(
            normalized_atr=1.0,
            adx=20.0,  # 中ADX
            ma_alignment=0.5,
        )

        assert result2.trend_strength == 0.5  # 20/40

    def test_custom_config(self) -> None:
        """カスタム設定"""
        config = RegimeDetectorConfig(
            high_vol_atr_threshold=2.0,
            trend_adx_threshold=25.0,
        )
        detector = MarketRegimeDetector(config)

        # デフォルトでは HIGH_VOL になる値がTRENDに
        result = detector.detect(
            normalized_atr=1.8,  # 新閾値以下
            adx=30.0,
            ma_alignment=0.5,
        )

        assert result.regime == MarketRegime.TREND

    def test_confidence_values(self) -> None:
        """確度値の範囲"""
        for atr in [0.3, 0.8, 1.0, 1.5, 2.0]:
            for adx in [10.0, 20.0, 30.0, 40.0]:
                for ma in [0.0, 0.3, 0.6]:
                    result = self.detector.detect(atr, adx, ma)
                    assert 0.0 <= result.confidence <= 1.0
