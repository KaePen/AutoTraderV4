"""マルチタイムフレーム特徴量

複数時間足間の整合性、乖離、トレンド一致度などの特徴量を計算。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from autotrader.calculator.features.trend_features import (
    TrendDirection,
    TrendFeatures,
)
from autotrader.config.tf_params_registry import get_mtf_weight
from autotrader.core.enums import Timeframe


class MTFAlignment(Enum):
    """MTF整合状態"""

    ALIGNED_UP = "aligned_up"
    ALIGNED_DOWN = "aligned_down"
    MIXED = "mixed"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class TimeframeData:
    """時間足データ

    Attributes:
        timeframe: 時間足
        high: 高値系列
        low: 安値系列
        close: 終値系列
    """

    timeframe: Timeframe
    high: pd.Series
    low: pd.Series
    close: pd.Series


@dataclass(frozen=True)
class MTFFeatureResult:
    """MTF特徴量結果

    Attributes:
        alignment: MTF整合状態
        alignment_score: 整合度スコア（0-1）
        trend_consistency: トレンド一致度（0-1）
        higher_tf_bias: 上位足バイアス（正=買い、負=売り）
        divergence_score: 乖離スコア
    """

    alignment: MTFAlignment
    alignment_score: float
    trend_consistency: float
    higher_tf_bias: float
    divergence_score: float


class MTFFeatures:
    """MTF特徴量計算クラス

    Args:
        trend_features: トレンド特徴量計算インスタンス
    """

    @staticmethod
    def _build_tf_weights() -> dict[Timeframe, float]:
        """レジストリから全TF重みを取得"""
        return {tf: get_mtf_weight(tf.value) for tf in Timeframe}

    TIMEFRAME_WEIGHTS: dict[Timeframe, float] = {
        tf: get_mtf_weight(tf.value) for tf in Timeframe
    }

    def __init__(
        self,
        trend_features: TrendFeatures | None = None,
    ) -> None:
        self.trend_features = trend_features or TrendFeatures()

    def _direction_to_score(self, direction: TrendDirection) -> float:
        """トレンド方向をスコアに変換

        Args:
            direction: トレンド方向

        Returns:
            float: スコア（-1から1）
        """
        direction_scores = {
            TrendDirection.STRONG_UP: 1.0,
            TrendDirection.UP: 0.5,
            TrendDirection.NEUTRAL: 0.0,
            TrendDirection.DOWN: -0.5,
            TrendDirection.STRONG_DOWN: -1.0,
        }
        return direction_scores.get(direction, 0.0)

    def calculate_alignment_score(
        self, tf_data_list: list[TimeframeData]
    ) -> float:
        """MTF整合度スコアを計算

        Args:
            tf_data_list: 時間足データリスト

        Returns:
            float: 整合度スコア（0-1）
        """
        if not tf_data_list:
            return 0.0

        direction_scores: list[tuple[float, float]] = []

        for tf_data in tf_data_list:
            result = self.trend_features.get_trend_feature_result(
                tf_data.high, tf_data.low, tf_data.close
            )
            weight = self.TIMEFRAME_WEIGHTS.get(tf_data.timeframe, 0.5)
            score = self._direction_to_score(result.direction)
            direction_scores.append((score, weight))

        if not direction_scores:
            return 0.0

        total_weight = sum(w for _, w in direction_scores)
        weighted_sum = sum(s * w for s, w in direction_scores)
        weighted_avg = weighted_sum / total_weight if total_weight > 0 else 0

        return abs(weighted_avg)

    def calculate_trend_consistency(
        self, tf_data_list: list[TimeframeData]
    ) -> float:
        """トレンド一致度を計算

        Args:
            tf_data_list: 時間足データリスト

        Returns:
            float: 一致度（0-1）
        """
        if len(tf_data_list) < 2:
            return 1.0

        directions: list[int] = []

        for tf_data in tf_data_list:
            result = self.trend_features.get_trend_feature_result(
                tf_data.high, tf_data.low, tf_data.close
            )
            score = self._direction_to_score(result.direction)
            directions.append(1 if score > 0 else (-1 if score < 0 else 0))

        up_count = sum(1 for d in directions if d > 0)
        down_count = sum(1 for d in directions if d < 0)
        total = len(directions)

        max_count = max(up_count, down_count)
        return max_count / total if total > 0 else 0.0

    def calculate_higher_tf_bias(
        self, tf_data_list: list[TimeframeData]
    ) -> float:
        """上位足バイアスを計算

        Args:
            tf_data_list: 時間足データリスト

        Returns:
            float: バイアス（-1から1）
        """
        higher_tf = [Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1]

        bias_scores: list[tuple[float, float]] = []

        for tf_data in tf_data_list:
            if tf_data.timeframe not in higher_tf:
                continue

            result = self.trend_features.get_trend_feature_result(
                tf_data.high, tf_data.low, tf_data.close
            )
            weight = self.TIMEFRAME_WEIGHTS.get(tf_data.timeframe, 0.5)
            score = self._direction_to_score(result.direction)
            bias_scores.append((score, weight))

        if not bias_scores:
            return 0.0

        total_weight = sum(w for _, w in bias_scores)
        weighted_sum = sum(s * w for s, w in bias_scores)

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def calculate_divergence_score(
        self, tf_data_list: list[TimeframeData]
    ) -> float:
        """時間足間乖離スコアを計算

        Args:
            tf_data_list: 時間足データリスト

        Returns:
            float: 乖離スコア
        """
        if len(tf_data_list) < 2:
            return 0.0

        sorted_data = sorted(
            tf_data_list,
            key=lambda x: x.timeframe.minutes(),
        )

        divergence_sum = 0.0

        for i in range(len(sorted_data) - 1):
            current = self.trend_features.get_trend_feature_result(
                sorted_data[i].high,
                sorted_data[i].low,
                sorted_data[i].close,
            )
            next_tf = self.trend_features.get_trend_feature_result(
                sorted_data[i + 1].high,
                sorted_data[i + 1].low,
                sorted_data[i + 1].close,
            )

            divergence_sum += abs(
                current.deviation_score - next_tf.deviation_score
            )

        return divergence_sum

    def determine_alignment(
        self, tf_data_list: list[TimeframeData]
    ) -> MTFAlignment:
        """MTF整合状態を判定

        Args:
            tf_data_list: 時間足データリスト

        Returns:
            MTFAlignment: 整合状態
        """
        alignment_score = self.calculate_alignment_score(tf_data_list)
        higher_tf_bias = self.calculate_higher_tf_bias(tf_data_list)

        if alignment_score > 0.7:
            if higher_tf_bias > 0.3:
                return MTFAlignment.ALIGNED_UP
            elif higher_tf_bias < -0.3:
                return MTFAlignment.ALIGNED_DOWN
            else:
                return MTFAlignment.MIXED
        elif alignment_score < 0.3:
            return MTFAlignment.CONFLICTING
        else:
            return MTFAlignment.MIXED

    def get_mtf_feature_result(
        self, tf_data_list: list[TimeframeData]
    ) -> MTFFeatureResult:
        """MTF特徴量結果を取得

        Args:
            tf_data_list: 時間足データリスト

        Returns:
            MTFFeatureResult: MTF特徴量結果
        """
        return MTFFeatureResult(
            alignment=self.determine_alignment(tf_data_list),
            alignment_score=self.calculate_alignment_score(tf_data_list),
            trend_consistency=self.calculate_trend_consistency(tf_data_list),
            higher_tf_bias=self.calculate_higher_tf_bias(tf_data_list),
            divergence_score=self.calculate_divergence_score(tf_data_list),
        )
