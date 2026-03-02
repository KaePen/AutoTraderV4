"""後方互換性シム: scoring.consensus へ移動済み"""

from __future__ import annotations

from autotrader.decision.unified.scoring.consensus import *  # noqa: F401,F403
from autotrader.decision.unified.scoring.consensus import (  # noqa: F401
    ConsensusConfig,
    ConsensusResult,
    ModeAwareScoreConsensus,
)
