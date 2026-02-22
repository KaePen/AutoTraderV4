"""スイングポイント検出

ローソク足データからスイングハイ/ローを検出し、
市場構造分析の基礎データを提供する。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class SwingType(Enum):
    """スイングタイプ"""

    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class SwingPoint:
    """スイングポイント

    Attributes:
        index: DataFrameインデックス
        bar_index: 0始まりのバーインデックス
        price: スイングポイントの価格
        swing_type: HIGH or LOW
        timestamp: タイムスタンプ（存在する場合）
    """

    index: any
    bar_index: int
    price: float
    swing_type: SwingType
    timestamp: pd.Timestamp | None = None


class SwingAnalyzer:
    """スイングポイント検出器

    左右n本のローソク足と比較して、中央が最高値/最安値なら
    スイングハイ/ローとして検出する。

    Args:
        lookback: 左側の比較本数（デフォルト: 5）
        lookforward: 右側の比較本数（デフォルト: 2）
    """

    def __init__(
        self,
        lookback: int = 5,
        lookforward: int = 2,
    ) -> None:
        self.lookback = lookback
        self.lookforward = lookforward

    def detect_swing_highs(self, df: pd.DataFrame) -> pd.Series:
        """スイングハイを検出（ベクトル化版）

        Args:
            df: high列を含むDataFrame

        Returns:
            pd.Series: bool型のスイングハイフラグ
        """
        high = df["high"].values
        n = len(high)
        result = np.zeros(n, dtype=bool)

        # ローリングmax計算（左右のウィンドウ）
        left_max = pd.Series(high).rolling(
            window=self.lookback, min_periods=self.lookback
        ).max().shift(1).values

        right_max = pd.Series(high[::-1]).rolling(
            window=self.lookforward, min_periods=self.lookforward
        ).max().shift(1).values[::-1]

        # 中央が両側より高い
        valid_range = slice(self.lookback, n - self.lookforward)
        result[valid_range] = (
            (high[valid_range] > left_max[valid_range]) &
            (high[valid_range] >= right_max[valid_range])
        )

        return pd.Series(result, index=df.index)

    def detect_swing_lows(self, df: pd.DataFrame) -> pd.Series:
        """スイングローを検出（ベクトル化版）

        Args:
            df: low列を含むDataFrame

        Returns:
            pd.Series: bool型のスイングローフラグ
        """
        low = df["low"].values
        n = len(low)
        result = np.zeros(n, dtype=bool)

        # ローリングmin計算（左右のウィンドウ）
        left_min = pd.Series(low).rolling(
            window=self.lookback, min_periods=self.lookback
        ).min().shift(1).values

        right_min = pd.Series(low[::-1]).rolling(
            window=self.lookforward, min_periods=self.lookforward
        ).min().shift(1).values[::-1]

        # 中央が両側より低い
        valid_range = slice(self.lookback, n - self.lookforward)
        result[valid_range] = (
            (low[valid_range] < left_min[valid_range]) &
            (low[valid_range] <= right_min[valid_range])
        )

        return pd.Series(result, index=df.index)

    def get_swing_points(self, df: pd.DataFrame) -> list[SwingPoint]:
        """スイングポイントをリストで取得

        Args:
            df: OHLC DataFrame

        Returns:
            list[SwingPoint]: 時系列順のスイングポイントリスト
        """
        swing_high_flags = self.detect_swing_highs(df)
        swing_low_flags = self.detect_swing_lows(df)

        points: list[SwingPoint] = []
        high = df["high"].values
        low = df["low"].values

        for bar_idx, idx in enumerate(df.index):
            timestamp = idx if isinstance(idx, pd.Timestamp) else None

            if swing_high_flags.iloc[bar_idx]:
                points.append(
                    SwingPoint(
                        index=idx,
                        bar_index=bar_idx,
                        price=float(high[bar_idx]),
                        swing_type=SwingType.HIGH,
                        timestamp=timestamp,
                    )
                )
            if swing_low_flags.iloc[bar_idx]:
                points.append(
                    SwingPoint(
                        index=idx,
                        bar_index=bar_idx,
                        price=float(low[bar_idx]),
                        swing_type=SwingType.LOW,
                        timestamp=timestamp,
                    )
                )

        points.sort(key=lambda x: x.bar_index)
        return points

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """全スイング関連指標を計算

        Args:
            df: OHLC DataFrame

        Returns:
            pd.DataFrame: スイング指標を追加したDataFrame
        """
        result = pd.DataFrame(index=df.index)

        # スイングポイント検出（bool型）
        swing_high_flags = self.detect_swing_highs(df)
        swing_low_flags = self.detect_swing_lows(df)

        result["swing_high"] = swing_high_flags
        result["swing_low"] = swing_low_flags

        # 直近のスイングポイント価格（フォワードフィル）
        high_prices = df["high"].where(swing_high_flags)
        low_prices = df["low"].where(swing_low_flags)

        result["last_swing_high"] = high_prices.ffill()
        result["last_swing_low"] = low_prices.ffill()

        # スイングポイントからの距離（バー数）
        result["bars_since_swing_high"] = swing_high_flags.cumsum()
        result["bars_since_swing_high"] = (
            result.groupby("bars_since_swing_high").cumcount()
        )

        result["bars_since_swing_low"] = swing_low_flags.cumsum()
        result["bars_since_swing_low"] = (
            result.groupby("bars_since_swing_low").cumcount()
        )

        return result

    def get_recent_swings(
        self,
        df: pd.DataFrame,
        n_highs: int = 3,
        n_lows: int = 3,
    ) -> tuple[list[SwingPoint], list[SwingPoint]]:
        """直近のスイングハイ/ローを取得

        Args:
            df: OHLC DataFrame
            n_highs: 取得するスイングハイの数
            n_lows: 取得するスイングローの数

        Returns:
            tuple: (直近スイングハイリスト, 直近スイングローリスト)
        """
        all_points = self.get_swing_points(df)

        highs = [p for p in all_points if p.swing_type == SwingType.HIGH]
        lows = [p for p in all_points if p.swing_type == SwingType.LOW]

        recent_highs = highs[-n_highs:] if len(highs) >= n_highs else highs
        recent_lows = lows[-n_lows:] if len(lows) >= n_lows else lows

        return recent_highs, recent_lows
