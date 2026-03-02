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
from .mode_aware_consensus import (
    ConsensusConfig,
    ConsensusResult,
    ModeAwareScoreConsensus,
)
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
from .multi_mode_controller import (
    MultiModeConfig,
    MultiModeController,
    MultiModeSignal,
)
from .position_aggregator import (
    AggregatorConfig,
    AggregatorState,
    ModePosition,
    PositionAggregator,
)
from .position_manager import (
    ManagedPosition,
    ManagementAction,
    ManagementActionType,
    PositionManager,
    PositionManagerConfig,
)
from .position_sizer import (
    PositionSizer,
    PositionSizerConfig,
)
from .signal_consolidator import (
    ConsolidatedSignal,
    PortfolioState,
    SignalConsolidator,
)
from .strategies import (
    BaseStrategy,
    EdgeScoreComponents,
    InStrategyConsensus,
    InStrategyConsensusConfig,
    InStrategyConsensusResult,
    PoolEvaluationResult,
    ProposedTrade,
    ScalpStrategy,
    SelectionResult,
    ShortMidStrategy,
    StrategyConfig,
    StrategyContext,
    StrategyId,
    StrategyTimeframes,
    SwingStrategy,
    get_registered_strategies,
    get_registry,
    get_strategy_class,
    register_strategy,
)
from .strategy_pool import StrategyPool
from .strategy_selector import SelectorConfig, StrategySelector
from .strength_calculator import IndicatorStrength, IndicatorStrengthCalculator
from .timeframe_evaluator import TimeframeEvaluator, TimeframeSignal
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
    # コンセンサス
    "ConsensusConfig",
    "ConsensusResult",
    "ModeAwareScoreConsensus",
    # ポジションサイジング
    "PositionSizer",
    "PositionSizerConfig",
    # ポジション管理
    "ManagedPosition",
    "ManagementAction",
    "ManagementActionType",
    "PositionManager",
    "PositionManagerConfig",
    # シグナル統合（レガシー）
    "ConsolidatedSignal",
    "PortfolioState",
    "SignalConsolidator",
    # 指標強度
    "IndicatorStrength",
    "IndicatorStrengthCalculator",
    # 時間足評価
    "TimeframeEvaluator",
    "TimeframeSignal",
    # メインボット
    "BotState",
    "RiskManager",
    "UnifiedTradeBot",
    # マルチモード（新規）
    "ModeConfig",
    "ModeMonitor",
    "ModeSignal",
    "UNIVERSAL_CONFIG",
    "MultiModeConfig",
    "MultiModeController",
    "MultiModeSignal",
    "AggregatorConfig",
    "AggregatorState",
    "ModePosition",
    "PositionAggregator",
    # 輻輳型アーキテクチャ
    "BaseStrategy",
    "EdgeScoreComponents",
    "InStrategyConsensus",
    "InStrategyConsensusConfig",
    "InStrategyConsensusResult",
    "PoolEvaluationResult",
    "ProposedTrade",
    "ScalpStrategy",
    "SelectionResult",
    "ShortMidStrategy",
    "StrategyConfig",
    "StrategyContext",
    "StrategyId",
    "StrategyTimeframes",
    "SwingStrategy",
    "register_strategy",
    "get_registered_strategies",
    "get_strategy_class",
    "get_registry",
    "StrategyPool",
    "SelectorConfig",
    "StrategySelector",
    # 動的TF選択（UNIVERSAL）
    "DynamicTFSelector",
    "DynamicTFResult",
]
