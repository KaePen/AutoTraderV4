"""コアモジュール

ドメインエンティティ、列挙型、例外、インターフェースを提供。
"""

from __future__ import annotations

from autotrader.core.entities import (
    AccountInfo,
    Candle,
    Position,
    Signal,
    Trade,
)
from autotrader.core.enums import (
    ConfidenceLevel,
    ExitReason,
    MarketRegime,
    MarketSession,
    SignalType,
    Timeframe,
    TrendDirection,
)
from autotrader.core.exceptions import (
    AutoTraderError,
    DataError,
    ExecutionError,
    LLMConnectionError,
    LLMError,
    LLMResponseError,
    LLMTimeoutError,
    ValidationError,
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
