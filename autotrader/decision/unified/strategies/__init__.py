"""輻輳型戦略モジュール

型定義と基底クラスを提供する。
"""

from __future__ import annotations

from .base import BaseStrategy, StrategyConfig
from .in_strategy_consensus import (
    InStrategyConsensus,
    InStrategyConsensusConfig,
)
from .types import (
    EdgeScoreComponents,
    InStrategyConsensusResult,
    PoolEvaluationResult,
    ProposedTrade,
    SelectionResult,
    StrategyContext,
    StrategyId,
    StrategyTimeframes,
)

__all__ = [
    # 型定義
    "EdgeScoreComponents",
    "InStrategyConsensusResult",
    "PoolEvaluationResult",
    "ProposedTrade",
    "SelectionResult",
    "StrategyContext",
    "StrategyId",
    "StrategyTimeframes",
    # 基底クラス
    "BaseStrategy",
    "StrategyConfig",
    "InStrategyConsensus",
    "InStrategyConsensusConfig",
]
