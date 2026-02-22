"""確度計算

シグナル強度と各種要因から最終的な確度を計算。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from autotrader.decision.signal_generator import SignalStrength
from autotrader.core.enums import SignalType


@dataclass(frozen=True)
class ConfidenceResult:
    """確度計算結果

    Attributes:
        confidence: 確度（0-1）
        base_confidence: 基本確度
        adjustments: 調整内訳
        reasoning: 計算理由
    """

    confidence: float
    base_confidence: float
    adjustments: dict[str, float]
    reasoning: str


class ConfidenceCalculator:
    """確度計算クラス

    シグナル強度を基本確度に変換し、
    各種要因で調整して最終確度を算出。

    Args:
        base_confidence_weight: 基本確度の重み
        mtf_weight: MTF整合度の重み
        volatility_weight: ボラティリティの重み
        trend_weight: トレンド強度の重み
    """

    def __init__(
        self,
        base_confidence_weight: float = 0.5,
        mtf_weight: float = 0.2,
        volatility_weight: float = 0.15,
        trend_weight: float = 0.15,
    ) -> None:
        self.base_confidence_weight = base_confidence_weight
        self.mtf_weight = mtf_weight
        self.volatility_weight = volatility_weight
        self.trend_weight = trend_weight

    def _calculate_base_confidence(
        self,
        strength: SignalStrength,
        signal_type: SignalType,
    ) -> float:
        """基本確度を計算

        Args:
            strength: シグナル強度
            signal_type: シグナル種別

        Returns:
            float: 基本確度（0-1）
        """
        if signal_type == SignalType.BUY:
            base = strength.buy_strength
            # 反対方向の強度でペナルティ
            penalty = strength.sell_strength * 0.3
        elif signal_type == SignalType.SELL:
            base = strength.sell_strength
            penalty = strength.buy_strength * 0.3
        else:
            return 0.0

        return max(base - penalty, 0.0)

    def _calculate_mtf_adjustment(
        self,
        indicators: pd.Series,
        signal_type: SignalType,
    ) -> float:
        """MTF整合度調整を計算

        Args:
            indicators: 指標値
            signal_type: シグナル種別

        Returns:
            float: MTF調整値（-0.2から0.2）
        """
        mtf_alignment = indicators.get("mtf_alignment")
        higher_tf_bias = indicators.get("higher_tf_bias")

        if mtf_alignment is None or higher_tf_bias is None:
            return 0.0

        if pd.isna(higher_tf_bias):
            return 0.0

        # シグナル方向とMTFバイアスの一致度
        if signal_type == SignalType.BUY:
            if higher_tf_bias > 0.3:
                return 0.15  # 上位足も買い方向
            elif higher_tf_bias < -0.3:
                return -0.15  # 上位足は売り方向
        elif signal_type == SignalType.SELL:
            if higher_tf_bias < -0.3:
                return 0.15
            elif higher_tf_bias > 0.3:
                return -0.15

        # 整合状態による補正
        if mtf_alignment == "conflicting":
            return -0.1

        return 0.0

    def _calculate_volatility_adjustment(
        self, indicators: pd.Series
    ) -> float:
        """ボラティリティ調整を計算

        Args:
            indicators: 指標値

        Returns:
            float: ボラティリティ調整値（-0.15から0.15）
        """
        vol_regime = indicators.get("volatility_regime")

        if vol_regime is None:
            return 0.0

        # 極端なボラティリティはマイナス調整
        if vol_regime in ("very_low", "very_high"):
            return -0.1
        elif vol_regime == "normal":
            return 0.05
        elif vol_regime == "low":
            return -0.05  # 動きが少ない
        elif vol_regime == "high":
            return 0.0  # 適度な動き

        return 0.0

    def _calculate_trend_adjustment(
        self,
        indicators: pd.Series,
        signal_type: SignalType,
    ) -> float:
        """トレンド強度調整を計算

        Args:
            indicators: 指標値
            signal_type: シグナル種別

        Returns:
            float: トレンド調整値（-0.15から0.15）
        """
        trend_strength = indicators.get("trend_strength")
        trend_direction = indicators.get("trend_direction")

        if trend_strength is None or pd.isna(trend_strength):
            return 0.0

        # トレンド方向とシグナル方向の一致
        direction_match = False
        if signal_type == SignalType.BUY:
            direction_match = trend_direction in ("strong_up", "up")
        elif signal_type == SignalType.SELL:
            direction_match = trend_direction in ("strong_down", "down")

        if direction_match:
            # トレンド方向一致でボーナス
            return trend_strength * 0.15
        else:
            # 逆張りでペナルティ
            return -trend_strength * 0.1

    def calculate(
        self,
        strength: SignalStrength,
        signal_type: SignalType,
        indicators: pd.Series,
    ) -> ConfidenceResult:
        """確度を計算

        Args:
            strength: シグナル強度
            signal_type: シグナル種別
            indicators: 指標値

        Returns:
            ConfidenceResult: 確度計算結果
        """
        if signal_type == SignalType.HOLD:
            return ConfidenceResult(
                confidence=0.0,
                base_confidence=0.0,
                adjustments={},
                reasoning="HOLDシグナル",
            )

        # 基本確度
        base = self._calculate_base_confidence(strength, signal_type)

        # 各種調整
        adjustments: dict[str, float] = {}

        mtf_adj = self._calculate_mtf_adjustment(indicators, signal_type)
        adjustments["mtf"] = mtf_adj

        vol_adj = self._calculate_volatility_adjustment(indicators)
        adjustments["volatility"] = vol_adj

        trend_adj = self._calculate_trend_adjustment(indicators, signal_type)
        adjustments["trend"] = trend_adj

        # 最終確度計算
        total_adjustment = sum(adjustments.values())
        confidence = base + total_adjustment

        # 0-1にクリップ
        confidence = max(0.0, min(1.0, confidence))

        # 理由生成
        reasons = [f"基本確度: {base:.2f}"]
        for name, adj in adjustments.items():
            if adj != 0:
                sign = "+" if adj > 0 else ""
                reasons.append(f"{name}: {sign}{adj:.2f}")
        reasons.append(f"最終確度: {confidence:.2f}")

        return ConfidenceResult(
            confidence=confidence,
            base_confidence=base,
            adjustments=adjustments,
            reasoning="; ".join(reasons),
        )
