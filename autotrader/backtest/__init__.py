"""バックテストモジュール"""

from __future__ import annotations

from autotrader.backtest.config import (
    UnifiedBacktestConfig,
    UnifiedBacktestResult,
    BacktestMetrics as UnifiedBacktestMetrics,
    MonthlyResult,
    YearlyResult,
    ParallelBacktestConfig,
)
from autotrader.backtest.data_loader import DataLoader
from autotrader.backtest.indicators import (
    IndicatorCalculator,
    MultiTimeframeDataLoader,
)
from autotrader.backtest.formatters import (
    ResultFormatter,
    CLIFormatter,
    JSONFormatter,
    CompactFormatter,
)
from autotrader.backtest.simulator import TradeSimulator
from autotrader.backtest.metrics import MetricsCalculator
from autotrader.backtest.engine import (
    BacktestEngine,
    SignalGeneratorProtocol,
    LegacyGeneratorAdapter,
    UnifiedBotAdapter,
    UnifiedEngineConfig,
    UnifiedEngineResult,
    UnifiedBacktestEngine,
    ParallelEngineConfig,
    ParallelMultiTFBacktestEngine,
)
from autotrader.backtest.walk_forward import (
    WalkForwardValidator,
    WalkForwardConfig,
    WalkForwardResult,
    WalkForwardPeriod,
    PeriodMetrics,
    OverfittingWarning,
    create_walk_forward_periods,
)
from autotrader.backtest.strategy_factory import (
    StrategyFactory,
    StrategyInfo,
)
from autotrader.backtest.runner import (
    BacktestRunner,
    BacktestConfig,
    BacktestResult,
)
from autotrader.backtest.events import (
    BacktestEventEmitter,
    ConsoleEventListener,
    EventListener,
    EventType,
    BacktestEvent,
    ProgressEvent,
    SignalEvent,
    TradeEvent,
    MetricsEvent,
    CandleEvent,
    TimelineEventQueue,
)
from autotrader.backtest.file_listener import FileEventListener
from autotrader.backtest.service import (
    BacktestService,
    BacktestServiceConfig,
    create_bot_config,
    create_backtest_config,
    run_backtest,
)
from autotrader.backtest.state import (
    BacktestStatus,
    BacktestState,
    BacktestStateManager,
    get_state_manager,
)
from autotrader.backtest.executor import (
    BacktestExecutor,
    ExecutorConfig,
    ExecutorResult,
    run_backtest as run_backtest_executor,
)
from autotrader.backtest.parallel import (
    ParallelYearExecutor,
    ParallelDataLoader,
    ParallelStrategyComparator,
    ParallelSignalEvaluator,
    EvaluationResult,
    EvaluatorParams,
    evaluate_timeframe_signal,
)
from autotrader.backtest.fast_backtest import (
    FastBacktestConfig,
    FastBacktestEngine,
    FastBacktestResult,
    ChunkResult,
)
from autotrader.backtest.adapters import CLIAdapter, WebUIAdapter
from autotrader.backtest.diagnostics import (
    BacktestDiagnostics,
    SignalDebugger,
    run_diagnostics,
    run_debug_signal,
)

__all__ = [
    # 統一設定クラス（新規）
    "UnifiedBacktestConfig",
    "UnifiedBacktestResult",
    "UnifiedBacktestMetrics",
    "MonthlyResult",
    "YearlyResult",
    "ParallelBacktestConfig",
    # データ処理
    "DataLoader",
    "IndicatorCalculator",
    "MultiTimeframeDataLoader",
    # フォーマッタ
    "ResultFormatter",
    "CLIFormatter",
    "JSONFormatter",
    "CompactFormatter",
    "TradeSimulator",
    "MetricsCalculator",
    "BacktestEngine",
    "SignalGeneratorProtocol",
    "LegacyGeneratorAdapter",
    "UnifiedBotAdapter",
    "UnifiedEngineConfig",
    "UnifiedEngineResult",
    "UnifiedBacktestEngine",
    "ParallelEngineConfig",
    "ParallelMultiTFBacktestEngine",
    # ウォークフォワード
    "WalkForwardValidator",
    "WalkForwardConfig",
    "WalkForwardResult",
    "WalkForwardPeriod",
    "PeriodMetrics",
    "OverfittingWarning",
    "create_walk_forward_periods",
    # 戦略
    "StrategyFactory",
    "StrategyInfo",
    # ランナー（レガシー互換）
    "BacktestRunner",
    "BacktestConfig",
    "BacktestResult",
    # イベント
    "BacktestEventEmitter",
    "ConsoleEventListener",
    "EventListener",
    "EventType",
    "BacktestEvent",
    "ProgressEvent",
    "SignalEvent",
    "TradeEvent",
    "MetricsEvent",
    "CandleEvent",
    "TimelineEventQueue",
    "FileEventListener",
    # サービス
    "BacktestService",
    "BacktestServiceConfig",
    "create_bot_config",
    "create_backtest_config",
    "run_backtest",
    # 状態管理
    "BacktestStatus",
    "BacktestState",
    "BacktestStateManager",
    "get_state_manager",
    # 統一実行エンジン（新規）
    "BacktestExecutor",
    "ExecutorConfig",
    "ExecutorResult",
    "run_backtest_executor",
    # 並列処理（新規）
    "ParallelYearExecutor",
    "ParallelDataLoader",
    "ParallelStrategyComparator",
    "ParallelSignalEvaluator",
    "EvaluationResult",
    "EvaluatorParams",
    "evaluate_timeframe_signal",
    # 高速バックテスト（新規）
    "FastBacktestConfig",
    "FastBacktestEngine",
    "FastBacktestResult",
    "ChunkResult",
    # アダプター（新規）
    "CLIAdapter",
    "WebUIAdapter",
    # 診断・デバッグ（新規）
    "BacktestDiagnostics",
    "SignalDebugger",
    "run_diagnostics",
    "run_debug_signal",
]
