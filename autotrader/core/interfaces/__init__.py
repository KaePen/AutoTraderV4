"""インターフェース定義モジュール"""

from __future__ import annotations

from autotrader.core.interfaces.calculator import (
    PrecomputeEngineProtocol,
)
from autotrader.core.interfaces.constraint import (
    ConstraintAction,
    ConstraintCheckResult,
)
from autotrader.core.interfaces.decision import (
    DecisionType,
    DecisionResult,
)
from autotrader.core.interfaces.data_provider import DataProvider
from autotrader.core.interfaces.position_sizing import (
    PositionSizerProtocol,
    SizingContext,
    SizingResult,
)
from autotrader.core.interfaces.trade_executor import TradeExecutor

__all__ = [
    # Calculator
    "PrecomputeEngineProtocol",
    # Constraint
    "ConstraintAction",
    "ConstraintCheckResult",
    # Decision
    "DecisionType",
    "DecisionResult",
    # Data
    "DataProvider",
    # Trade
    "TradeExecutor",
    # Position Sizing
    "SizingContext",
    "SizingResult",
    "PositionSizerProtocol",
]
