"""判定機インターフェース

最終意思決定の抽象インターフェース。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from autotrader.core.enums import SignalType


class DecisionType(Enum):
    """判断タイプ"""

    ENTRY_BUY = "entry_buy"
    ENTRY_SELL = "entry_sell"
    EXIT = "exit"
    HOLD = "hold"


@dataclass(frozen=True)
class DecisionResult:
    """判断結果

    Attributes:
        decision_type: 判断タイプ
        confidence: 確度（0-1）
        signal_type: シグナルタイプ
        target_price: 目標価格
        stop_loss_price: 損切価格
        reasoning: 判断理由
        indicators_snapshot: 指標スナップショット
        timestamp: 判断時刻
    """

    decision_type: DecisionType
    confidence: float
    signal_type: "SignalType | None" = None
    target_price: float | None = None
    stop_loss_price: float | None = None
    reasoning: str = ""
    indicators_snapshot: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def is_entry(self) -> bool:
        """エントリー判断か

        Returns:
            bool: エントリーならTrue
        """
        return self.decision_type in (
            DecisionType.ENTRY_BUY,
            DecisionType.ENTRY_SELL,
        )

    def is_exit(self) -> bool:
        """決済判断か

        Returns:
            bool: 決済ならTrue
        """
        return self.decision_type == DecisionType.EXIT


class SignalGeneratorInterface(ABC):
    """シグナル生成インターフェース"""

    @abstractmethod
    def generate(
        self,
        indicators: "pd.DataFrame",
        features: "pd.DataFrame",
    ) -> DecisionResult:
        """シグナルを生成

        Args:
            indicators: テクニカル指標
            features: 特徴量

        Returns:
            DecisionResult: 判断結果
        """
        ...


class ExitManagerInterface(ABC):
    """決済管理インターフェース"""

    @abstractmethod
    def should_exit(
        self,
        position: dict,
        current_price: float,
        indicators: "pd.DataFrame",
    ) -> tuple[bool, str]:
        """決済すべきか判断

        Args:
            position: ポジション情報
            current_price: 現在価格
            indicators: テクニカル指標

        Returns:
            tuple[bool, str]: (決済すべきか, 理由)
        """
        ...


class DecisionEngineInterface(ABC):
    """判定機インターフェース"""

    @abstractmethod
    def decide(
        self,
        indicators: "pd.DataFrame",
        features: "pd.DataFrame",
        constraint_result: dict,
        position: dict | None = None,
    ) -> DecisionResult:
        """判断を実行

        Args:
            indicators: テクニカル指標
            features: 特徴量
            constraint_result: 制約チェック結果
            position: 現在のポジション

        Returns:
            DecisionResult: 判断結果
        """
        ...
