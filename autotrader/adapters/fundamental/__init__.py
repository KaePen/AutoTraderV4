"""ファンダメンタルデータ収集アダプター

経済指標・ニュース・市場記憶のデータ収集・管理機能を提供。
"""

from __future__ import annotations

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    FundamentalContext,
    ImpactLevel,
    EventSource,
)
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)
from autotrader.adapters.fundamental.memory import (
    FundamentalMemoryService,
)

__all__ = [
    "EconomicEvent",
    "FundamentalContext",
    "ImpactLevel",
    "EventSource",
    "EconomicEventNormalizer",
    "FundamentalMemoryService",
]
