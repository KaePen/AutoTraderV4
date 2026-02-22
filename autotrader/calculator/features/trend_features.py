"""トレンド関連特徴量

トレンド強度、方向性、継続性などの特徴量を計算。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from autotrader.calculator.technical.trend import TrendIndicators


class TrendDirection(Enum):
    """トレンド方向"""

    STRONG_UP = "strong_up"
    UP = "up"
    NEUTRAL = "neutral"
    DOWN = "down"
    STRONG_DOWN = "strong_down"


@dataclass(frozen=True)
class TrendFeatureResult:
    """トレンド特徴量結果

    Attributes:
        direction: トレンド方向
        strength: トレンド強度（0-1）
        ma_alignment: MA整列度（短期MAが長期MAの上なら正）
        slope_consistency: 傾き一貫性（連続同方向なら高）
        deviation_score: 乖離度スコア（-1から1）
    """

    direction: TrendDirection
    strength: float
    ma_alignment: float
    slope_consistency: float
    deviation_score: float


class TrendFeatures:
    """トレンド特徴量計算クラス

    Args:
        short_ma_period: 短期MA期間（デフォルト: 10）
        long_ma_period: 長期MA期間（デフォルト: 50）
        adx_threshold: ADXトレンド判定閾値（デフォルト: 25）
        slope_lookback: 傾き一貫性判定期間（デフォルト: 5）
    """

    def __init__(
        self,
        short_ma_period: int = 10,
        long_ma_period: int = 50,
        adx_threshold: float = 25.0,
        slope_lookback: int = 5,
    ) -> None:
        self.short_ma_period = short_ma_period
        self.long_ma_period = long_ma_period
        self.adx_threshold = adx_threshold
        self.slope_lookback = slope_lookback

        self.short_trend = TrendIndicators(
            sma_period=short_ma_period, ema_period=short_ma_period
        )
        self.long_trend = TrendIndicators(
            sma_period=long_ma_period, ema_period=long_ma_period
        )

    def calculate_ma_alignment(self, close: pd.Series) -> pd.Series:
        """MA整列度を計算

        Args:
            close: 終値系列

        Returns:
            pd.Series: MA整列度（正=上昇トレンド、負=下降トレンド）
        """
        short_ma = self.short_trend.calculate_ema(close)
        long_ma = self.long_trend.calculate_ema(close)

        alignment = (short_ma - long_ma) / long_ma * 100
        return alignment.clip(-5, 5) / 5

    def calculate_slope_consistency(self, close: pd.Series) -> pd.Series:
        """傾き一貫性を計算

        Args:
            close: 終値系列

        Returns:
            pd.Series: 傾き一貫性（-1から1）
        """
        ma = self.short_trend.calculate_ema(close)
        slope = ma.diff()

        result = pd.Series(np.nan, index=close.index)

        for i in range(self.slope_lookback, len(close)):
            window = slope.iloc[i - self.slope_lookback + 1 : i + 1]
            positive_ratio = (window > 0).sum() / len(window)
            negative_ratio = (window < 0).sum() / len(window)
            result.iloc[i] = positive_ratio - negative_ratio

        return result

    def calculate_deviation_score(self, close: pd.Series) -> pd.Series:
        """乖離度スコアを計算

        Args:
            close: 終値系列

        Returns:
            pd.Series: 乖離度スコア（-1から1）
        """
        ma = self.long_trend.calculate_ema(close)
        deviation_pct = (close - ma) / ma * 100
        return deviation_pct.clip(-3, 3) / 3

    def calculate_trend_strength(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.Series:
        """トレンド強度を計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.Series: トレンド強度（0-1）
        """
        trend_ind = TrendIndicators(adx_period=14)
        adx_df = trend_ind.calculate_adx(high, low, close)
        adx = adx_df["ADX_14"]
        return (adx / 50).clip(0, 1)

    def determine_direction(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.Series:
        """トレンド方向を判定

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.Series: トレンド方向
        """
        ma_alignment = self.calculate_ma_alignment(close)
        strength = self.calculate_trend_strength(high, low, close)

        result = pd.Series(TrendDirection.NEUTRAL, index=close.index)

        for i in range(len(close)):
            align = ma_alignment.iloc[i]
            str_val = strength.iloc[i]

            if pd.isna(align) or pd.isna(str_val):
                continue

            if align > 0.3 and str_val > 0.5:
                result.iloc[i] = TrendDirection.STRONG_UP
            elif align > 0.1:
                result.iloc[i] = TrendDirection.UP
            elif align < -0.3 and str_val > 0.5:
                result.iloc[i] = TrendDirection.STRONG_DOWN
            elif align < -0.1:
                result.iloc[i] = TrendDirection.DOWN
            else:
                result.iloc[i] = TrendDirection.NEUTRAL

        return result

    def calculate_all(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.DataFrame:
        """全トレンド特徴量を一括計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.DataFrame: 全トレンド特徴量
        """
        result = pd.DataFrame(index=close.index)
        result["trend_direction"] = self.determine_direction(high, low, close)
        result["trend_strength"] = self.calculate_trend_strength(
            high, low, close
        )
        result["ma_alignment"] = self.calculate_ma_alignment(close)
        result["slope_consistency"] = self.calculate_slope_consistency(close)
        result["deviation_score"] = self.calculate_deviation_score(close)

        return result

    def get_trend_feature_result(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> TrendFeatureResult:
        """最新のトレンド特徴量結果を取得

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            TrendFeatureResult: 最新のトレンド特徴量
        """
        df = self.calculate_all(high, low, close)
        last = df.iloc[-1]

        return TrendFeatureResult(
            direction=last["trend_direction"],
            strength=float(last["trend_strength"])
            if not pd.isna(last["trend_strength"])
            else 0.0,
            ma_alignment=float(last["ma_alignment"])
            if not pd.isna(last["ma_alignment"])
            else 0.0,
            slope_consistency=float(last["slope_consistency"])
            if not pd.isna(last["slope_consistency"])
            else 0.0,
            deviation_score=float(last["deviation_score"])
            if not pd.isna(last["deviation_score"])
            else 0.0,
        )
