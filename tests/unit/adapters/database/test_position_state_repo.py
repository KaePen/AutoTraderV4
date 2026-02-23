"""PositionStateRepository CRUDテスト"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autotrader.adapters.database.models import (
    LocalBase,
    PositionStateRecord,
)
from autotrader.adapters.database.repositories import (
    PositionStateRepository,
)


@pytest.fixture()
def session():
    """インメモリSQLiteセッション"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    LocalBase.metadata.create_all(bind=engine)
    factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False,
    )
    sess = factory()
    yield sess
    sess.close()


class TestPositionStateRepository:
    """PositionStateRepository CRUDテスト"""

    def test_upsert_insert(self, session) -> None:
        """新規INSERT"""
        repo = PositionStateRepository(session)
        state = {
            "position_id": "12345",
            "highest_price": 151.5,
            "lowest_price": 149.0,
            "highest_r": 1.5,
            "bars_held": 10,
            "trailing_activated": True,
            "partial_closed_1r": True,
            "partial_closed_2r": False,
            "tp_disabled": True,
            "early_be_applied": True,
            "insurance_sl_applied": False,
            "insurance_partial_applied": False,
            "half_r_partial_applied": False,
        }
        repo.upsert(state)
        session.commit()

        record = repo.get_by_position_id("12345")
        assert record is not None
        assert record.position_id == "12345"
        assert record.highest_price == 151.5
        assert record.lowest_price == 149.0
        assert record.highest_r == 1.5
        assert record.bars_held == 10
        assert record.trailing_activated is True
        assert record.partial_closed_1r is True
        assert record.partial_closed_2r is False
        assert record.tp_disabled is True
        assert record.early_be_applied is True
        assert record.insurance_sl_applied is False
        assert record.insurance_partial_applied is False
        assert record.half_r_partial_applied is False

    def test_upsert_update(self, session) -> None:
        """既存レコードのUPDATE"""
        repo = PositionStateRepository(session)
        # 初回INSERT
        state1 = {
            "position_id": "99999",
            "highest_price": 150.0,
            "lowest_price": 148.0,
            "highest_r": 0.5,
            "bars_held": 5,
            "trailing_activated": False,
            "partial_closed_1r": False,
            "partial_closed_2r": False,
            "tp_disabled": False,
            "early_be_applied": False,
            "insurance_sl_applied": False,
            "insurance_partial_applied": False,
            "half_r_partial_applied": False,
        }
        repo.upsert(state1)
        session.commit()

        # UPDATE
        state2 = {
            "position_id": "99999",
            "highest_price": 152.0,
            "lowest_price": 148.0,
            "highest_r": 2.0,
            "bars_held": 20,
            "trailing_activated": True,
            "partial_closed_1r": True,
            "partial_closed_2r": True,
            "tp_disabled": True,
            "early_be_applied": True,
            "insurance_sl_applied": True,
            "insurance_partial_applied": True,
            "half_r_partial_applied": True,
        }
        repo.upsert(state2)
        session.commit()

        record = repo.get_by_position_id("99999")
        assert record is not None
        assert record.highest_price == 152.0
        assert record.highest_r == 2.0
        assert record.bars_held == 20
        assert record.trailing_activated is True
        assert record.partial_closed_1r is True
        assert record.partial_closed_2r is True
        assert record.tp_disabled is True
        assert record.insurance_sl_applied is True

    def test_delete(self, session) -> None:
        """削除後にgetがNone"""
        repo = PositionStateRepository(session)
        state = {
            "position_id": "to_delete",
            "highest_price": 0.0,
            "lowest_price": 0.0,
            "highest_r": 0.0,
            "bars_held": 0,
            "trailing_activated": False,
            "partial_closed_1r": False,
            "partial_closed_2r": False,
            "tp_disabled": False,
            "early_be_applied": False,
            "insurance_sl_applied": False,
            "insurance_partial_applied": False,
            "half_r_partial_applied": False,
        }
        repo.upsert(state)
        session.commit()

        assert repo.get_by_position_id(
            "to_delete"
        ) is not None

        repo.delete("to_delete")
        session.commit()

        assert repo.get_by_position_id(
            "to_delete"
        ) is None

    def test_get_all(self, session) -> None:
        """全レコード取得"""
        repo = PositionStateRepository(session)
        for pid in ["a", "b", "c"]:
            repo.upsert({
                "position_id": pid,
                "highest_price": 0.0,
                "lowest_price": 0.0,
                "highest_r": 0.0,
                "bars_held": 0,
                "trailing_activated": False,
                "partial_closed_1r": False,
                "partial_closed_2r": False,
                "tp_disabled": False,
                "early_be_applied": False,
                "insurance_sl_applied": False,
                "insurance_partial_applied": False,
                "half_r_partial_applied": False,
            })
        session.commit()

        all_records = repo.get_all()
        assert len(all_records) == 3
        ids = {r.position_id for r in all_records}
        assert ids == {"a", "b", "c"}

    def test_get_nonexistent(self, session) -> None:
        """存在しないIDの取得はNone"""
        repo = PositionStateRepository(session)
        assert repo.get_by_position_id("nope") is None
