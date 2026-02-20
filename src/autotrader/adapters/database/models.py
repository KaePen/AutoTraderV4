"""SQLAlchemyモデル定義"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Boolean,
    Text,
    JSON,
    Index,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ベースモデル"""

    pass


class TradeRecord(Base):
    """トレード記録テーブル

    実行されたトレードを記録。
    """

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(36), unique=True, nullable=False, index=True)
    ticket = Column(Integer, nullable=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    signal_type = Column(String(10), nullable=False)
    volume = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    profit_loss = Column(Float, nullable=True)
    profit_loss_pips = Column(Float, nullable=True)
    exit_reason = Column(String(30), nullable=True)
    opened_at = Column(DateTime, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    is_open = Column(Boolean, default=True, nullable=False)

    __table_args__ = (
        Index("ix_trades_symbol_opened", "symbol", "opened_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換"""
        return {
            "id": self.id,
            "trade_id": self.trade_id,
            "ticket": self.ticket,
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "profit_loss": self.profit_loss,
            "profit_loss_pips": self.profit_loss_pips,
            "exit_reason": self.exit_reason,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "is_open": self.is_open,
        }


class AuditLog(Base):
    """監査ログテーブル

    システム操作の監査ログを記録。
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False, index=True)
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(String(50), nullable=True)
    before_state = Column(JSON, nullable=True)
    after_state = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    user = Column(String(50), default="system")
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_audit_event_time", "event_type", "timestamp"),
    )

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換"""
        return {
            "id": self.id,
            "event_type": self.event_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "reason": self.reason,
            "user": self.user,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }
