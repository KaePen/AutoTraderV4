"""ルーターモジュール"""

from __future__ import annotations

from autotrader.web.routers import (
    candles,
    dashboard,
    fundamental,
    indicators,
    positions,
    settings,
    signals,
    trades,
    trading,
)

__all__ = [
    "dashboard",
    "signals",
    "positions",
    "trades",
    "indicators",
    "candles",
    "settings",
    "trading",
    "fundamental",
]
