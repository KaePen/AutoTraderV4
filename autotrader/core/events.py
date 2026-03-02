"""型付きイベント定義

EventBus で発行/購読するイベントの dataclass 定義。
frozen=True で不変性を保証し、typo やフィールド不整合を
静的解析で検出可能にする。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class BaseEvent:
    """全イベントの基底クラス"""

    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class TradeOpenedEvent(BaseEvent):
    """トレードオープンイベント"""

    trade_id: str = ""
    symbol: str = ""
    direction: str = ""
    volume: float = 0.0
    entry_price: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0


@dataclass(frozen=True)
class TradeClosedEvent(BaseEvent):
    """トレードクローズイベント"""

    trade_id: str = ""
    symbol: str = ""
    pnl: float = 0.0
    close_reason: str = ""


@dataclass(frozen=True)
class SignalGeneratedEvent(BaseEvent):
    """シグナル生成イベント"""

    symbol: str = ""
    direction: str = ""
    confidence: float = 0.0
    timeframe: str = ""


@dataclass(frozen=True)
class PositionUpdatedEvent(BaseEvent):
    """ポジション更新イベント"""

    trade_id: str = ""
    symbol: str = ""
    action: str = ""  # trailing_update, partial_close, breakeven
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorEvent(BaseEvent):
    """エラーイベント"""

    source: str = ""
    error_type: str = ""
    message: str = ""
