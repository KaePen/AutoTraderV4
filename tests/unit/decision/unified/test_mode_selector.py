"""TradingModeSelectorのユニットテスト"""

from __future__ import annotations

import pytest

from autotrader.core.enums import MarketRegime, TradingStrategyMode
from autotrader.decision.unified.mode_selector import (
    ModeSelectorConfig,
    TradingModeSelector,
    TradingPlan,
)


class TestTradingModeSelector:
    """TradingModeSelectorのテスト - 常にUNIVERSALプランを返す"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.selector = TradingModeSelector()

    def test_always_returns_universal(self) -> None:
        """全レジームでUNIVERSALプランを返す"""
        for regime in [
            MarketRegime.HIGH_VOL,
            MarketRegime.TREND,
            MarketRegime.RANGE,
            MarketRegime.LOW_VOL,
        ]:
            plan = self.selector.select(
                regime=regime,
                volatility_level=1.0,
                htf_alignment=0.5,
            )
            assert plan.mode == TradingStrategyMode.UNIVERSAL

    def test_universal_plan_includes_all_tfs(self) -> None:
        """UNIVERSALプランは全TFをconfirm_tfsに含む"""
        plan = self.selector.select(
            regime=MarketRegime.RANGE,
            volatility_level=1.0,
        )
        assert plan.mode == TradingStrategyMode.UNIVERSAL
        assert len(plan.confirm_tfs) > 3

    def test_selection_reason_contains_universal(self) -> None:
        """UNIVERSALモードの選択理由が設定される"""
        plan = self.selector.select(
            regime=MarketRegime.TREND,
            volatility_level=1.0,
        )
        assert "UNIVERSAL" in plan.selection_reason


class TestTradingPlan:
    """TradingPlanのテスト"""

    def test_all_tfs_property(self) -> None:
        """all_tfsプロパティ"""
        plan = TradingPlan(
            mode=TradingStrategyMode.UNIVERSAL,
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1", "H4"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
        )

        all_tfs = plan.all_tfs

        assert "M15" in all_tfs
        assert "M5" in all_tfs
        assert "H1" in all_tfs
        assert "H4" in all_tfs
        # 重複なし
        assert len(all_tfs) == len(set(all_tfs))

    def test_recommended_tp_sl_ratio(self) -> None:
        """推奨TP/SL比率"""
        plan = TradingPlan(
            mode=TradingStrategyMode.UNIVERSAL,
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
        )

        ratio = plan.get_recommended_tp_sl_ratio()

        assert ratio == 2.0  # (1.5 + 2.5) / 2


class TestModeSelectorConfig:
    """ModeSelectorConfigのテスト"""

    def test_default_config(self) -> None:
        """デフォルト設定でUNIVERSAL返却"""
        config = ModeSelectorConfig()
        selector = TradingModeSelector(config)

        plan = selector.select(
            regime=MarketRegime.TREND,
            volatility_level=1.0,
            htf_alignment=0.9,
        )

        assert plan.mode == TradingStrategyMode.UNIVERSAL

    def test_get_plan_for_mode(self) -> None:
        """UNIVERSALモード指定でプラン取得"""
        selector = TradingModeSelector()

        universal_plan = selector.get_plan_for_mode(
            TradingStrategyMode.UNIVERSAL,
        )

        assert universal_plan.mode == TradingStrategyMode.UNIVERSAL
