"""セッション切替待機フィルタのテスト"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.constraint.filters.session_transition_filter import (
    SessionTransitionFilter,
    SessionTransitionResult,
)


class TestSessionTransitionFilter:
    """SessionTransitionFilterのテスト"""

    @pytest.fixture
    def filter_default(self) -> SessionTransitionFilter:
        """デフォルト設定のフィルタ"""
        return SessionTransitionFilter()

    @pytest.fixture
    def filter_custom(self) -> SessionTransitionFilter:
        """カスタム設定のフィルタ"""
        return SessionTransitionFilter(wait_minutes=15)

    @pytest.fixture
    def filter_disabled(self) -> SessionTransitionFilter:
        """無効化されたフィルタ"""
        return SessionTransitionFilter(enabled=False)

    def test_tokyo_to_london_transition(
        self,
        filter_default: SessionTransitionFilter,
    ) -> None:
        """TOKYO→LONDON切替後30分は待機"""
        # UTC 07:00（切替直後）
        time_07_00 = datetime(2024, 1, 15, 7, 0, 0)
        result = filter_default.check(time_07_00)
        assert result.should_filter is True
        assert "TOKYO_TO_LONDON" in result.transition_name
        assert result.minutes_remaining == 30

        # UTC 07:15（待機中）
        time_07_15 = datetime(2024, 1, 15, 7, 15, 0)
        result = filter_default.check(time_07_15)
        assert result.should_filter is True
        assert result.minutes_remaining == 15

        # UTC 07:29（待機終了直前）
        time_07_29 = datetime(2024, 1, 15, 7, 29, 0)
        result = filter_default.check(time_07_29)
        assert result.should_filter is True
        assert result.minutes_remaining == 1

        # UTC 07:30（待機終了）
        time_07_30 = datetime(2024, 1, 15, 7, 30, 0)
        result = filter_default.check(time_07_30)
        assert result.should_filter is False

    def test_london_to_newyork_transition(
        self,
        filter_default: SessionTransitionFilter,
    ) -> None:
        """LONDON→NEWYORK切替後30分は待機"""
        # UTC 13:00（切替直後）
        time_13_00 = datetime(2024, 1, 15, 13, 0, 0)
        result = filter_default.check(time_13_00)
        assert result.should_filter is True
        assert "LONDON_TO_NEWYORK" in result.transition_name

        # UTC 13:30（待機終了）
        time_13_30 = datetime(2024, 1, 15, 13, 30, 0)
        result = filter_default.check(time_13_30)
        assert result.should_filter is False

    def test_newyork_to_tokyo_transition(
        self,
        filter_default: SessionTransitionFilter,
    ) -> None:
        """NEWYORK→TOKYO切替後30分は待機"""
        # UTC 22:00（切替直後）
        time_22_00 = datetime(2024, 1, 15, 22, 0, 0)
        result = filter_default.check(time_22_00)
        assert result.should_filter is True
        assert "NEWYORK_TO_TOKYO" in result.transition_name

        # UTC 22:30（待機終了）
        time_22_30 = datetime(2024, 1, 15, 22, 30, 0)
        result = filter_default.check(time_22_30)
        assert result.should_filter is False

    def test_non_transition_time(
        self,
        filter_default: SessionTransitionFilter,
    ) -> None:
        """切替時間外はフィルタしない"""
        # 各セッション中の時間
        test_times = [
            datetime(2024, 1, 15, 3, 0, 0),   # TOKYO
            datetime(2024, 1, 15, 10, 0, 0),  # LONDON
            datetime(2024, 1, 15, 18, 0, 0),  # NEWYORK
        ]

        for test_time in test_times:
            result = filter_default.check(test_time)
            assert result.should_filter is False

    def test_custom_wait_minutes(
        self,
        filter_custom: SessionTransitionFilter,
    ) -> None:
        """カスタム待機時間の設定"""
        # UTC 07:00（切替直後）
        time_07_00 = datetime(2024, 1, 15, 7, 0, 0)
        result = filter_custom.check(time_07_00)
        assert result.should_filter is True
        assert result.minutes_remaining == 15

        # UTC 07:15（待機終了）
        time_07_15 = datetime(2024, 1, 15, 7, 15, 0)
        result = filter_custom.check(time_07_15)
        assert result.should_filter is False

    def test_disabled_filter(
        self,
        filter_disabled: SessionTransitionFilter,
    ) -> None:
        """無効化時はフィルタしない"""
        # 切替直後の時間でもフィルタしない
        time_07_00 = datetime(2024, 1, 15, 7, 0, 0)
        result = filter_disabled.check(time_07_00)
        assert result.should_filter is False

    def test_should_filter_simple_interface(
        self,
        filter_default: SessionTransitionFilter,
    ) -> None:
        """簡易インターフェースのテスト"""
        # 切替中
        time_07_00 = datetime(2024, 1, 15, 7, 0, 0)
        should_filter, reason = filter_default.should_filter(time_07_00)
        assert should_filter is True
        assert "TOKYO_TO_LONDON" in reason

        # 切替外
        time_10_00 = datetime(2024, 1, 15, 10, 0, 0)
        should_filter, reason = filter_default.should_filter(time_10_00)
        assert should_filter is False
        assert reason == ""

    def test_result_dataclass(self) -> None:
        """SessionTransitionResultのデフォルト値"""
        result = SessionTransitionResult(should_filter=False)
        assert result.should_filter is False
        assert result.reason == ""
        assert result.transition_name == ""
        assert result.minutes_remaining == 0
