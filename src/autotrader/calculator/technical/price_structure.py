"""価格構造系テクニカル指標

Pivot高値/安値, スイング点を計算。
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
    NONE = "none"


@dataclass(frozen=True)
class SwingPoint:
    """スイングポイント

    Attributes:
        index: ポイントのインデックス
        price: 価格
        swing_type: スイングタイプ（HIGH/LOW）
    """

    index: int
    price: float
    swing_type: SwingType


@dataclass(frozen=True)
class PriceStructureResult:
    """価格構造指標計算結果

    Attributes:
        pivot_high: 直近のピボット高値
        pivot_low: 直近のピボット安値
        higher_high: より高い高値が形成されたか
        lower_low: より低い安値が形成されたか
        swing_points: スイングポイントリスト
    """

    pivot_high: float | None
    pivot_low: float | None
    higher_high: bool
    lower_low: bool
    swing_points: list[SwingPoint]


class PriceStructureIndicators:
    """価格構造系指標の計算クラス

    Args:
        pivot_left: ピボット左期間（デフォルト: 5）
        pivot_right: ピボット右期間（デフォルト: 5）
        swing_lookback: スイング検出ルックバック期間（デフォルト: 20）
    """

    def __init__(
        self,
        pivot_left: int = 5,
        pivot_right: int = 5,
        swing_lookback: int = 20,
    ) -> None:
        self.pivot_left = pivot_left
        self.pivot_right = pivot_right
        self.swing_lookback = swing_lookback

    def calculate_pivot_high(self, high: pd.Series) -> pd.Series:
        """ピボット高値を計算

        Args:
            high: 高値系列

        Returns:
            pd.Series: ピボット高値（該当バーのみ値あり）
        """
        result = pd.Series(np.nan, index=high.index)

        for i in range(self.pivot_left, len(high) - self.pivot_right):
            window_high = high.iloc[
                i - self.pivot_left : i + self.pivot_right + 1
            ]
            center_value = high.iloc[i]

            if center_value == window_high.max():
                result.iloc[i] = center_value

        return result

    def calculate_pivot_low(self, low: pd.Series) -> pd.Series:
        """ピボット安値を計算

        Args:
            low: 安値系列

        Returns:
            pd.Series: ピボット安値（該当バーのみ値あり）
        """
        result = pd.Series(np.nan, index=low.index)

        for i in range(self.pivot_left, len(low) - self.pivot_right):
            window_low = low.iloc[
                i - self.pivot_left : i + self.pivot_right + 1
            ]
            center_value = low.iloc[i]

            if center_value == window_low.min():
                result.iloc[i] = center_value

        return result

    def detect_swing_points(
        self, high: pd.Series, low: pd.Series
    ) -> list[SwingPoint]:
        """スイングポイントを検出

        Args:
            high: 高値系列
            low: 安値系列

        Returns:
            list[SwingPoint]: 検出されたスイングポイントリスト
        """
        pivot_highs = self.calculate_pivot_high(high)
        pivot_lows = self.calculate_pivot_low(low)

        swing_points: list[SwingPoint] = []

        for i in range(len(high)):
            if not pd.isna(pivot_highs.iloc[i]):
                swing_points.append(
                    SwingPoint(
                        index=i,
                        price=float(pivot_highs.iloc[i]),
                        swing_type=SwingType.HIGH,
                    )
                )
            if not pd.isna(pivot_lows.iloc[i]):
                swing_points.append(
                    SwingPoint(
                        index=i,
                        price=float(pivot_lows.iloc[i]),
                        swing_type=SwingType.LOW,
                    )
                )

        swing_points.sort(key=lambda x: x.index)
        return swing_points

    def check_higher_high(self, high: pd.Series) -> bool:
        """より高い高値が形成されたかチェック

        Args:
            high: 高値系列

        Returns:
            bool: 直近2つのピボット高値を比較
        """
        pivot_highs = self.calculate_pivot_high(high)
        valid_highs = pivot_highs.dropna()

        if len(valid_highs) < 2:
            return False

        return float(valid_highs.iloc[-1]) > float(valid_highs.iloc[-2])

    def check_lower_low(self, low: pd.Series) -> bool:
        """より低い安値が形成されたかチェック

        Args:
            low: 安値系列

        Returns:
            bool: 直近2つのピボット安値を比較
        """
        pivot_lows = self.calculate_pivot_low(low)
        valid_lows = pivot_lows.dropna()

        if len(valid_lows) < 2:
            return False

        return float(valid_lows.iloc[-1]) < float(valid_lows.iloc[-2])

    def calculate_all(
        self, high: pd.Series, low: pd.Series
    ) -> pd.DataFrame:
        """全価格構造指標を一括計算

        Args:
            high: 高値系列
            low: 安値系列

        Returns:
            pd.DataFrame: 全価格構造指標を含むデータフレーム
        """
        pivot_highs = self.calculate_pivot_high(high)
        pivot_lows = self.calculate_pivot_low(low)

        result = pd.DataFrame(index=high.index)
        result["pivot_high"] = pivot_highs
        result["pivot_low"] = pivot_lows
        result["last_pivot_high"] = pivot_highs.ffill()
        result["last_pivot_low"] = pivot_lows.ffill()

        return result

    def get_price_structure_result(
        self, high: pd.Series, low: pd.Series
    ) -> PriceStructureResult:
        """最新の価格構造指標結果を取得

        Args:
            high: 高値系列
            low: 安値系列

        Returns:
            PriceStructureResult: 最新の価格構造指標
        """
        df = self.calculate_all(high, low)
        last = df.iloc[-1]

        swing_points = self.detect_swing_points(
            high.iloc[-self.swing_lookback:],
            low.iloc[-self.swing_lookback:],
        )

        return PriceStructureResult(
            pivot_high=last.get("last_pivot_high"),
            pivot_low=last.get("last_pivot_low"),
            higher_high=self.check_higher_high(high),
            lower_low=self.check_lower_low(low),
            swing_points=swing_points,
        )
