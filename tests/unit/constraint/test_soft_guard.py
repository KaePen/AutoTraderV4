"""SoftGuardのユニットテスト"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.constraint.soft_guard import (
    DynamicSpreadEstimator,
    SoftGuard,
    SoftGuardConfig,
)


class TestDynamicSpreadEstimator:
    """動的スプレッド見積もり機能のテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.estimator = DynamicSpreadEstimator()

    def test_tokyo_low_liquidity_hours(self) -> None:
        """TOKYO早朝（低流動性時間帯）でスプレッド1.5倍"""
        base_spread = 2.0
        adjusted = self.estimator.estimate_spread(
            base_spread=base_spread,
            session="TOKYO",
            hour=18,  # UTC 18時（低流動性）
            atr_ratio=1.0,
            is_pre_event=False,
        )

        assert adjusted == base_spread * 1.5

    def test_high_volatility_spreads(self) -> None:
        """高ボラティリティ時のスプレッド拡大"""
        base_spread = 2.0
        atr_ratio = 1.8

        adjusted = self.estimator.estimate_spread(
            base_spread=base_spread,
            session="LONDON",
            hour=12,
            atr_ratio=atr_ratio,
            is_pre_event=False,
        )

        # atr_ratio=1.8で1.8倍（上限2.0まで）
        assert adjusted == base_spread * atr_ratio

    def test_very_high_volatility_capped(self) -> None:
        """超高ボラティリティ時は上限2.0倍"""
        base_spread = 2.0
        atr_ratio = 3.0  # 非常に高い

        adjusted = self.estimator.estimate_spread(
            base_spread=base_spread,
            session="LONDON",
            hour=12,
            atr_ratio=atr_ratio,
            is_pre_event=False,
        )

        # 上限2.0倍
        assert adjusted == base_spread * 2.0

    def test_pre_event_doubles_spread(self) -> None:
        """イベント前はスプレッド2倍"""
        base_spread = 2.0

        adjusted = self.estimator.estimate_spread(
            base_spread=base_spread,
            session="NEWYORK",
            hour=14,
            atr_ratio=1.0,
            is_pre_event=True,
        )

        assert adjusted == base_spread * 2.0

    def test_combined_adjustments(self) -> None:
        """複数の調整が重なる場合"""
        base_spread = 2.0

        # TOKYO早朝 + 高ボラ + イベント前
        adjusted = self.estimator.estimate_spread(
            base_spread=base_spread,
            session="TOKYO",
            hour=19,  # 低流動性
            atr_ratio=1.8,  # 高ボラ
            is_pre_event=True,  # イベント前
        )

        # 1.5倍（低流動性） × min(1.8, 2.0) × 2.0（イベント前）
        expected = base_spread * 1.5 * 1.8 * 2.0
        assert adjusted == expected

    def test_normal_conditions(self) -> None:
        """通常条件ではスプレッドそのまま"""
        base_spread = 2.0

        adjusted = self.estimator.estimate_spread(
            base_spread=base_spread,
            session="LONDON",
            hour=12,
            atr_ratio=1.0,
            is_pre_event=False,
        )

        assert adjusted == base_spread


class TestSoftGuardWithDynamicSpread:
    """SoftGuardの動的スプレッド統合テスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.guard = SoftGuard(
            SoftGuardConfig(
                dynamic_spread_enabled=True,
                spread_threshold_pips=2.0,
            )
        )

    def test_dynamic_spread_in_soft_guard(self) -> None:
        """SoftGuardで動的スプレッドが適用される"""
        context = {
            "spread_pips": 1.5,  # 基本スプレッド
            "session": "TOKYO",
            "current_time": datetime(2024, 1, 1, 18, 0),  # UTC 18時
            "atr_ratio": 1.0,
            "is_pre_event": False,
        }

        penalty, reason = self.guard.check_spread(context)

        # 1.5 * 1.5 = 2.25 > 2.0 → ペナルティ発生
        assert penalty > 0.0
        assert "高スプレッド" in reason

    def test_dynamic_spread_disabled(self) -> None:
        """動的スプレッド無効時は基本スプレッドのみ使用"""
        guard = SoftGuard(
            SoftGuardConfig(
                dynamic_spread_enabled=False,
                spread_threshold_pips=2.0,
            )
        )

        context = {
            "spread_pips": 1.5,  # 基本スプレッド
            "session": "TOKYO",
            "current_time": datetime(2024, 1, 1, 18, 0),
            "atr_ratio": 1.0,
            "is_pre_event": False,
        }

        penalty, reason = guard.check_spread(context)

        # 動的調整なし: 1.5 < 2.0 → ペナルティなし
        assert penalty == 0.0
        assert reason is None
