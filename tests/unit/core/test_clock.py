"""Clock Protocol テスト

FixedClock / SystemClock の動作を検証する。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autotrader.core.clock import FixedClock, SystemClock


class TestSystemClock:
    """SystemClock が現在時刻を返す"""

    def test_now_returns_datetime(self) -> None:
        clock = SystemClock()
        now = clock.now()
        assert isinstance(now, datetime)

    def test_now_is_close_to_real_time(self) -> None:
        clock = SystemClock()
        before = datetime.now(UTC)
        result = clock.now()
        after = datetime.now(UTC)
        assert before <= result <= after


class TestFixedClock:
    """FixedClock が固定時刻を返す"""

    def test_now_returns_fixed_time(self) -> None:
        t = datetime(2024, 1, 15, 10, 30, 0)
        clock = FixedClock(t)
        assert clock.now() == t

    def test_now_is_stable(self) -> None:
        t = datetime(2024, 1, 15, 10, 30, 0)
        clock = FixedClock(t)
        assert clock.now() == clock.now()

    def test_advance_moves_time_forward(self) -> None:
        t = datetime(2024, 1, 15, 10, 0, 0)
        clock = FixedClock(t)
        clock.advance(hours=2, minutes=30)
        assert clock.now() == t + timedelta(hours=2, minutes=30)

    def test_advance_cumulative(self) -> None:
        t = datetime(2024, 1, 15, 10, 0, 0)
        clock = FixedClock(t)
        clock.advance(hours=1)
        clock.advance(minutes=15)
        assert clock.now() == t + timedelta(hours=1, minutes=15)
