"""ボラティリティ関連特徴量

ボラティリティレベル、レジーム、変動率などの特徴量を計算。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from autotrader.calculator.technical.volatility import VolatilityIndicators


class VolatilityRegime(Enum):
    """ボラティリティレジーム"""

    VERY_LOW = "very_low"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    VERY_HIGH = "very_high"


@dataclass(frozen=True)
class VolatilityFeatureResult:
    """ボラティリティ特徴量結果

    Attributes:
        regime: ボラティリティレジーム
        normalized_atr: 正規化ATR（過去平均比）
        bb_squeeze: BBスクイーズ度（0-1、1=強いスクイーズ）
        range_expansion: レンジ拡大率
        volatility_trend: ボラティリティトレンド（上昇/下降）
    """

    regime: VolatilityRegime
    normalized_atr: float
    bb_squeeze: float
    range_expansion: float
    volatility_trend: float


class VolatilityFeatures:
    """ボラティリティ特徴量計算クラス

    Args:
        atr_period: ATR期間（デフォルト: 14）
        bb_period: BB期間（デフォルト: 20）
        lookback_period: 正規化用ルックバック期間（デフォルト: 100）
    """

    def __init__(
        self,
        atr_period: int = 14,
        bb_period: int = 20,
        lookback_period: int = 100,
    ) -> None:
        self.atr_period = atr_period
        self.bb_period = bb_period
        self.lookback_period = lookback_period

        self.volatility = VolatilityIndicators(
            atr_period=atr_period, bb_period=bb_period
        )

    def calculate_normalized_atr(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.Series:
        """正規化ATRを計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.Series: 正規化ATR
        """
        atr = self.volatility.calculate_atr(high, low, close)
        atr_mean = atr.rolling(window=self.lookback_period).mean()
        return atr / atr_mean

    def calculate_bb_squeeze(self, close: pd.Series) -> pd.Series:
        """BBスクイーズ度を計算

        Args:
            close: 終値系列

        Returns:
            pd.Series: スクイーズ度（0-1）
        """
        bb_df = self.volatility.calculate_bollinger_bands(close)
        bb_width = bb_df["bb_width"]

        min_width = bb_width.rolling(window=self.lookback_period).min()
        max_width = bb_width.rolling(window=self.lookback_period).max()

        width_range = max_width - min_width
        squeeze = 1 - (bb_width - min_width) / width_range.replace(0, np.nan)
        return squeeze.clip(0, 1)

    def calculate_range_expansion(
        self, high: pd.Series, low: pd.Series
    ) -> pd.Series:
        """レンジ拡大率を計算

        Args:
            high: 高値系列
            low: 安値系列

        Returns:
            pd.Series: レンジ拡大率
        """
        current_range = high - low
        avg_range = current_range.rolling(window=self.lookback_period).mean()
        return current_range / avg_range

    def calculate_volatility_trend(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.Series:
        """ボラティリティトレンドを計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.Series: ボラティリティトレンド
        """
        atr = self.volatility.calculate_atr(high, low, close)
        atr_slope = atr.diff(periods=5) / atr.shift(5)
        return atr_slope.clip(-0.1, 0.1) / 0.1

    def determine_regime(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.Series:
        """ボラティリティレジームを判定

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.Series: ボラティリティレジーム
        """
        normalized_atr = self.calculate_normalized_atr(high, low, close)

        result = pd.Series(VolatilityRegime.NORMAL, index=close.index)

        for i in range(len(close)):
            val = normalized_atr.iloc[i]

            if pd.isna(val):
                continue

            if val < 0.5:
                result.iloc[i] = VolatilityRegime.VERY_LOW
            elif val < 0.8:
                result.iloc[i] = VolatilityRegime.LOW
            elif val < 1.2:
                result.iloc[i] = VolatilityRegime.NORMAL
            elif val < 1.5:
                result.iloc[i] = VolatilityRegime.HIGH
            else:
                result.iloc[i] = VolatilityRegime.VERY_HIGH

        return result

    def calculate_all(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.DataFrame:
        """全ボラティリティ特徴量を一括計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.DataFrame: 全ボラティリティ特徴量
        """
        result = pd.DataFrame(index=close.index)
        result["volatility_regime"] = self.determine_regime(high, low, close)
        result["normalized_atr"] = self.calculate_normalized_atr(
            high, low, close
        )
        result["bb_squeeze"] = self.calculate_bb_squeeze(close)
        result["range_expansion"] = self.calculate_range_expansion(high, low)
        result["volatility_trend"] = self.calculate_volatility_trend(
            high, low, close
        )

        return result

    def get_volatility_feature_result(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> VolatilityFeatureResult:
        """最新のボラティリティ特徴量結果を取得

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            VolatilityFeatureResult: 最新のボラティリティ特徴量
        """
        df = self.calculate_all(high, low, close)
        last = df.iloc[-1]

        return VolatilityFeatureResult(
            regime=last["volatility_regime"],
            normalized_atr=float(last["normalized_atr"])
            if not pd.isna(last["normalized_atr"])
            else 1.0,
            bb_squeeze=float(last["bb_squeeze"])
            if not pd.isna(last["bb_squeeze"])
            else 0.0,
            range_expansion=float(last["range_expansion"])
            if not pd.isna(last["range_expansion"])
            else 1.0,
            volatility_trend=float(last["volatility_trend"])
            if not pd.isna(last["volatility_trend"])
            else 0.0,
        )
