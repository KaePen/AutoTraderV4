"""IndicatorStrengthCalculatorのユニットテスト"""

from __future__ import annotations

import pandas as pd

from autotrader.decision.unified.strength_calculator import (
    IndicatorStrength,
    IndicatorStrengthCalculator,
)
from autotrader.decision.unified.config import StrengthConfig


class TestIndicatorStrength:
    """IndicatorStrengthのテスト"""

    def test_creation(self) -> None:
        """作成テスト"""
        strength = IndicatorStrength(
            rsi=0.8,
            macd=0.6,
            bollinger=0.5,
            stochastic=0.7,
            trend=0.6,
        )
        assert strength.rsi == 0.8
        assert strength.macd == 0.6

    def test_total_strength(self) -> None:
        """total_strengthプロパティテスト"""
        strength = IndicatorStrength(
            rsi=0.8,
            macd=0.6,
            bollinger=0.5,
            stochastic=0.7,
            trend=0.6,
        )
        total = strength.total_strength
        assert isinstance(total, float)
        assert -1.0 <= total <= 1.0

    def test_buy_strength(self) -> None:
        """buy_strengthプロパティテスト"""
        strength = IndicatorStrength(
            rsi=0.8,
            macd=0.6,
            trend=0.5,
        )
        assert strength.buy_strength >= 0

    def test_sell_strength(self) -> None:
        """sell_strengthプロパティテスト"""
        strength = IndicatorStrength(
            rsi=-0.8,
            macd=-0.6,
            trend=-0.5,
        )
        assert strength.sell_strength >= 0


class TestIndicatorStrengthCalculator:
    """IndicatorStrengthCalculatorのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.calculator = IndicatorStrengthCalculator()

    def test_init(self) -> None:
        """初期化テスト"""
        assert self.calculator.config is not None

    def test_init_with_config(self) -> None:
        """カスタム設定での初期化"""
        config = StrengthConfig(
            rsi_oversold=25.0,
            rsi_overbought=75.0,
        )
        calculator = IndicatorStrengthCalculator(config)
        assert calculator.config.rsi_oversold == 25.0

    def test_calculate_with_rsi(self) -> None:
        """RSI計算テスト"""
        row = pd.Series({
            "rsi_14": 25.0,  # 売られすぎ
            "close": 150.0,
        })
        strength = self.calculator.calculate(row)
        assert strength.rsi > 0  # 買い強度

    def test_calculate_with_macd(self) -> None:
        """MACD計算テスト"""
        row = pd.Series({
            "macd": 0.1,
            "macd_signal": 0.05,
            "macd_histogram": 0.05,
            "close": 150.0,
        })
        strength = self.calculator.calculate(row)
        # MACDがシグナルを上回る→買い
        assert isinstance(strength.macd, float)

    def test_calculate_with_stoch(self) -> None:
        """ストキャスティクス計算テスト"""
        row = pd.Series({
            "stoch_k": 15.0,  # 売られすぎ
            "stoch_d": 20.0,
            "close": 150.0,
        })
        strength = self.calculator.calculate(row)
        assert strength.stochastic > 0  # 買い強度

    def test_calculate_with_trend(self) -> None:
        """トレンド計算テスト"""
        row = pd.Series({
            "sma_20": 149.5,
            "sma_50": 149.0,
            "close": 150.0,  # SMA上にある
        })
        strength = self.calculator.calculate(row)
        assert strength.trend >= 0  # 上昇トレンド

    def test_calculate_with_bb(self) -> None:
        """ボリンジャーバンド計算テスト"""
        row = pd.Series({
            "bb_position": 0.1,  # 下限近く
            "close": 150.0,
        })
        strength = self.calculator.calculate(row)
        # BB下限近く→買い
        assert isinstance(strength.bollinger, float)

    def test_calculate_missing_indicators(self) -> None:
        """指標欠損時の計算"""
        row = pd.Series({
            "close": 150.0,
        })
        strength = self.calculator.calculate(row)
        # エラーなく計算できること
        assert isinstance(strength, IndicatorStrength)

    def test_calculate_nan_values(self) -> None:
        """NaN値での計算"""
        row = pd.Series({
            "rsi_14": float("nan"),
            "macd": float("nan"),
            "close": 150.0,
        })
        strength = self.calculator.calculate(row)
        assert isinstance(strength, IndicatorStrength)

    def test_overbought_rsi(self) -> None:
        """RSI買われすぎテスト"""
        row = pd.Series({
            "rsi_14": 80.0,  # 買われすぎ
            "close": 150.0,
        })
        strength = self.calculator.calculate(row)
        assert strength.rsi < 0  # 売り強度

    def test_overbought_stoch(self) -> None:
        """ストキャスティクス買われすぎテスト"""
        row = pd.Series({
            "stoch_k": 85.0,  # 買われすぎ
            "close": 150.0,
        })
        strength = self.calculator.calculate(row)
        assert strength.stochastic < 0  # 売り強度

    def test_downtrend(self) -> None:
        """下降トレンドテスト"""
        row = pd.Series({
            "sma_20": 150.5,
            "sma_50": 151.0,
            "close": 150.0,  # SMA下にある
        })
        strength = self.calculator.calculate(row)
        assert strength.trend <= 0  # 下降トレンド

    def test_atr_normalized(self) -> None:
        """正規化ATRテスト"""
        row = pd.Series({
            "atr_14": 0.20,
            "close": 150.0,
        })
        strength = self.calculator.calculate(row)
        assert isinstance(strength.atr_normalized, float)
