"""制約機モジュールのテスト"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.constraint.hard_guard import (
    HardGuard,
    HardGuardConfig,
    HardGuardReason,
)
from autotrader.constraint.soft_guard import (
    SoftGuard,
    SoftGuardConfig,
    SoftGuardReason,
)
from autotrader.constraint.result import (
    ConstraintChecker,
    ConstraintAction,
)


class TestHardGuard:
    """ハードガードテスト"""

    def test_check_margin_ok(self) -> None:
        """証拠金OK"""
        guard = HardGuard()
        context = {"margin_ratio": 200.0}
        ok, reason = guard.check_margin(context)

        assert ok is True
        assert reason is None

    def test_check_margin_ng(self) -> None:
        """証拠金NG"""
        guard = HardGuard()
        context = {"margin_ratio": 100.0}
        ok, reason = guard.check_margin(context)

        assert ok is False
        assert "証拠金維持率不足" in reason

    def test_check_daily_loss_ok(self) -> None:
        """日次損失OK"""
        guard = HardGuard()
        context = {"daily_pnl_pct": -3.0}
        ok, reason = guard.check_daily_loss(context)

        assert ok is True

    def test_check_daily_loss_ng(self) -> None:
        """日次損失NG"""
        guard = HardGuard()
        context = {"daily_pnl_pct": -6.0}
        ok, reason = guard.check_daily_loss(context)

        assert ok is False
        assert "日次損失上限超過" in reason

    def test_check_position_limit_ok(self) -> None:
        """ポジション上限OK"""
        guard = HardGuard()
        context = {"position_count": 2}
        ok, reason = guard.check_position_limit(context)

        assert ok is True

    def test_check_position_limit_ng(self) -> None:
        """ポジション上限NG"""
        guard = HardGuard()
        context = {"position_count": 3}
        ok, reason = guard.check_position_limit(context)

        assert ok is False
        assert "ポジション上限" in reason

    def test_check_trading_hours_weekend(self) -> None:
        """週末は取引禁止"""
        guard = HardGuard()
        # 土曜日
        context = {"current_time": datetime(2024, 1, 6, 12, 0)}
        ok, reason = guard.check_trading_hours(context)

        assert ok is False
        assert "週末" in reason

    def test_check_blocked_hours(self) -> None:
        """取引禁止時間帯"""
        guard = HardGuard()
        # 23時（ブロック時間帯）
        context = {"current_time": datetime(2024, 1, 3, 23, 0)}
        ok, reason = guard.check_trading_hours(context)

        assert ok is False
        assert "取引禁止時間帯" in reason

    def test_check_high_impact_news(self) -> None:
        """高インパクトニュース前は禁止"""
        guard = HardGuard()
        context = {
            "high_impact_news": True,
            "news_minutes_away": 10,
        }
        ok, reason = guard.check_high_impact_news(context)

        assert ok is False
        assert "高インパクトニュース" in reason

    def test_full_check_allowed(self) -> None:
        """全チェック通過"""
        guard = HardGuard()
        context = {
            "margin_ratio": 200.0,
            "daily_pnl_pct": 0.0,
            "position_count": 1,
            "current_time": datetime(2024, 1, 3, 10, 0),
            "data_quality": "good",
            "high_impact_news": False,
        }
        result = guard.check(context, is_entry=True)

        assert result.is_allowed is True
        assert len(result.reasons) == 0

    def test_full_check_denied(self) -> None:
        """チェック失敗"""
        guard = HardGuard()
        context = {
            "margin_ratio": 100.0,  # NG
            "daily_pnl_pct": -6.0,  # NG
        }
        result = guard.check(context, is_entry=True)

        assert result.is_allowed is False
        assert len(result.reasons) >= 2


class TestSoftGuard:
    """ソフトガードテスト"""

    def test_check_spread_ok(self) -> None:
        """スプレッドOK"""
        guard = SoftGuard()
        context = {"spread_pips": 1.0}
        penalty, reason = guard.check_spread(context)

        assert penalty == 0.0
        assert reason is None

    def test_check_spread_high(self) -> None:
        """高スプレッド"""
        guard = SoftGuard()
        context = {"spread_pips": 3.0}
        penalty, reason = guard.check_spread(context)

        assert penalty > 0
        assert "高スプレッド" in reason

    def test_check_off_hours(self) -> None:
        """オフタイム"""
        guard = SoftGuard()
        context = {"current_time": datetime(2024, 1, 3, 5, 0)}  # 5時
        penalty, reason = guard.check_session_hours(context)

        assert penalty > 0
        assert "オフタイム" in reason

    def test_check_low_volatility(self) -> None:
        """低ボラティリティ"""
        guard = SoftGuard()
        context = {"atr_ratio": 0.3}
        penalty, reason = guard.check_volatility(context)

        assert penalty > 0
        assert "低ボラティリティ" in reason

    def test_check_high_volatility(self) -> None:
        """高ボラティリティ"""
        guard = SoftGuard()
        context = {"atr_ratio": 2.5}
        penalty, reason = guard.check_volatility(context)

        assert penalty > 0
        assert "高ボラティリティ" in reason

    def test_check_recent_loss(self) -> None:
        """連敗中"""
        guard = SoftGuard()
        context = {"recent_losses": 4}
        penalty, reason = guard.check_recent_performance(context)

        assert penalty > 0
        assert "連敗" in reason

    def test_check_mtf_conflict(self) -> None:
        """MTF不整合"""
        guard = SoftGuard()
        context = {"mtf_alignment": "conflicting"}
        penalty, reason = guard.check_mtf_conflict(context)

        assert penalty > 0
        assert "MTF不整合" in reason

    def test_full_check_no_penalty(self) -> None:
        """ペナルティなし"""
        guard = SoftGuard()
        context = {
            "spread_pips": 1.0,
            "current_time": datetime(2024, 1, 3, 10, 0),
            "atr_ratio": 1.0,
            "recent_losses": 0,
            "mtf_alignment": "aligned",
            "trend_strength": 0.6,
        }
        result = guard.check(context, is_entry=True)

        assert result.total_penalty == 0.0
        assert len(result.reasons) == 0

    def test_full_check_with_penalty(self) -> None:
        """ペナルティあり"""
        guard = SoftGuard()
        context = {
            "spread_pips": 3.0,
            "current_time": datetime(2024, 1, 3, 5, 0),
        }
        result = guard.check(context, is_entry=True)

        assert result.total_penalty > 0
        assert len(result.reasons) >= 2

    def test_penalty_capped_at_08(self) -> None:
        """ペナルティは0.8が上限"""
        guard = SoftGuard()
        context = {
            "spread_pips": 10.0,
            "current_time": datetime(2024, 1, 3, 5, 0),
            "atr_ratio": 3.0,
            "recent_losses": 10,
            "mtf_alignment": "conflicting",
            "trend_strength": 0.1,
        }
        result = guard.check(context, is_entry=True)

        assert result.total_penalty <= 0.8


class TestConstraintChecker:
    """制約チェッカーテスト"""

    def test_check_entry_allowed(self) -> None:
        """エントリー許可"""
        checker = ConstraintChecker()
        context = {
            "margin_ratio": 200.0,
            "daily_pnl_pct": 0.0,
            "position_count": 1,
            "current_time": datetime(2024, 1, 3, 10, 0),
            "data_quality": "good",
            "high_impact_news": False,
            "spread_pips": 1.0,
            "atr_ratio": 1.0,
            "recent_losses": 0,
            "mtf_alignment": "aligned",
            "trend_strength": 0.6,
        }
        result = checker.check_entry(context)

        assert result.action == ConstraintAction.ALLOW
        assert result.is_allowed() is True
        assert result.total_penalty == 0.0

    def test_check_entry_denied(self) -> None:
        """エントリー禁止"""
        checker = ConstraintChecker()
        context = {
            "margin_ratio": 100.0,  # NG
        }
        result = checker.check_entry(context)

        assert result.action == ConstraintAction.DENY
        assert result.is_allowed() is False
        assert result.total_penalty == 1.0

    def test_check_entry_penalized(self) -> None:
        """エントリーにペナルティ"""
        checker = ConstraintChecker()
        context = {
            "margin_ratio": 200.0,
            "daily_pnl_pct": 0.0,
            "position_count": 1,
            "current_time": datetime(2024, 1, 3, 10, 0),
            "data_quality": "good",
            "high_impact_news": False,
            "spread_pips": 4.0,  # ペナルティ
        }
        result = checker.check_entry(context)

        assert result.action == ConstraintAction.PENALIZE
        assert result.is_allowed() is True
        assert result.total_penalty > 0

    def test_adjusted_confidence(self) -> None:
        """確度調整"""
        checker = ConstraintChecker()
        context = {
            "margin_ratio": 200.0,
            "spread_pips": 4.0,
        }
        result = checker.check_entry(context)

        original_confidence = 0.7
        adjusted = result.get_adjusted_confidence(original_confidence)

        assert adjusted < original_confidence
        assert adjusted == original_confidence * (1 - result.total_penalty)
