"""BacktestStateManager + FixedClock テスト

Clock 注入により時刻をテストで制御できることを検証する。
"""

from __future__ import annotations

from datetime import UTC, datetime

from autotrader.backtest.state import BacktestStateManager
from autotrader.core.clock import FixedClock


class TestBacktestStateManagerClock:
    """BacktestStateManager に FixedClock を注入するテスト"""

    def test_start_uses_injected_clock(self) -> None:
        """start() が注入された Clock の時刻を使う"""
        t = datetime(2024, 6, 1, 9, 0, 0)
        clock = FixedClock(t)
        mgr = BacktestStateManager(clock=clock)

        state = mgr.create("bt-1")
        mgr.start("bt-1")

        assert state.started_at == t

    def test_complete_uses_injected_clock(self) -> None:
        """complete() が注入された Clock の時刻を使う"""
        t = datetime(2024, 6, 1, 9, 0, 0)
        clock = FixedClock(t)
        mgr = BacktestStateManager(clock=clock)

        mgr.create("bt-1")
        mgr.start("bt-1")

        # 1時間後に完了
        clock.advance(hours=1)
        mgr.complete("bt-1")

        state = mgr.get("bt-1")
        assert state is not None
        assert state.completed_at == datetime(2024, 6, 1, 10, 0, 0)

    def test_cleanup_completed_respects_clock(self) -> None:
        """cleanup_completed が Clock の時刻で判定する"""
        t = datetime(2024, 6, 1, 9, 0, 0)
        clock = FixedClock(t)
        mgr = BacktestStateManager(clock=clock)

        mgr.create("bt-1")
        mgr.start("bt-1")
        mgr.complete("bt-1")

        # 30分後 — まだ削除されない（デフォルト1時間）
        clock.advance(minutes=30)
        cleaned = mgr.cleanup_completed(max_age_seconds=3600)
        assert cleaned == 0

        # 2時間後 — 削除される
        clock.advance(hours=2)
        cleaned = mgr.cleanup_completed(max_age_seconds=3600)
        assert cleaned == 1
        assert mgr.get("bt-1") is None

    def test_default_clock_is_system_clock(self) -> None:
        """clock 引数なしの場合 SystemClock が使われる"""
        mgr = BacktestStateManager()
        mgr.create("bt-1")
        mgr.start("bt-1")

        state = mgr.get("bt-1")
        assert state is not None
        assert state.started_at is not None
        # SystemClock なので現在時刻に近い値
        diff = abs(
            (datetime.now(UTC) - state.started_at).total_seconds()
        )
        assert diff < 5
