"""インターフェース定義モジュール"""

from __future__ import annotations

from autotrader.core.interfaces.calculator import (
    IndicatorCalculator,
    FeatureCalculator,
    PrecomputeEngineInterface,
)
from autotrader.core.interfaces.constraint import (
    ConstraintAction,
    ConstraintCheckResult,
    ConstraintCheckerInterface,
    Guard,
)
from autotrader.core.interfaces.decision import (
    DecisionType,
    DecisionResult,
    SignalGeneratorInterface,
    ExitManagerInterface,
    DecisionEngineInterface,
)
from autotrader.core.interfaces.data_provider import DataProvider
from autotrader.core.interfaces.trade_executor import TradeExecutor
from autotrader.core.interfaces.position_sizing import (
    SizingContext,
    SizingResult,
    PositionSizerProtocol,
)

__all__ = [
    # Calculator
    "IndicatorCalculator",
    "FeatureCalculator",
    "PrecomputeEngineInterface",
    # Constraint
    "ConstraintAction",
    "ConstraintCheckResult",
    "ConstraintCheckerInterface",
    "Guard",
    # Decision
    "DecisionType",
    "DecisionResult",
    "SignalGeneratorInterface",
    "ExitManagerInterface",
    "DecisionEngineInterface",
    # Data
    "DataProvider",
    # Trade
    "TradeExecutor",
    # Position Sizing
    "SizingContext",
    "SizingResult",
    "PositionSizerProtocol",
]
