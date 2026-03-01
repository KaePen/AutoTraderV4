"""SQLAlchemyモデル定義"""

from __future__ import annotations

from datetime import datetime, timezone
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
    """ベースモデル（Supabase/メインDB用）"""

    pass


class LocalBase(DeclarativeBase):
    """ローカルDB用ベースモデル（SQLite専用）"""

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
    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
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
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

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


class MarketMemoryRecord(Base):
    """市場記憶テーブル

    LLMが生成した方向性の記憶をTTL付きで蓄積。
    マクロバイアス・指標後バイアス・センチメントを保存。
    """

    __tablename__ = "market_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    memory_id = Column(
        String(36), unique=True, nullable=False, index=True
    )
    symbol = Column(String(20), nullable=False, index=True)
    # MACRO_BIAS / POST_EVENT_BIAS / SENTIMENT_SCORE
    memory_type = Column(String(30), nullable=False)
    direction_score = Column(Float, nullable=False)  # -1.0〜+1.0
    confidence = Column(Float, nullable=False)  # 0.0〜1.0
    summary = Column(Text, nullable=True)
    source_event = Column(String(200), nullable=True)
    valid_until = Column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    llm_reasoning = Column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_market_memory_symbol_type_valid",
            "symbol",
            "memory_type",
            "valid_until",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換"""
        return {
            "memory_id": self.memory_id,
            "symbol": self.symbol,
            "memory_type": self.memory_type,
            "direction_score": self.direction_score,
            "confidence": self.confidence,
            "summary": self.summary,
            "source_event": self.source_event,
            "valid_until": (
                self.valid_until.isoformat()
                if self.valid_until else None
            ),
            "created_at": (
                self.created_at.isoformat()
                if self.created_at else None
            ),
        }


class PositionStateRecord(LocalBase):
    """ポジション管理状態テーブル（ローカルSQLite）

    PositionManagerの内部状態（追跡値・管理フラグ）を永続化。
    MT5が保持しないメモリ上の管理状態のみを保存する。
    """

    __tablename__ = "position_management_state"

    id = Column(
        Integer, primary_key=True, autoincrement=True,
    )
    position_id = Column(
        String(36), unique=True, nullable=False, index=True,
    )

    # 追跡値
    highest_price = Column(
        Float, nullable=False, default=0.0,
    )
    lowest_price = Column(
        Float, nullable=False, default=0.0,
    )
    highest_r = Column(
        Float, nullable=False, default=0.0,
    )
    bars_held = Column(
        Integer, nullable=False, default=0,
    )
    trailing_activated = Column(
        Boolean, nullable=False, default=False,
    )

    # 管理フラグ（7つ）
    partial_closed_1r = Column(
        Boolean, nullable=False, default=False,
    )
    partial_closed_2r = Column(
        Boolean, nullable=False, default=False,
    )
    tp_disabled = Column(
        Boolean, nullable=False, default=False,
    )
    early_be_applied = Column(
        Boolean, nullable=False, default=False,
    )
    insurance_sl_applied = Column(
        Boolean, nullable=False, default=False,
    )
    insurance_partial_applied = Column(
        Boolean, nullable=False, default=False,
    )
    half_r_partial_applied = Column(
        Boolean, nullable=False, default=False,
    )

    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
