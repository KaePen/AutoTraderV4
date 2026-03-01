"""MarketMemoryRepository CRUDテスト"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autotrader.adapters.database.models import Base
from autotrader.adapters.database.repositories import (
    MarketMemoryRepository,
)


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


class TestMarketMemoryRepository:
    """MarketMemoryRepository CRUDテスト"""

    def test_create(self, session) -> None:
        """記憶作成"""
        repo = MarketMemoryRepository(session)
        now = datetime.now(timezone.utc)
        valid = now + timedelta(hours=6)

        record = repo.create(
            memory_id="mem-001",
            symbol="USDJPY",
            memory_type="MACRO_BIAS",
            direction_score=0.7,
            confidence=0.85,
            valid_until=valid,
            summary="USD強気",
            source_event="FOMC",
        )
        session.commit()

        assert record.memory_id == "mem-001"
        assert record.symbol == "USDJPY"
        assert record.direction_score == 0.7
        assert record.confidence == 0.85

    def test_get_active(self, session) -> None:
        """有効な記憶のみ取得"""
        repo = MarketMemoryRepository(session)
        now = datetime.now(timezone.utc)

        # 有効
        repo.create(
            memory_id="active-1",
            symbol="USDJPY",
            memory_type="MACRO_BIAS",
            direction_score=0.5,
            confidence=0.8,
            valid_until=now + timedelta(hours=2),
        )
        # 期限切れ
        repo.create(
            memory_id="expired-1",
            symbol="USDJPY",
            memory_type="MACRO_BIAS",
            direction_score=-0.3,
            confidence=0.6,
            valid_until=now - timedelta(hours=1),
        )
        # 別シンボル
        repo.create(
            memory_id="other-1",
            symbol="EURUSD",
            memory_type="MACRO_BIAS",
            direction_score=0.2,
            confidence=0.7,
            valid_until=now + timedelta(hours=3),
        )
        session.commit()

        active = repo.get_active(
            symbol="USDJPY",
            memory_type="MACRO_BIAS",
            now=now,
        )
        assert len(active) == 1
        assert active[0].memory_id == "active-1"

    def test_delete_expired(self, session) -> None:
        """期限切れ記憶を削除"""
        repo = MarketMemoryRepository(session)
        now = datetime.now(timezone.utc)

        # 期限切れ2件 + 有効1件
        repo.create(
            memory_id="exp-1",
            symbol="USDJPY",
            memory_type="MACRO_BIAS",
            direction_score=0.1,
            confidence=0.5,
            valid_until=now - timedelta(hours=2),
        )
        repo.create(
            memory_id="exp-2",
            symbol="EURUSD",
            memory_type="SENTIMENT_SCORE",
            direction_score=-0.2,
            confidence=0.4,
            valid_until=now - timedelta(minutes=30),
        )
        repo.create(
            memory_id="valid-1",
            symbol="USDJPY",
            memory_type="MACRO_BIAS",
            direction_score=0.8,
            confidence=0.9,
            valid_until=now + timedelta(hours=5),
        )
        session.commit()

        deleted = repo.delete_expired(now)
        session.commit()

        assert deleted == 2
        # 有効な記憶は残る
        remaining = repo.get_active(
            symbol="USDJPY",
            memory_type="MACRO_BIAS",
            now=now,
        )
        assert len(remaining) == 1
        assert remaining[0].memory_id == "valid-1"
