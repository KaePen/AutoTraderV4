"""輻輳型戦略モジュール

複数の保有期間戦略を並列評価し、edge_scoreで最適戦略を選択する。
"""

from __future__ import annotations

from .base import BaseStrategy, StrategyConfig
from .in_strategy_consensus import (
    InStrategyConsensus,
    InStrategyConsensusConfig,
)

# 具体戦略のimportでデコレータが実行され、レジストリに登録される
from .no_trade import NoTradeStrategy
from .registry import (
    get_registered_strategies,
    get_registry,
    get_strategy_class,
    register_strategy,
)
from .scalp import ScalpStrategy
from .short_mid import ShortMidStrategy
from .swing import SwingStrategy
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
    # レジストリ
    "register_strategy",
    "get_registered_strategies",
    "get_strategy_class",
    "get_registry",
    # 具体戦略
    "ScalpStrategy",
    "ShortMidStrategy",
    "SwingStrategy",
    "NoTradeStrategy",
]
