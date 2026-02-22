"""統合判定エンジン

シグナル生成、確度計算、制約チェックを統合して最終判断を行う。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from autotrader.core.enums import SignalType
from autotrader.decision.signal_generator import SignalGenerator, DirectionPolicy
from autotrader.decision.confidence_calculator import ConfidenceCalculator
from autotrader.decision.exit_manager import ExitManager


@dataclass(frozen=True)
class DecisionOutput:
    """判定出力

    Attributes:
        signal_type: シグナル種別
        confidence: 確度
        should_trade: トレードすべきか
        target_price: 目標価格
        stop_loss_price: 損切価格
        reasoning: 判断理由
        indicators_snapshot: 指標スナップショット
        timestamp: 判断時刻
    """

    signal_type: SignalType
    confidence: float
    should_trade: bool
    target_price: float | None = None
    stop_loss_price: float | None = None
    reasoning: str = ""
    indicators_snapshot: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


class DecisionEngine:
    """統合判定エンジン

    テクニカル指標と制約チェック結果から最終的な取引判断を行う。

    Args:
        signal_generator: シグナル生成器
        confidence_calculator: 確度計算器
        exit_manager: 決済管理器
        min_confidence: 最小確度閾値
        atr_sl_multiplier: ATRベースSL倍率
        atr_tp_multiplier: ATRベースTP倍率
    """

    def __init__(
        self,
        signal_generator: SignalGenerator | None = None,
        confidence_calculator: ConfidenceCalculator | None = None,
        exit_manager: ExitManager | None = None,
        min_confidence: float = 0.50,
        atr_sl_multiplier: float = 1.5,
        atr_tp_multiplier: float = 2.0,
    ) -> None:
        self.signal_generator = signal_generator or SignalGenerator()
        self.confidence_calculator = (
            confidence_calculator or ConfidenceCalculator()
        )
        self.exit_manager = exit_manager or ExitManager()
        self.min_confidence = min_confidence
        self.atr_sl_multiplier = atr_sl_multiplier
        self.atr_tp_multiplier = atr_tp_multiplier

    def _calculate_sl_tp(
        self,
        signal_type: SignalType,
        current_price: float,
        atr: float,
    ) -> tuple[float, float]:
        """SL/TPを計算

        Args:
            signal_type: シグナル種別
            current_price: 現在価格
            atr: ATR値

        Returns:
            tuple[float, float]: (SL価格, TP価格)
        """
        sl_distance = atr * self.atr_sl_multiplier
        tp_distance = atr * self.atr_tp_multiplier

        if signal_type == SignalType.BUY:
            stop_loss = current_price - sl_distance
            take_profit = current_price + tp_distance
        elif signal_type == SignalType.SELL:
            stop_loss = current_price + sl_distance
            take_profit = current_price - tp_distance
        else:
            stop_loss = current_price
            take_profit = current_price

        return stop_loss, take_profit

    def _create_indicators_snapshot(
        self, indicators: pd.Series
    ) -> dict[str, Any]:
        """指標スナップショットを作成

        Args:
            indicators: 指標値

        Returns:
            dict[str, Any]: スナップショット
        """
        keys = [
            "rsi_14",
            "macd",
            "macd_signal",
            "macd_histogram",
            "stoch_k",
            "stoch_d",
            "adx_14",
            "ma_alignment",
            "trend_direction",
            "trend_strength",
            "bb_percent_b",
            "atr_14",
            "volatility_regime",
        ]

        snapshot = {}
        for key in keys:
            value = indicators.get(key)
            if value is not None and not pd.isna(value):
                if hasattr(value, "value"):  # Enum
                    snapshot[key] = value.value
                else:
                    snapshot[key] = float(value) if isinstance(value, (int, float)) else str(value)

        return snapshot

    def decide_entry(
        self,
        indicators: pd.Series,
        current_price: float,
        constraint_result: dict | None = None,
    ) -> DecisionOutput:
        """エントリー判断

        Args:
            indicators: 指標値
            current_price: 現在価格
            constraint_result: 制約チェック結果

        Returns:
            DecisionOutput: 判断結果
        """
        # シグナル生成
        signal_result = self.signal_generator.generate(indicators)

        # HOLDならそのまま返す
        if signal_result.signal_type == SignalType.HOLD:
            return DecisionOutput(
                signal_type=SignalType.HOLD,
                confidence=0.0,
                should_trade=False,
                reasoning=signal_result.reasoning,
                indicators_snapshot=self._create_indicators_snapshot(indicators),
            )

        # 確度計算
        confidence_result = self.confidence_calculator.calculate(
            signal_result.strength,
            signal_result.signal_type,
            indicators,
        )

        # 制約ペナルティ適用
        final_confidence = confidence_result.confidence
        if constraint_result is not None:
            penalty = constraint_result.get("total_penalty", 0.0)
            final_confidence = final_confidence * (1 - penalty)

            # 制約でブロック
            if not constraint_result.get("is_allowed", True):
                return DecisionOutput(
                    signal_type=signal_result.signal_type,
                    confidence=0.0,
                    should_trade=False,
                    reasoning=f"制約違反: {constraint_result.get('reasons', [])}",
                    indicators_snapshot=self._create_indicators_snapshot(indicators),
                )

        # 確度閾値チェック
        should_trade = final_confidence >= self.min_confidence

        # SL/TP計算
        atr = indicators.get("atr_14")
        if atr is None:
            atr = indicators.get("atr")
        if atr is None or pd.isna(atr):
            atr = current_price * 0.001  # デフォルト0.1%

        stop_loss, take_profit = self._calculate_sl_tp(
            signal_result.signal_type, current_price, atr
        )

        # 理由まとめ
        reasons = [
            signal_result.reasoning,
            confidence_result.reasoning,
        ]
        if constraint_result is not None:
            reasons.append(f"制約ペナルティ: {constraint_result.get('total_penalty', 0):.2f}")

        return DecisionOutput(
            signal_type=signal_result.signal_type,
            confidence=final_confidence,
            should_trade=should_trade,
            target_price=take_profit,
            stop_loss_price=stop_loss,
            reasoning="; ".join(reasons),
            indicators_snapshot=self._create_indicators_snapshot(indicators),
        )

    def decide_exit(
        self,
        position: dict,
        indicators: pd.Series,
        current_price: float,
        current_time: datetime,
    ) -> DecisionOutput:
        """決済判断

        Args:
            position: ポジション情報
            indicators: 指標値
            current_price: 現在価格
            current_time: 現在時刻

        Returns:
            DecisionOutput: 判断結果
        """
        exit_decision = self.exit_manager.should_exit(
            position, current_price, current_time, indicators
        )

        if exit_decision.should_exit:
            return DecisionOutput(
                signal_type=SignalType.HOLD,  # 決済はHOLDで表現
                confidence=1.0,
                should_trade=True,  # 決済実行
                target_price=exit_decision.exit_price,
                reasoning=exit_decision.reasoning,
                indicators_snapshot=self._create_indicators_snapshot(indicators),
            )

        # トレーリングストップ更新チェック
        atr = indicators.get("atr_14")
        if atr is None:
            atr = indicators.get("atr")
        if atr is not None and not pd.isna(atr):
            trailing_result = self.exit_manager.calculate_trailing_stop(
                position, current_price, atr
            )

            if trailing_result.should_update:
                return DecisionOutput(
                    signal_type=position.get("signal_type", SignalType.HOLD),
                    confidence=0.5,
                    should_trade=False,  # 決済ではない
                    stop_loss_price=trailing_result.new_stop_loss,
                    reasoning=trailing_result.reasoning,
                    indicators_snapshot=self._create_indicators_snapshot(indicators),
                )

        return DecisionOutput(
            signal_type=position.get("signal_type", SignalType.HOLD),
            confidence=0.0,
            should_trade=False,
            reasoning="保有継続",
            indicators_snapshot=self._create_indicators_snapshot(indicators),
        )
