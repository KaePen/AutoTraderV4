"""後方互換性シム: scoring.consolidator へ移動済み"""

from __future__ import annotations

from autotrader.decision.unified.scoring.consolidator import *  # noqa: F401,F403
from autotrader.decision.unified.scoring.consolidator import (  # noqa: F401
    ConsolidatedSignal,
    PortfolioState,
    SignalConsolidator,
)
