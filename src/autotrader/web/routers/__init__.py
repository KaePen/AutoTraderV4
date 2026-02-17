"""ルーターモジュール"""

from __future__ import annotations

from autotrader.web.routers import (
    dashboard,
    signals,
    positions,
    trades,
    indicators,
    candles,
    settings,
    backtest,
)

__all__ = [
    "dashboard",
    "signals",
    "positions",
    "trades",
    "indicators",
    "candles",
    "settings",
    "backtest",
]
