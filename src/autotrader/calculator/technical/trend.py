"""トレンド系テクニカル指標

SMA, EMA, ADX, +DI/-DI, スロープ, 乖離率を計算。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta


@dataclass(frozen=True)
class TrendResult:
    """トレンド指標計算結果

    Attributes:
        sma: 単純移動平均
        ema: 指数移動平均
        slope: 傾き（正規化）
        deviation: 乖離率
        adx: ADX値
        plus_di: +DI値
        minus_di: -DI値
    """

    sma: float | None
    ema: float | None
    slope: float | None
    deviation: float | None
    adx: float | None
    plus_di: float | None
    minus_di: float | None


class TrendIndicators:
    """トレンド系指標の計算クラス

    Args:
        sma_period: SMA期間（デフォルト: 20）
        ema_period: EMA期間（デフォルト: 20）
        adx_period: ADX期間（デフォルト: 14）
        slope_period: 傾き計算期間（デフォルト: 5）
    """

    def __init__(
        self,
        sma_period: int = 20,
        ema_period: int = 20,
        adx_period: int = 14,
        slope_period: int = 5,
    ) -> None:
        self.sma_period = sma_period
        self.ema_period = ema_period
        self.adx_period = adx_period
        self.slope_period = slope_period

    def calculate_sma(self, close: pd.Series) -> pd.Series:
        """単純移動平均を計算

        Args:
            close: 終値系列

        Returns:
            pd.Series: SMA値
        """
        return ta.sma(close, length=self.sma_period)

    def calculate_ema(self, close: pd.Series) -> pd.Series:
        """指数移動平均を計算

        Args:
            close: 終値系列

        Returns:
            pd.Series: EMA値
        """
        return ta.ema(close, length=self.ema_period)

    def calculate_slope(self, series: pd.Series) -> pd.Series:
        """正規化された傾きを計算

        Args:
            series: 数値系列

        Returns:
            pd.Series: 傾き（変化率）
        """
        return series.pct_change(periods=self.slope_period)

    def calculate_deviation(
        self, close: pd.Series, ma: pd.Series
    ) -> pd.Series:
        """移動平均からの乖離率を計算

        Args:
            close: 終値系列
            ma: 移動平均系列

        Returns:
            pd.Series: 乖離率（%）
        """
        return ((close - ma) / ma) * 100

    def calculate_adx(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.DataFrame:
        """ADX, +DI, -DIを計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.DataFrame: ADX, +DI, -DI列を含むデータフレーム
        """
        result = ta.adx(high, low, close, length=self.adx_period)
        if result is None:
            return pd.DataFrame(
                {
                    f"ADX_{self.adx_period}": np.nan,
                    f"DMP_{self.adx_period}": np.nan,
                    f"DMN_{self.adx_period}": np.nan,
                },
                index=close.index,
            )
        return result

    def calculate_all(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.DataFrame:
        """全トレンド指標を一括計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.DataFrame: 全トレンド指標を含むデータフレーム
        """
        sma = self.calculate_sma(close)
        ema = self.calculate_ema(close)
        adx_df = self.calculate_adx(high, low, close)

        result = pd.DataFrame(index=close.index)
        result[f"sma_{self.sma_period}"] = sma
        result[f"ema_{self.ema_period}"] = ema
        result[f"sma_slope_{self.slope_period}"] = self.calculate_slope(sma)
        result[f"ema_slope_{self.slope_period}"] = self.calculate_slope(ema)
        result["sma_deviation"] = self.calculate_deviation(close, sma)
        result["ema_deviation"] = self.calculate_deviation(close, ema)
        result[f"adx_{self.adx_period}"] = adx_df[f"ADX_{self.adx_period}"]
        result[f"plus_di_{self.adx_period}"] = adx_df[f"DMP_{self.adx_period}"]
        result[f"minus_di_{self.adx_period}"] = adx_df[
            f"DMN_{self.adx_period}"
        ]

        return result

    def get_trend_result(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> TrendResult:
        """最新のトレンド指標結果を取得

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            TrendResult: 最新のトレンド指標
        """
        df = self.calculate_all(high, low, close)
        last = df.iloc[-1]

        return TrendResult(
            sma=last.get(f"sma_{self.sma_period}"),
            ema=last.get(f"ema_{self.ema_period}"),
            slope=last.get(f"sma_slope_{self.slope_period}"),
            deviation=last.get("sma_deviation"),
            adx=last.get(f"adx_{self.adx_period}"),
            plus_di=last.get(f"plus_di_{self.adx_period}"),
            minus_di=last.get(f"minus_di_{self.adx_period}"),
        )
