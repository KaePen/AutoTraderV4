"""外部システム接続アダプター"""

from __future__ import annotations

from autotrader.adapters.ollama import (
    OllamaClient,
    ConfidenceAdjustmentOutput,
    VetoCheckOutput,
)

__all__ = [
    "OllamaClient",
    "ConfidenceAdjustmentOutput",
    "VetoCheckOutput",
]
