"""計算機モジュールのテスト"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from autotrader.calculator.technical.trend import TrendIndicators
from autotrader.calculator.technical.momentum import MomentumIndicators
from autotrader.calculator.technical.volatility import VolatilityIndicators
from autotrader.calculator.technical.price_structure import (
    PriceStructureIndicators,
    SwingType,
)
from autotrader.calculator.features.trend_features import (
    TrendFeatures,
    TrendDirection,
)
from autotrader.calculator.features.volatility_features import (
    VolatilityFeatures,
    VolatilityRegime,
)


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """サンプルOHLCVデータを生成"""
    np.random.seed(42)
    n = 200

    dates = pd.date_range(
        start=datetime(2024, 1, 1),
        periods=n,
        freq="15min",
    )

    # ランダムウォーク価格
    close = 150.0 + np.cumsum(np.random.randn(n) * 0.1)
    high = close + np.abs(np.random.randn(n) * 0.05)
    low = close - np.abs(np.random.randn(n) * 0.05)
    open_ = close + np.random.randn(n) * 0.02

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.random.randint(100, 1000, n),
        },
        index=dates,
    )


class TestTrendIndicators:
    """トレンド指標テスト"""

    def test_calculate_sma(self, sample_ohlcv: pd.DataFrame) -> None:
        """SMA計算テスト"""
        trend = TrendIndicators(sma_period=20)
        sma = trend.calculate_sma(sample_ohlcv["close"])

        assert len(sma) == len(sample_ohlcv)
        assert sma.iloc[:19].isna().all()
        assert not sma.iloc[19:].isna().any()

    def test_calculate_ema(self, sample_ohlcv: pd.DataFrame) -> None:
        """EMA計算テスト"""
        trend = TrendIndicators(ema_period=20)
        ema = trend.calculate_ema(sample_ohlcv["close"])

        assert len(ema) == len(sample_ohlcv)
        # EMAは最初から値がある
        assert not ema.iloc[19:].isna().any()

    def test_calculate_adx(self, sample_ohlcv: pd.DataFrame) -> None:
        """ADX計算テスト"""
        trend = TrendIndicators(adx_period=14)
        adx_df = trend.calculate_adx(
            sample_ohlcv["high"],
            sample_ohlcv["low"],
            sample_ohlcv["close"],
        )

        assert "ADX_14" in adx_df.columns
        assert "DMP_14" in adx_df.columns
        assert "DMN_14" in adx_df.columns

    def test_calculate_all(self, sample_ohlcv: pd.DataFrame) -> None:
        """全指標一括計算テスト"""
        trend = TrendIndicators()
        result = trend.calculate_all(
            sample_ohlcv["high"],
            sample_ohlcv["low"],
            sample_ohlcv["close"],
        )

        assert "sma_20" in result.columns
        assert "ema_20" in result.columns
        assert "adx_14" in result.columns


class TestMomentumIndicators:
    """モメンタム指標テスト"""

    def test_calculate_rsi(self, sample_ohlcv: pd.DataFrame) -> None:
        """RSI計算テスト"""
        momentum = MomentumIndicators(rsi_period=14)
        rsi = momentum.calculate_rsi(sample_ohlcv["close"])

        assert len(rsi) == len(sample_ohlcv)
        # RSI範囲確認
        valid_rsi = rsi.dropna()
        assert (valid_rsi >= 0).all() and (valid_rsi <= 100).all()

    def test_calculate_macd(self, sample_ohlcv: pd.DataFrame) -> None:
        """MACD計算テスト"""
        momentum = MomentumIndicators()
        macd_df = momentum.calculate_macd(sample_ohlcv["close"])

        assert "MACD" in macd_df.columns
        assert "MACD_signal" in macd_df.columns
        assert "MACD_histogram" in macd_df.columns

    def test_calculate_stochastics(self, sample_ohlcv: pd.DataFrame) -> None:
        """ストキャスティクス計算テスト"""
        momentum = MomentumIndicators()
        stoch_df = momentum.calculate_stochastics(
            sample_ohlcv["high"],
            sample_ohlcv["low"],
            sample_ohlcv["close"],
        )

        assert "stoch_k" in stoch_df.columns
        assert "stoch_d" in stoch_df.columns


class TestVolatilityIndicators:
    """ボラティリティ指標テスト"""

    def test_calculate_atr(self, sample_ohlcv: pd.DataFrame) -> None:
        """ATR計算テスト"""
        vol = VolatilityIndicators(atr_period=14)
        atr = vol.calculate_atr(
            sample_ohlcv["high"],
            sample_ohlcv["low"],
            sample_ohlcv["close"],
        )

        assert len(atr) == len(sample_ohlcv)
        # ATRは正の値
        valid_atr = atr.dropna()
        assert (valid_atr >= 0).all()

    def test_calculate_bollinger_bands(
        self, sample_ohlcv: pd.DataFrame
    ) -> None:
        """ボリンジャーバンド計算テスト"""
        vol = VolatilityIndicators(bb_period=20, bb_std=2.0)
        bb_df = vol.calculate_bollinger_bands(sample_ohlcv["close"])

        assert "bb_upper" in bb_df.columns
        assert "bb_middle" in bb_df.columns
        assert "bb_lower" in bb_df.columns

        # upper > middle > lower の関係
        valid_idx = bb_df["bb_upper"].notna()
        assert (
            bb_df.loc[valid_idx, "bb_upper"]
            >= bb_df.loc[valid_idx, "bb_middle"]
        ).all()
        assert (
            bb_df.loc[valid_idx, "bb_middle"]
            >= bb_df.loc[valid_idx, "bb_lower"]
        ).all()


class TestPriceStructureIndicators:
    """価格構造指標テスト"""

    def test_calculate_pivot_high(self, sample_ohlcv: pd.DataFrame) -> None:
        """ピボット高値計算テスト"""
        structure = PriceStructureIndicators(pivot_left=5, pivot_right=5)
        pivot_high = structure.calculate_pivot_high(sample_ohlcv["high"])

        assert len(pivot_high) == len(sample_ohlcv)
        # ピボット値はNaNか元の高値と同じ
        valid = pivot_high.dropna()
        assert len(valid) > 0

    def test_detect_swing_points(self, sample_ohlcv: pd.DataFrame) -> None:
        """スイングポイント検出テスト"""
        structure = PriceStructureIndicators()
        swing_points = structure.detect_swing_points(
            sample_ohlcv["high"],
            sample_ohlcv["low"],
        )

        assert len(swing_points) > 0
        for point in swing_points:
            assert point.swing_type in (SwingType.HIGH, SwingType.LOW)


class TestTrendFeatures:
    """トレンド特徴量テスト"""

    def test_calculate_ma_alignment(self, sample_ohlcv: pd.DataFrame) -> None:
        """MA整列度計算テスト"""
        features = TrendFeatures()
        alignment = features.calculate_ma_alignment(sample_ohlcv["close"])

        assert len(alignment) == len(sample_ohlcv)
        # -1から1の範囲
        valid = alignment.dropna()
        assert (valid >= -1).all() and (valid <= 1).all()

    def test_determine_direction(self, sample_ohlcv: pd.DataFrame) -> None:
        """トレンド方向判定テスト"""
        features = TrendFeatures()
        direction = features.determine_direction(
            sample_ohlcv["high"],
            sample_ohlcv["low"],
            sample_ohlcv["close"],
        )

        assert len(direction) == len(sample_ohlcv)
        # 全てTrendDirection
        for d in direction:
            assert isinstance(d, TrendDirection)

    def test_get_trend_feature_result(
        self, sample_ohlcv: pd.DataFrame
    ) -> None:
        """トレンド特徴量結果取得テスト"""
        features = TrendFeatures()
        result = features.get_trend_feature_result(
            sample_ohlcv["high"],
            sample_ohlcv["low"],
            sample_ohlcv["close"],
        )

        assert isinstance(result.direction, TrendDirection)
        assert 0 <= result.strength <= 1
        assert -1 <= result.ma_alignment <= 1


class TestVolatilityFeatures:
    """ボラティリティ特徴量テスト"""

    def test_determine_regime(self, sample_ohlcv: pd.DataFrame) -> None:
        """ボラティリティレジーム判定テスト"""
        features = VolatilityFeatures()
        regime = features.determine_regime(
            sample_ohlcv["high"],
            sample_ohlcv["low"],
            sample_ohlcv["close"],
        )

        assert len(regime) == len(sample_ohlcv)
        for r in regime:
            assert isinstance(r, VolatilityRegime)

    def test_get_volatility_feature_result(
        self, sample_ohlcv: pd.DataFrame
    ) -> None:
        """ボラティリティ特徴量結果取得テスト"""
        features = VolatilityFeatures()
        result = features.get_volatility_feature_result(
            sample_ohlcv["high"],
            sample_ohlcv["low"],
            sample_ohlcv["close"],
        )

        assert isinstance(result.regime, VolatilityRegime)
        assert result.normalized_atr >= 0
