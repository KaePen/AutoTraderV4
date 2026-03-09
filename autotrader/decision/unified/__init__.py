"""統合トレードボットモジュール"""

from __future__ import annotations

from .config import (
    ConsolidatorConfig,
    EvaluatorConfig,
    FilterConfig,
    RiskConfig,
    RiskManagementConfig,
    SignalConfig,
    StrengthConfig,
    UnifiedBotConfig,
)
from .dynamic_tf_selector import DynamicTFResult, DynamicTFSelector
from .mode_monitor import (
    UNIVERSAL_CONFIG,
    ModeConfig,
    ModeMonitor,
    ModeSignal,
)
from .mode_selector import (
    ModeSelectorConfig,
    TradingModeSelector,
    TradingPlan,
)
from .pipeline_pkg import (
    DirectionalEdgeAssessor,
    DirectionalEdgeResult,
    EntryConfig,
    EntryDecision,
    EntryTimeframeResolver,
    SignalPipeline,
)
from .position_aggregator import (
    AggregatorConfig,
    AggregatorState,
    ModePosition,
    PositionAggregator,
)
from .risk import (
    ManagedPosition,
    ManagementAction,
    ManagementActionType,
    PositionManager,
    PositionManagerConfig,
    PositionSizer,
    PositionSizerConfig,
)
from .scoring import (
    ConsolidatedSignal,
    ConsensusConfig,
    ConsensusResult,
    IndicatorStrength,
    IndicatorStrengthCalculator,
    ModeAwareScoreConsensus,
    PortfolioState,
    SignalConsolidator,
    TimeframeEvaluator,
    TimeframeSignal,
)
from .strategies import (
    BaseStrategy,
    EdgeScoreComponents,
    InStrategyConsensus,
    InStrategyConsensusConfig,
    InStrategyConsensusResult,
    PoolEvaluationResult,
    ProposedTrade,
    SelectionResult,
    StrategyConfig,
    StrategyContext,
    StrategyId,
    StrategyTimeframes,
)
from .timeframe_router import (
    TimeframeRole,
    TimeframeRouter,
    TimeframeSet,
)
from .trade_bot import BotState, RiskManager, UnifiedTradeBot

__all__ = [
    # 設定
    "ConsolidatorConfig",
    "EvaluatorConfig",
    "FilterConfig",
    "RiskConfig",
    "RiskManagementConfig",
    "SignalConfig",
    "StrengthConfig",
    "UnifiedBotConfig",
    # モード選択
    "ModeSelectorConfig",
    "TradingModeSelector",
    "TradingPlan",
    # タイムフレームルーター
    "TimeframeRole",
    "TimeframeRouter",
    "TimeframeSet",
    # コンセンサス（scoring/）
    "ConsensusConfig",
    "ConsensusResult",
    "ModeAwareScoreConsensus",
    # ポジションサイジング（risk/）
    "PositionSizer",
    "PositionSizerConfig",
    # ポジション管理（risk/）
    "ManagedPosition",
    "ManagementAction",
    "ManagementActionType",
    "PositionManager",
    "PositionManagerConfig",
    # シグナル統合（scoring/）
    "ConsolidatedSignal",
    "PortfolioState",
    "SignalConsolidator",
    # 指標強度（scoring/）
    "IndicatorStrength",
    "IndicatorStrengthCalculator",
    # 時間足評価（scoring/）
    "TimeframeEvaluator",
    "TimeframeSignal",
    # メインボット
    "BotState",
    "RiskManager",
    "UnifiedTradeBot",
    # モード監視
    "ModeConfig",
    "ModeMonitor",
    "ModeSignal",
    "UNIVERSAL_CONFIG",
    "AggregatorConfig",
    "AggregatorState",
    "ModePosition",
    "PositionAggregator",
    # 輻輳型アーキテクチャ（strategies/）
    "BaseStrategy",
    "EdgeScoreComponents",
    "InStrategyConsensus",
    "InStrategyConsensusConfig",
    "InStrategyConsensusResult",
    "PoolEvaluationResult",
    "ProposedTrade",
    "SelectionResult",
    "StrategyConfig",
    "StrategyContext",
    "StrategyId",
    "StrategyTimeframes",
    # 動的TF選択（UNIVERSAL）
    "DynamicTFSelector",
    "DynamicTFResult",
    # パイプライン（pipeline_pkg/）
    "DirectionalEdgeAssessor",
    "DirectionalEdgeResult",
    "EntryConfig",
    "EntryDecision",
    "EntryTimeframeResolver",
    "SignalPipeline",
]
