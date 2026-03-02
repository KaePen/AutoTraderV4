"""TradeRepository CRUDテスト"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autotrader.adapters.database.models import Base
from autotrader.adapters.database.repositories import (
    TradeRepository,
)
from autotrader.core.entities import Trade
from autotrader.core.enums import ExitReason, SignalType


@pytest.fixture()
def session():
    """インメモリSQLiteセッション"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False,
    )
    sess = factory()
    yield sess
    sess.close()


class TestTradeRepository:
    """TradeRepository CRUDテスト"""

    def test_create_trade(self, session) -> None:
        """トレード作成 - Trade エンティティが返る"""
        repo = TradeRepository(session)
        now = datetime.now(timezone.utc)
        trade = repo.create(
            symbol="USDJPY",
            signal_type="BUY",
            volume=0.1,
            entry_price=150.0,
            opened_at=now,
            stop_loss=149.0,
            take_profit=152.0,
            ticket=12345,
        )
        session.commit()

        assert isinstance(trade, Trade)
        assert trade.trade_id is not None
        assert trade.symbol == "USDJPY"
        assert trade.signal_type == SignalType.BUY
        assert trade.volume == 0.1
        assert trade.entry_price == 150.0
        assert trade.stop_loss == 149.0
        assert trade.take_profit == 152.0
        assert trade.ticket == 12345
        assert trade.closed_at is None
        assert trade.exit_price is None

    def test_close_trade(self, session) -> None:
        """トレード決済 - trade_id で指定"""
        repo = TradeRepository(session)
        now = datetime.now(timezone.utc)
        trade = repo.create(
            symbol="EURUSD",
            signal_type="SELL",
            volume=0.2,
            entry_price=1.1000,
            opened_at=now,
        )
        session.commit()

        closed = repo.close(
            trade_id=trade.trade_id,
            exit_price=1.0950,
            closed_at=now,
            exit_reason="TP_HIT",
            profit_loss=100.0,
            profit_loss_pips=5.0,
        )
        session.commit()

        assert isinstance(closed, Trade)
        assert closed.closed_at is not None
        assert closed.exit_price == 1.0950
        assert closed.exit_reason == ExitReason.TAKE_PROFIT
        assert closed.profit_loss == 100.0
        assert closed.profit_loss_pips == 5.0

    def test_close_trade_not_found(self, session) -> None:
        """存在しない trade_id で close は None"""
        repo = TradeRepository(session)
        result = repo.close(
            trade_id="nonexistent",
            exit_price=1.0,
            closed_at=datetime.now(timezone.utc),
            exit_reason="MANUAL",
            profit_loss=0.0,
        )
        assert result is None

    def test_get_by_id(self, session) -> None:
        """IDでトレード取得 - Trade エンティティ"""
        repo = TradeRepository(session)
        now = datetime.now(timezone.utc)
        trade = repo.create(
            symbol="USDJPY",
            signal_type="BUY",
            volume=0.1,
            entry_price=150.0,
            opened_at=now,
        )
        session.commit()

        found = repo.get_by_id(trade.trade_id)
        assert found is not None
        assert isinstance(found, Trade)
        assert found.trade_id == trade.trade_id

    def test_get_by_id_not_found(self, session) -> None:
        """存在しないIDはNone"""
        repo = TradeRepository(session)
        assert repo.get_by_id("nonexistent") is None

    def test_get_open_trades(self, session) -> None:
        """オープントレード取得 - Trade エンティティリスト"""
        repo = TradeRepository(session)
        now = datetime.now(timezone.utc)

        # 2件オープン（USDJPY, EURUSD）
        repo.create(
            symbol="USDJPY",
            signal_type="BUY",
            volume=0.1,
            entry_price=150.0,
            opened_at=now,
        )
        trade2 = repo.create(
            symbol="EURUSD",
            signal_type="SELL",
            volume=0.2,
            entry_price=1.1000,
            opened_at=now,
        )
        # 1件クローズ
        repo.close(
            trade_id=trade2.trade_id,
            exit_price=1.0950,
            closed_at=now,
            exit_reason="SL_HIT",
            profit_loss=-50.0,
        )
        session.commit()

        # 全シンボル
        all_open = repo.get_open_trades()
        assert len(all_open) == 1
        assert isinstance(all_open[0], Trade)
        assert all_open[0].symbol == "USDJPY"

        # シンボル指定
        usdjpy = repo.get_open_trades(symbol="USDJPY")
        assert len(usdjpy) == 1

        eurusd = repo.get_open_trades(symbol="EURUSD")
        assert len(eurusd) == 0

    def test_to_entity_to_record_roundtrip(
        self, session,
    ) -> None:
        """_to_entity と _to_record の往復変換"""
        repo = TradeRepository(session)
        now = datetime.now(timezone.utc)
        trade = repo.create(
            symbol="GBPUSD",
            signal_type="BUY",
            volume=0.5,
            entry_price=1.2500,
            opened_at=now,
            stop_loss=1.2400,
            take_profit=1.2700,
            ticket=99999,
        )
        session.commit()

        # Trade → TradeRecord → Trade の往復
        record = TradeRepository._to_record(trade)
        roundtrip = TradeRepository._to_entity(record)
        assert roundtrip.trade_id == trade.trade_id
        assert roundtrip.symbol == trade.symbol
        assert roundtrip.signal_type == trade.signal_type
        assert roundtrip.volume == trade.volume
        assert roundtrip.entry_price == trade.entry_price
        assert roundtrip.stop_loss == trade.stop_loss
        assert roundtrip.take_profit == trade.take_profit
        assert roundtrip.ticket == trade.ticket
