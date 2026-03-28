"""SQLAlchemyモデル定義"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ベースモデル（メインDB用）"""

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
    entry_own_score = Column(Float, nullable=True, default=0.0)
    opened_at = Column(DateTime(timezone=True), nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    is_open = Column(Boolean, default=True, nullable=False)

    __table_args__ = (
        Index("ix_trades_symbol_opened", "symbol", "opened_at"),
        Index("ix_trades_is_open_symbol", "is_open", "symbol"),
        Index("ix_trades_closed_at", "closed_at"),
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
            "entry_own_score": self.entry_own_score,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "is_open": self.is_open,
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

    # エッジ劣化監視: エントリー時スコア（再起動後も劣化計算を継続するために保存）
    entry_own_score = Column(
        Float, nullable=True, default=0.0,
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
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
