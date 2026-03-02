"""判定機インターフェース

判断タイプ・判断結果のデータ型。
未使用の SignalGeneratorInterface / ExitManagerInterface /
DecisionEngineInterface は削除済み。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
