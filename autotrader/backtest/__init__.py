"""バックテストモジュール"""

from __future__ import annotations

from autotrader.backtest.adapters import CLIAdapter, WebUIAdapter
from autotrader.backtest.config import (
    BacktestMetrics as UnifiedBacktestMetrics,
)
from autotrader.backtest.config import (
    MonthlyResult,
    ParallelBacktestConfig,
    UnifiedBacktestConfig,
    UnifiedBacktestResult,
    YearlyResult,
)
from autotrader.backtest.data_loader import DataLoader
from autotrader.backtest.csv_data_provider import CSVDataProvider
from autotrader.backtest.indicators import (
    MultiTimeframeDataLoader,
)
from autotrader.backtest.engine import (
    BacktestEngine,
    LegacyGeneratorAdapter,
    ParallelEngineConfig,
    ParallelMultiTFBacktestEngine,
    SignalGeneratorProtocol,
    UnifiedBacktestEngine,
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
    WalkForwardWindow,
    RollingWFResult,
    RollingWFReport,
    RollingWalkForwardValidator,
    ParameterStabilityTest,
    StabilityResult,
    StabilityReport,
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
from autotrader.backtest.year_runner import (
    run_unified_year,
    validate_trade_log,
)
from autotrader.backtest.month_runner import (
    run_monthly_parallel,
)
from autotrader.backtest.metrics_aggregator import (
    aggregate_results,
    aggregate_results_from_yearly,
)
from autotrader.backtest.parallel_worker import (
    _worker_process_init,
    _run_year_worker,
)
from autotrader.backtest.events import (
    BacktestEvent,
    BacktestEventEmitter,
    CandleEvent,
    ConsoleEventListener,
    EventListener,
    EventType,
    MetricsEvent,
    ProgressEvent,
    SignalEvent,
    TimelineEventQueue,
    TradeEvent,
)
from autotrader.backtest.executor import (
    BacktestExecutor,
    ExecutorConfig,
    ExecutorResult,
)
from autotrader.backtest.executor import (
    run_backtest as run_backtest_executor,
)
from autotrader.backtest.fast_backtest import (
    ChunkResult,
    FastBacktestConfig,
    FastBacktestEngine,
    FastBacktestResult,
)
from autotrader.backtest.file_listener import FileEventListener
from autotrader.backtest.formatters import (
    CLIFormatter,
    CompactFormatter,
    JSONFormatter,
    ResultFormatter,
)
from autotrader.backtest.indicators import (
    MultiTimeframeDataLoader,
)
from autotrader.backtest.metrics import MetricsCalculator
from autotrader.backtest.metrics_aggregator import (
    aggregate_results,
    aggregate_results_from_yearly,
)
from autotrader.backtest.parallel import (
    EvaluationResult,
    EvaluatorParams,
    ParallelDataLoader,
    ParallelSignalEvaluator,
    ParallelStrategyComparator,
    ParallelYearExecutor,
    evaluate_timeframe_signal,
)
from autotrader.backtest.parallel_worker import (
    _run_year_worker,
    _worker_process_init,
)
from autotrader.backtest.runner import (
    BacktestConfig,
    BacktestResult,
    BacktestRunner,
)
from autotrader.backtest.service import (
    BacktestService,
    BacktestServiceConfig,
    create_backtest_config,
    create_bot_config,
    run_backtest,
)
from autotrader.backtest.simulator import TradeSimulator
from autotrader.backtest.state import (
    BacktestState,
    BacktestStateManager,
    BacktestStatus,
    get_state_manager,
)
from autotrader.backtest.strategy_factory import (
    StrategyFactory,
    StrategyInfo,
)
from autotrader.backtest.walk_forward import (
    OverfittingWarning,
    PeriodMetrics,
    WalkForwardConfig,
    WalkForwardPeriod,
    WalkForwardResult,
    WalkForwardValidator,
    create_walk_forward_periods,
)
from autotrader.backtest.year_runner import (
    run_unified_year,
    validate_trade_log,
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
    "CSVDataProvider",
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
    # ウォークフォワード（年単位ローリング）
    "WalkForwardWindow",
    "RollingWFResult",
    "RollingWFReport",
    "RollingWalkForwardValidator",
    # パラメータ安定性テスト
    "ParameterStabilityTest",
    "StabilityResult",
    "StabilityReport",
    # 戦略
    "StrategyFactory",
    "StrategyInfo",
    # ランナー（レガシー互換）
    "BacktestRunner",
    "BacktestConfig",
    "BacktestResult",
    # 分割モジュール（新規）
    "run_unified_year",
    "validate_trade_log",
    "run_monthly_parallel",
    "aggregate_results",
    "aggregate_results_from_yearly",
    "_worker_process_init",
    "_run_year_worker",
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
