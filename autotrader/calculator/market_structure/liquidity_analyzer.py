"""流動性ゾーン分析

Smart Money Concept (SMC) に基づいて流動性ゾーンを特定し、
ストップハンティングパターンを検出する。

流動性ゾーン:
- 買い側流動性 (Buy Side Liquidity): 直近高値の上にあるショートのSL
- 売り側流動性 (Sell Side Liquidity): 直近安値の下にあるロングのSL

Equal Highs/Lows:
- 同じ価格レベルで複数回高値/安値を形成した場合、より多くのSLが集中
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from autotrader.calculator.market_structure.swing_analyzer import (
    SwingAnalyzer,
    SwingPoint,
    SwingType,
)


class LiquidityType(Enum):
    """流動性タイプ"""

    BUY_SIDE = "buy_side"  # 高値の上（ショートのSL）
    SELL_SIDE = "sell_side"  # 安値の下（ロングのSL）


@dataclass
class LiquidityZone:
    """流動性ゾーン

    Attributes:
        level: 価格レベル
        liquidity_type: BUY_SIDE or SELL_SIDE
        strength: ゾーンの強さ（同価格のスイング数）
        bar_indices: このレベルを形成したバーインデックス
        swept: 流動性が取られたかどうか
    """

    level: float
    liquidity_type: LiquidityType
    strength: int
    bar_indices: list[int]
    swept: bool = False


class LiquidityAnalyzer:
    """流動性ゾーン分析器

    Args:
        swing_analyzer: スイング検出器
        tolerance_pips: 同一レベルとみなす許容範囲（pips）
        pip_value: 1pipの価格（デフォルト: 0.01 for JPY pairs）
        min_zone_strength: 有効なゾーンとみなす最小強度
    """

    def __init__(
        self,
        swing_analyzer: SwingAnalyzer | None = None,
        tolerance_pips: float = 5.0,
        pip_value: float = 0.01,
        min_zone_strength: int = 1,
    ) -> None:
        self.swing_analyzer = swing_analyzer or SwingAnalyzer()
        self.tolerance_pips = tolerance_pips
        self.pip_value = pip_value
        self.min_zone_strength = min_zone_strength
        self._tolerance = tolerance_pips * pip_value

    def find_liquidity_zones(
        self,
        df: pd.DataFrame,
        n_recent_swings: int = 10,
    ) -> list[LiquidityZone]:
        """流動性ゾーンを検出

        Args:
            df: OHLC DataFrame
            n_recent_swings: 考慮する直近スイング数

        Returns:
            list[LiquidityZone]: 検出された流動性ゾーン
        """
        swing_points = self.swing_analyzer.get_swing_points(df)

        # 直近のスイングポイントに絞る
        recent_points = swing_points[-n_recent_swings * 2 :]

        highs = [p for p in recent_points if p.swing_type == SwingType.HIGH]
        lows = [p for p in recent_points if p.swing_type == SwingType.LOW]

        zones: list[LiquidityZone] = []

        # 買い側流動性（スイングハイ）
        buy_zones = self._cluster_levels(highs, LiquidityType.BUY_SIDE)
        zones.extend(buy_zones)

        # 売り側流動性（スイングロー）
        sell_zones = self._cluster_levels(lows, LiquidityType.SELL_SIDE)
        zones.extend(sell_zones)

        # 強度でフィルタリング
        zones = [z for z in zones if z.strength >= self.min_zone_strength]

        return zones

    def _cluster_levels(
        self,
        points: list[SwingPoint],
        liquidity_type: LiquidityType,
    ) -> list[LiquidityZone]:
        """近い価格レベルをクラスタリング

        Args:
            points: スイングポイントリスト
            liquidity_type: 流動性タイプ

        Returns:
            list[LiquidityZone]: クラスタリングされたゾーン
        """
        if not points:
            return []

        # 価格でソート
        sorted_points = sorted(points, key=lambda p: p.price)
        clusters: list[list[SwingPoint]] = []
        current_cluster: list[SwingPoint] = [sorted_points[0]]

        for point in sorted_points[1:]:
            # 前のポイントと近ければ同じクラスター
            if abs(point.price - current_cluster[-1].price) <= self._tolerance:
                current_cluster.append(point)
            else:
                clusters.append(current_cluster)
                current_cluster = [point]

        clusters.append(current_cluster)

        # クラスターをゾーンに変換
        zones: list[LiquidityZone] = []
        for cluster in clusters:
            avg_price = sum(p.price for p in cluster) / len(cluster)
            zones.append(
                LiquidityZone(
                    level=avg_price,
                    liquidity_type=liquidity_type,
                    strength=len(cluster),
                    bar_indices=[p.bar_index for p in cluster],
                    swept=False,
                )
            )

        return zones

    def detect_liquidity_grab(
        self,
        df: pd.DataFrame,
        lookback: int = 20,
    ) -> pd.DataFrame:
        """流動性グラブ（ストップハンティング）を検出（最適化版）

        流動性ゾーンを一時的に超えた後、反転するパターンを検出。
        ベクトル化されたアプローチで高速に計算。

        Args:
            df: OHLC DataFrame
            lookback: ゾーン検出のルックバック期間

        Returns:
            pd.DataFrame: 流動性グラブ指標を含むDataFrame
        """
        result = pd.DataFrame(index=df.index)
        result["liquidity_grab_bullish"] = False
        result["liquidity_grab_bearish"] = False
        result["buy_side_liquidity"] = np.nan
        result["sell_side_liquidity"] = np.nan

        # スイングポイントを一度だけ計算
        swing_high = self.swing_analyzer.detect_swing_highs(df)
        swing_low = self.swing_analyzer.detect_swing_lows(df)

        # ローリングウィンドウで直近の流動性レベルを計算
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        # 直近のスイングハイ/ローを追跡（ベクトル化）
        last_swing_high = pd.Series(high, index=df.index).where(
            swing_high
        ).ffill()
        last_swing_low = pd.Series(low, index=df.index).where(
            swing_low
        ).ffill()

        # 流動性レベルを設定
        result["buy_side_liquidity"] = last_swing_high
        result["sell_side_liquidity"] = last_swing_low

        # 流動性グラブ検出（ベクトル化）
        # 強気グラブ: 安値がスイングローを下回り、終値は上
        result["liquidity_grab_bullish"] = (
            (low < last_swing_low.values) &
            (close > last_swing_low.values)
        )

        # 弱気グラブ: 高値がスイングハイを上回り、終値は下
        result["liquidity_grab_bearish"] = (
            (high > last_swing_high.values) &
            (close < last_swing_high.values)
        )

        return result

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """全流動性指標を計算

        Args:
            df: OHLC DataFrame

        Returns:
            pd.DataFrame: 流動性指標を含むDataFrame
        """
        # 流動性グラブ検出
        result = self.detect_liquidity_grab(df)

        # 強度の高いゾーンをマーク
        zones = self.find_liquidity_zones(df)

        result["strong_buy_liquidity"] = np.nan
        result["strong_sell_liquidity"] = np.nan

        strong_buy = [
            z
            for z in zones
            if z.liquidity_type == LiquidityType.BUY_SIDE and z.strength >= 2
        ]
        strong_sell = [
            z
            for z in zones
            if z.liquidity_type == LiquidityType.SELL_SIDE and z.strength >= 2
        ]

        if strong_buy:
            result["strong_buy_liquidity"] = strong_buy[0].level
        if strong_sell:
            result["strong_sell_liquidity"] = strong_sell[0].level

        # 流動性グラブの発生回数
        result["liquidity_grab_bullish_count"] = (
            result["liquidity_grab_bullish"].cumsum()
        )
        result["liquidity_grab_bearish_count"] = (
            result["liquidity_grab_bearish"].cumsum()
        )

        return result

    def get_nearest_liquidity(
        self,
        df: pd.DataFrame,
        current_price: float,
        direction: int,
    ) -> float | None:
        """指定方向の最も近い流動性レベルを取得

        Args:
            df: OHLC DataFrame
            current_price: 現在価格
            direction: 1（買い）or -1（売り）

        Returns:
            float | None: 流動性レベル（見つからない場合None）
        """
        zones = self.find_liquidity_zones(df)

        if direction == 1:
            # 買いの場合、上にある流動性（TP候補）
            upper_zones = [
                z
                for z in zones
                if z.liquidity_type == LiquidityType.BUY_SIDE
                and z.level > current_price
            ]
            if upper_zones:
                return min(upper_zones, key=lambda z: z.level).level
        else:
            # 売りの場合、下にある流動性（TP候補）
            lower_zones = [
                z
                for z in zones
                if z.liquidity_type == LiquidityType.SELL_SIDE
                and z.level < current_price
            ]
            if lower_zones:
                return max(lower_zones, key=lambda z: z.level).level

        return None
