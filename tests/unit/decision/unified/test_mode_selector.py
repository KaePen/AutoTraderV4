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
    """TradingModeSelectorのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.selector = TradingModeSelector()

    def test_high_vol_selects_scalping(self) -> None:
        """高ボラティリティでSCALPING選択（アクティブ時間帯）"""
        plan = self.selector.select(
            regime=MarketRegime.HIGH_VOL,
            volatility_level=1.5,
            htf_alignment=0.5,
            hour_utc=8,  # ロンドンアクティブ時間帯
        )

        assert plan.mode == TradingStrategyMode.SCALPING
        assert plan.primary_tf == "M5"
        assert plan.entry_tf == "M1"

    def test_strong_trend_high_alignment_selects_swing(self) -> None:
        """強トレンド＋高整合でSWING選択"""
        plan = self.selector.select(
            regime=MarketRegime.TREND,
            volatility_level=1.0,
            htf_alignment=0.7,  # 高整合
        )

        assert plan.mode == TradingStrategyMode.SWING
        assert plan.primary_tf == "H4"

    def test_trend_low_alignment_selects_swing(self) -> None:
        """トレンド＋低整合でもSWING選択（P0-2）"""
        plan = self.selector.select(
            regime=MarketRegime.TREND,
            volatility_level=1.0,
            htf_alignment=0.2,  # 低整合
        )

        # P0-2: TREND→常にSWING
        assert plan.mode == TradingStrategyMode.SWING
        assert plan.primary_tf == "H4"

    def test_range_selects_day_trade(self) -> None:
        """レンジでDAY_TRADE選択"""
        plan = self.selector.select(
            regime=MarketRegime.RANGE,
            volatility_level=1.0,
            htf_alignment=0.5,
        )

        assert plan.mode == TradingStrategyMode.DAY_TRADE

    def test_low_vol_selects_day_trade(self) -> None:
        """低ボラでDAY_TRADE選択"""
        plan = self.selector.select(
            regime=MarketRegime.LOW_VOL,
            volatility_level=0.5,
            htf_alignment=0.5,
        )

        assert plan.mode == TradingStrategyMode.DAY_TRADE

    def test_high_volatility_level_overrides(self) -> None:
        """高ボラティリティレベルでSCALPING"""
        plan = self.selector.select(
            regime=MarketRegime.TREND,  # TRENDでも
            volatility_level=1.5,       # 高ボラ
            htf_alignment=0.7,
        )

        assert plan.mode == TradingStrategyMode.SCALPING


class TestTradingPlan:
    """TradingPlanのテスト"""

    def test_all_tfs_property(self) -> None:
        """all_tfsプロパティ"""
        plan = TradingPlan(
            mode=TradingStrategyMode.DAY_TRADE,
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
            mode=TradingStrategyMode.DAY_TRADE,
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

    def test_custom_config(self) -> None:
        """カスタム設定"""
        config = ModeSelectorConfig(
            high_vol_threshold=1.5,
            htf_alignment_threshold=0.3,
            prefer_swing_on_strong_trend=False,
        )
        selector = TradingModeSelector(config)

        # P0-2: TREND→常にSWING（prefer_swingに関係なく）
        plan = selector.select(
            regime=MarketRegime.TREND,
            volatility_level=1.0,
            htf_alignment=0.9,
        )

        assert plan.mode == TradingStrategyMode.SWING

    def test_get_plan_for_mode(self) -> None:
        """モード指定でプラン取得"""
        selector = TradingModeSelector()

        scalp_plan = selector.get_plan_for_mode(TradingStrategyMode.SCALPING)
        day_plan = selector.get_plan_for_mode(TradingStrategyMode.DAY_TRADE)
        swing_plan = selector.get_plan_for_mode(TradingStrategyMode.SWING)

        assert scalp_plan.mode == TradingStrategyMode.SCALPING
        assert day_plan.mode == TradingStrategyMode.DAY_TRADE
        assert swing_plan.mode == TradingStrategyMode.SWING

        # 各モードで異なるTF
        assert scalp_plan.primary_tf != swing_plan.primary_tf
