"""コアモジュール

ドメインエンティティ、列挙型、例外、インターフェースを提供。
"""

from __future__ import annotations

from autotrader.core.entities import (
    Candle,
    Signal,
    Trade,
    Position,
    AccountInfo,
)
from autotrader.core.enums import (
    Timeframe,
    SignalType,
    ConfidenceLevel,
    ExitReason,
    TrendDirection,
    MarketSession,
    MarketRegime,
    TradingStrategyMode,
)
from autotrader.core.exceptions import (
    AutoTraderError,
    DataError,
    ExecutionError,
    ValidationError,
    LLMError,
    LLMConnectionError,
    LLMTimeoutError,
    LLMResponseError,
)

__all__ = [
    # Entities
    "Candle",
    "Signal",
    "Trade",
    "Position",
    "AccountInfo",
    # Enums
    "Timeframe",
    "SignalType",
    "ConfidenceLevel",
    "ExitReason",
    "TrendDirection",
    "MarketSession",
    "MarketRegime",
    "TradingStrategyMode",
    # Exceptions
    "AutoTraderError",
    "DataError",
    "ExecutionError",
    "ValidationError",
    "LLMError",
    "LLMConnectionError",
    "LLMTimeoutError",
    "LLMResponseError",
]
