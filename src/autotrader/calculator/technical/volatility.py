"""ボラティリティ系テクニカル指標

ATR, ボリンジャーバンドを計算。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta


@dataclass(frozen=True)
class VolatilityResult:
    """ボラティリティ指標計算結果

    Attributes:
        atr: ATR値
        atr_percent: ATR%（終値に対する比率）
        bb_upper: ボリンジャーバンド上限
        bb_middle: ボリンジャーバンド中央
        bb_lower: ボリンジャーバンド下限
        bb_width: バンド幅
        bb_percent_b: %B（バンド内位置）
    """

    atr: float | None
    atr_percent: float | None
    bb_upper: float | None
    bb_middle: float | None
    bb_lower: float | None
    bb_width: float | None
    bb_percent_b: float | None


class VolatilityIndicators:
    """ボラティリティ系指標の計算クラス

    Args:
        atr_period: ATR期間（デフォルト: 14）
        bb_period: ボリンジャーバンド期間（デフォルト: 20）
        bb_std: ボリンジャーバンド標準偏差倍率（デフォルト: 2.0）
    """

    def __init__(
        self,
        atr_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
    ) -> None:
        self.atr_period = atr_period
        self.bb_period = bb_period
        self.bb_std = bb_std

    def calculate_atr(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.Series:
        """ATRを計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.Series: ATR値
        """
        result = ta.atr(high, low, close, length=self.atr_period)
        if result is None:
            return pd.Series(np.nan, index=close.index)
        return result

    def calculate_atr_percent(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.Series:
        """ATR%を計算（終値に対する比率）

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.Series: ATR%値
        """
        atr = self.calculate_atr(high, low, close)
        return (atr / close) * 100

    def calculate_bollinger_bands(self, close: pd.Series) -> pd.DataFrame:
        """ボリンジャーバンドを計算

        Args:
            close: 終値系列

        Returns:
            pd.DataFrame: 上限, 中央, 下限, 幅, %B
        """
        result = ta.bbands(close, length=self.bb_period, std=self.bb_std)
        if result is None:
            return pd.DataFrame(
                {
                    "bb_upper": np.nan,
                    "bb_middle": np.nan,
                    "bb_lower": np.nan,
                    "bb_width": np.nan,
                    "bb_percent_b": np.nan,
                },
                index=close.index,
            )

        # pandas-taのバージョンで列名形式が異なる場合に対応
        cols = result.columns.tolist()
        col_lower = [c for c in cols if c.startswith("BBL")][0]
        col_mid = [c for c in cols if c.startswith("BBM")][0]
        col_upper = [c for c in cols if c.startswith("BBU")][0]
        col_width = [c for c in cols if c.startswith("BBB")][0]
        col_pct = [c for c in cols if c.startswith("BBP")][0]

        return pd.DataFrame(
            {
                "bb_upper": result[col_upper],
                "bb_middle": result[col_mid],
                "bb_lower": result[col_lower],
                "bb_width": result[col_width],
                "bb_percent_b": result[col_pct],
            }
        )

    def calculate_all(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.DataFrame:
        """全ボラティリティ指標を一括計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.DataFrame: 全ボラティリティ指標を含むデータフレーム
        """
        atr = self.calculate_atr(high, low, close)
        atr_pct = self.calculate_atr_percent(high, low, close)
        bb_df = self.calculate_bollinger_bands(close)

        result = pd.DataFrame(index=close.index)
        result[f"atr_{self.atr_period}"] = atr
        result[f"atr_percent_{self.atr_period}"] = atr_pct
        result["bb_upper"] = bb_df["bb_upper"]
        result["bb_middle"] = bb_df["bb_middle"]
        result["bb_lower"] = bb_df["bb_lower"]
        result["bb_width"] = bb_df["bb_width"]
        result["bb_percent_b"] = bb_df["bb_percent_b"]

        return result

    def get_volatility_result(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> VolatilityResult:
        """最新のボラティリティ指標結果を取得

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            VolatilityResult: 最新のボラティリティ指標
        """
        df = self.calculate_all(high, low, close)
        last = df.iloc[-1]

        return VolatilityResult(
            atr=last.get(f"atr_{self.atr_period}"),
            atr_percent=last.get(f"atr_percent_{self.atr_period}"),
            bb_upper=last.get("bb_upper"),
            bb_middle=last.get("bb_middle"),
            bb_lower=last.get("bb_lower"),
            bb_width=last.get("bb_width"),
            bb_percent_b=last.get("bb_percent_b"),
        )
