"""Ollama LLMアダプタ

構造化出力対応のOllama LLMクライアント。
Veto判定・信頼度調整機能を提供。
"""

from __future__ import annotations

from autotrader.adapters.ollama.client import OllamaClient
from autotrader.adapters.ollama.schemas import (
    ConfidenceAdjustmentOutput,
    VetoCheckOutput,
)

__all__ = [
    "OllamaClient",
    "ConfidenceAdjustmentOutput",
    "VetoCheckOutput",
]
