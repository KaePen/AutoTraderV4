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
    ForeignKey,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """ベースモデル"""

    pass


class SignalRecord(Base):
    """シグナル記録テーブル

    生成されたトレードシグナルを記録。
    """

    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(String(36), unique=True, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timeframe = Column(String(10), nullable=False)
    signal_type = Column(String(10), nullable=False)  # BUY/SELL/HOLD
    confidence = Column(Float, nullable=False)
    target_price = Column(Float, nullable=True)
    stop_loss_price = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    indicators_snapshot = Column(JSON, nullable=True)
    constraint_result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # リレーション
    trades = relationship("TradeRecord", back_populates="signal")

    __table_args__ = (
        Index("ix_signals_symbol_created", "symbol", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換"""
        return {
            "id": self.id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "signal_type": self.signal_type,
            "confidence": self.confidence,
            "target_price": self.target_price,
            "stop_loss_price": self.stop_loss_price,
            "reasoning": self.reasoning,
            "indicators_snapshot": self.indicators_snapshot,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TradeRecord(Base):
    """トレード記録テーブル

    実行されたトレードを記録。
    """

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(36), unique=True, nullable=False, index=True)
    signal_id = Column(String(36), ForeignKey("signals.signal_id"), nullable=True)
    backtest_id = Column(Integer, ForeignKey("backtest_results.id"), nullable=True)
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

    # リレーション
    signal = relationship("SignalRecord", back_populates="trades")
    backtest = relationship("BacktestResult", back_populates="trades")

    __table_args__ = (
        Index("ix_trades_symbol_opened", "symbol", "opened_at"),
        Index("ix_trades_backtest", "backtest_id"),
    )

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換"""
        return {
            "id": self.id,
            "trade_id": self.trade_id,
            "signal_id": self.signal_id,
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


class BacktestResult(Base):
    """バックテスト結果テーブル

    バックテスト実行結果を記録。
    """

    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    config = Column(JSON, nullable=True)

    # 評価指標
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    win_rate = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    total_profit = Column(Float, nullable=True)
    total_loss = Column(Float, nullable=True)
    net_profit = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)
    avg_trade_duration = Column(Float, nullable=True)  # 分単位
    avg_profit_per_trade = Column(Float, nullable=True)
    expectancy = Column(Float, nullable=True)

    # 日次統計
    daily_stats = Column(JSON, nullable=True)
    equity_curve = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="running")  # running/completed/failed

    # リレーション
    trades = relationship("TradeRecord", back_populates="backtest")

    __table_args__ = (
        Index("ix_backtest_symbol_date", "symbol", "start_date"),
    )

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換"""
        return {
            "id": self.id,
            "name": self.name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "net_profit": self.net_profit,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "expectancy": self.expectancy,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
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
