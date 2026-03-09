"""TradingPlan.create_universal()ファクトリのユニットテスト"""

from __future__ import annotations


from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.mode_selector import TradingPlan


class TestCreateUniversal:
    """TradingPlan.create_universal()のテスト"""

    def test_default_config(self) -> None:
        """デフォルトconfigでUNIVERSALプラン生成"""
        plan = TradingPlan.create_universal()
        assert plan.mode == "UNIVERSAL"
        assert plan.primary_tf == "M15"
        assert plan.entry_tf == "M5"
        assert plan.manage_tf == "M15"
        assert plan.max_holding_bars == 32
        assert plan.tp_sl_ratio_range == (1.1, 1.4)

    def test_custom_config(self) -> None:
        """カスタムconfigでUNIVERSALプラン生成"""
        config = UnifiedBotConfig(
            default_primary_tf="H1",
            default_entry_tf="M15",
            default_manage_tf="H1",
            default_max_holding_bars=48,
            default_tp_sl_ratio_range=(1.2, 1.6),
        )
        plan = TradingPlan.create_universal(config)
        assert plan.primary_tf == "H1"
        assert plan.entry_tf == "M15"
        assert plan.manage_tf == "H1"
        assert plan.max_holding_bars == 48
        assert plan.tp_sl_ratio_range == (1.2, 1.6)

    def test_confirm_tfs_auto_derived(self) -> None:
        """confirm_tfsはtimeframesからprimary/entryを除外して導出"""
        config = UnifiedBotConfig(
            timeframes=["M5", "M15", "H1", "H4", "D1"],
            default_primary_tf="M15",
            default_entry_tf="M5",
        )
        plan = TradingPlan.create_universal(config)
        assert "M5" not in plan.confirm_tfs
        assert "M15" not in plan.confirm_tfs
        assert "H1" in plan.confirm_tfs
        assert "H4" in plan.confirm_tfs
        assert "D1" in plan.confirm_tfs

    def test_selection_reason(self) -> None:
        """選択理由にUNIVERSALが含まれる"""
        plan = TradingPlan.create_universal()
        assert "UNIVERSAL" in plan.selection_reason

    def test_none_config_uses_defaults(self) -> None:
        """config=NoneでデフォルトUnifiedBotConfigを使用"""
        plan = TradingPlan.create_universal(None)
        assert plan.mode == "UNIVERSAL"
        assert plan.primary_tf == "M15"


class TestUnifiedBotConfigNewFields:
    """UnifiedBotConfigの新フィールドテスト"""

    def test_default_trading_plan_fields(self) -> None:
        """TradingPlanデフォルト設定のデフォルト値"""
        config = UnifiedBotConfig()
        assert config.default_primary_tf == "M15"
        assert config.default_entry_tf == "M5"
        assert config.default_manage_tf == "M15"
        assert config.default_max_holding_bars == 32
        assert config.default_tp_sl_ratio_range == (1.1, 1.4)

    def test_default_filter_thresholds(self) -> None:
        """フィルター閾値のデフォルト値"""
        config = UnifiedBotConfig()
        assert config.macd_slope_filter_threshold == -2.0
        assert config.sl_min_pips == 20.0
        assert config.sl_max_pips_default == 50.0

    def test_default_consensus_weights(self) -> None:
        """コンセンサス重みのデフォルト値（M1最適化No.3）"""
        config = UnifiedBotConfig()
        assert config.consensus_primary_weight == 2.0
        assert config.consensus_entry_weight == 1.5
        assert config.consensus_confirm_weight == 3.0
        assert config.consensus_manage_weight == 0.5
        assert config.consensus_other_weight == 1.0

    def test_custom_consensus_weights(self) -> None:
        """カスタムコンセンサス重み"""
        config = UnifiedBotConfig(
            consensus_primary_weight=4.0,
            consensus_entry_weight=3.0,
        )
        assert config.consensus_primary_weight == 4.0
        assert config.consensus_entry_weight == 3.0
