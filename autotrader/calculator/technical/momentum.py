"""モメンタム系テクニカル指標

RSI, MACD, Stochastics を計算。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta


@dataclass(frozen=True)
class MomentumResult:
    """モメンタム指標計算結果

    Attributes:
        rsi: RSI値
        macd: MACD値
        macd_signal: MACDシグナル
        macd_histogram: MACDヒストグラム
        stoch_k: ストキャスティクス%K
        stoch_d: ストキャスティクス%D
    """

    rsi: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    stoch_k: float | None
    stoch_d: float | None


class MomentumIndicators:
    """モメンタム系指標の計算クラス

    Args:
        rsi_period: RSI期間（デフォルト: 14）
        macd_fast: MACD短期EMA期間（デフォルト: 12）
        macd_slow: MACD長期EMA期間（デフォルト: 26）
        macd_signal: MACDシグナル期間（デフォルト: 9）
        stoch_k: ストキャスティクス%K期間（デフォルト: 14）
        stoch_d: ストキャスティクス%D期間（デフォルト: 3）
        stoch_smooth: ストキャスティクス平滑化期間（デフォルト: 3）
    """

    def __init__(
        self,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        stoch_k: int = 14,
        stoch_d: int = 3,
        stoch_smooth: int = 3,
    ) -> None:
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal_period = macd_signal
        self.stoch_k = stoch_k
        self.stoch_d = stoch_d
        self.stoch_smooth = stoch_smooth

    def calculate_rsi(self, close: pd.Series) -> pd.Series:
        """RSIを計算

        Args:
            close: 終値系列

        Returns:
            pd.Series: RSI値
        """
        result = ta.rsi(close, length=self.rsi_period)
        if result is None:
            return pd.Series(np.nan, index=close.index)
        return result

    def calculate_macd(self, close: pd.Series) -> pd.DataFrame:
        """MACDを計算

        Args:
            close: 終値系列

        Returns:
            pd.DataFrame: MACD, シグナル, ヒストグラム
        """
        result = ta.macd(
            close,
            fast=self.macd_fast,
            slow=self.macd_slow,
            signal=self.macd_signal_period,
        )
        if result is None:
            return pd.DataFrame(
                {
                    "MACD": np.nan,
                    "MACD_signal": np.nan,
                    "MACD_histogram": np.nan,
                },
                index=close.index,
            )

        col_macd = f"MACD_{self.macd_fast}_{self.macd_slow}_{self.macd_signal_period}"
        col_signal = f"MACDs_{self.macd_fast}_{self.macd_slow}_{self.macd_signal_period}"
        col_hist = f"MACDh_{self.macd_fast}_{self.macd_slow}_{self.macd_signal_period}"

        return pd.DataFrame(
            {
                "MACD": result[col_macd],
                "MACD_signal": result[col_signal],
                "MACD_histogram": result[col_hist],
            }
        )

    def calculate_stochastics(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.DataFrame:
        """ストキャスティクスを計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.DataFrame: %K, %D値
        """
        result = ta.stoch(
            high,
            low,
            close,
            k=self.stoch_k,
            d=self.stoch_d,
            smooth_k=self.stoch_smooth,
        )
        if result is None:
            return pd.DataFrame(
                {"stoch_k": np.nan, "stoch_d": np.nan}, index=close.index
            )

        col_k = f"STOCHk_{self.stoch_k}_{self.stoch_d}_{self.stoch_smooth}"
        col_d = f"STOCHd_{self.stoch_k}_{self.stoch_d}_{self.stoch_smooth}"

        return pd.DataFrame(
            {"stoch_k": result[col_k], "stoch_d": result[col_d]}
        )

    def calculate_all(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> pd.DataFrame:
        """全モメンタム指標を一括計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            pd.DataFrame: 全モメンタム指標を含むデータフレーム
        """
        rsi = self.calculate_rsi(close)
        macd_df = self.calculate_macd(close)
        stoch_df = self.calculate_stochastics(high, low, close)

        result = pd.DataFrame(index=close.index)
        result[f"rsi_{self.rsi_period}"] = rsi
        result["macd"] = macd_df["MACD"]
        result["macd_signal"] = macd_df["MACD_signal"]
        result["macd_histogram"] = macd_df["MACD_histogram"]
        result["stoch_k"] = stoch_df["stoch_k"]
        result["stoch_d"] = stoch_df["stoch_d"]

        return result

    def get_momentum_result(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> MomentumResult:
        """最新のモメンタム指標結果を取得

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列

        Returns:
            MomentumResult: 最新のモメンタム指標
        """
        df = self.calculate_all(high, low, close)
        last = df.iloc[-1]

        return MomentumResult(
            rsi=last.get(f"rsi_{self.rsi_period}"),
            macd=last.get("macd"),
            macd_signal=last.get("macd_signal"),
            macd_histogram=last.get("macd_histogram"),
            stoch_k=last.get("stoch_k"),
            stoch_d=last.get("stoch_d"),
        )
