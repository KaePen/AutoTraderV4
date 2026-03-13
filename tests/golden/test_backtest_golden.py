"""Config回帰テスト

デフォルト設定値が意図せず変更されていないことを検証する。
Phase 3 regression (-44%劣化) のような事態を早期検出するための
ゴールデンテスト。
"""

from __future__ import annotations

import pytest

from autotrader.config.trading_params import SymbolPreset, get_preset
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.position_manager import (
    PositionManagerConfig,
)


class TestUnifiedBotConfigDefaults:
    """UnifiedBotConfig デフォルト値の回帰テスト"""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.config = UnifiedBotConfig()

    # --- コンセンサス・シグナル設定 ---

    def test_consensus_threshold(self) -> None:
        assert self.config.consensus_threshold == 9.0

    def test_consensus_weights(self) -> None:
        assert self.config.consensus_primary_weight == 2.0
        assert self.config.consensus_entry_weight == 1.5
        assert self.config.consensus_confirm_weight == 3.0
        assert self.config.consensus_manage_weight == 0.5
        assert self.config.consensus_other_weight == 1.0

    # --- BCA設定 ---

    def test_bca_enabled(self) -> None:
        assert self.config.bca_enabled is True

    def test_bca_min_edge(self) -> None:
        assert self.config.bca_min_edge == 0.60

    def test_bca_penalty_scale(self) -> None:
        assert self.config.bca_penalty_scale == 1.0

    # --- リスク管理設定 ---

    def test_sl_min_pips(self) -> None:
        assert self.config.sl_min_pips == 20.0

    def test_sl_max_pips_default(self) -> None:
        assert self.config.sl_max_pips_default == 50.0

    def test_penalty_cap(self) -> None:
        assert self.config.penalty_cap == 0.3

    def test_max_positions(self) -> None:
        assert self.config.max_positions == 3

    def test_tp_sl_ratio_range(self) -> None:
        assert self.config.default_tp_sl_ratio_range == (1.1, 1.4)

    # --- ポジション管理設定 ---

    def test_use_dynamic_lot(self) -> None:
        assert self.config.use_dynamic_lot is True

    def test_use_position_manager(self) -> None:
        assert self.config.use_position_manager is True

    def test_base_risk_pct(self) -> None:
        assert self.config.base_risk_pct == 0.04

    def test_max_lot_per_trade(self) -> None:
        assert self.config.max_lot_per_trade == 5.0

    def test_slippage_buffer_pips(self) -> None:
        assert self.config.slippage_buffer_pips == 2.0

    # --- レジーム・フィルター設定 ---

    def test_regime_threshold_enabled(self) -> None:
        assert self.config.regime_threshold_enabled is True

    def test_regime_trend_threshold_add(self) -> None:
        assert self.config.regime_trend_threshold_add == 1.5

    def test_htf_score_filter_enabled(self) -> None:
        assert self.config.htf_score_filter_enabled is True

    def test_htf_score_filter_threshold_add(self) -> None:
        assert self.config.htf_score_filter_threshold_add == 1.0

    # --- SoftGuardペナルティ ---

    def test_sg_penalties(self) -> None:
        assert self.config.sg_spread_penalty_rate == 0.2
        assert self.config.sg_off_hours_penalty == 0.25
        assert self.config.sg_volatility_penalty == 0.05
        assert self.config.sg_recent_loss_penalty == 0.1

    # --- Phase3構造的改善（デフォルトOFF確認）---

    def test_phase3_features_off_by_default(self) -> None:
        """Phase3で劣化を引き起こしたフィーチャーがOFF"""
        assert self.config.session_transition_wait_enabled is False
        assert self.config.liquidity_based_tp_enabled is False

    # --- ファンダメンタル設定（デフォルトOFF確認）---

    def test_fundamental_disabled_by_default(self) -> None:
        """ファンダメンタル機能はデフォルトOFF"""
        assert self.config.fundamental_assessor_enabled is False
        assert self.config.fundamental_softguard_enabled is False
        assert self.config.fundamental_pm_enabled is False

    # --- 改善検証パラメータ（デフォルトOFF確認）---

    def test_improvement_params_off_by_default(self) -> None:
        assert self.config.off_hours_trend_block is False
        assert self.config.off_hours_high_align_block is False
        assert self.config.trend_sl_min_pips is None
        assert self.config.trend_sl_max_pips is None
        assert self.config.high_align_penalty_threshold is None

    # --- M1マイクロ反転フィルタ（デフォルトOFF確認）---

    def test_m1_micro_reversal_disabled_by_default(
        self,
    ) -> None:
        """M1マイクロ反転フィルタはデフォルトOFF"""
        assert self.config.m1_micro_reversal_enabled is False

    def test_m1_micro_reversal_default_thresholds(
        self,
    ) -> None:
        """M1マイクロ反転フィルタのデフォルト閾値"""
        assert self.config.m1_micro_reversal_bb_extreme == 0.90
        assert self.config.m1_micro_reversal_stoch_extreme == 80.0
        assert self.config.m1_micro_reversal_roc_atr_extreme == 1.5
        assert self.config.m1_micro_reversal_roc_lookback == 5
        assert self.config.m1_micro_reversal_min_signals == 2

    # --- 時間足設定 ---

    def test_default_timeframes(self) -> None:
        expected = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]
        assert self.config.timeframes == expected

    def test_htf_alignment_tfs(self) -> None:
        assert self.config.htf_alignment_tfs == ["H4", "D1"]

    def test_regime_detection_tf(self) -> None:
        assert self.config.regime_detection_tf == "H1"


class TestSymbolPresetValues:
    """通貨ペアプリセットの回帰テスト"""

    def test_usdjpy_preset(self) -> None:
        """USDJPYプリセット（主要パラメータ）"""
        p = get_preset("USDJPY")
        assert p.pip_value == 100.0
        assert p.spread_pips == 1.5
        assert p.slippage_pips == 0.5
        assert p.default_sl_pips == 20.0
        assert p.default_tp_pips == 40.0
        assert p.max_positions == 1
        assert p.bonus_max_positions == 0
        assert p.base_risk_pct == 0.003
        assert p.max_lot_per_trade == 2.0
        assert p.max_total_exposure_lot == 4.0
        assert p.use_position_manager is True

    def test_eurjpy_preset(self) -> None:
        """EURJPYプリセット"""
        p = get_preset("EURJPY")
        assert p.pip_value == 100.0
        assert p.spread_pips == 2.0
        assert p.slippage_pips == 0.7
        assert p.default_sl_pips == 25.0
        assert p.default_tp_pips == 50.0
        assert p.max_positions == 1
        assert p.base_risk_pct == 0.003

    def test_gbpjpy_preset(self) -> None:
        """GBPJPYプリセット（高スプレッド通貨ペア）"""
        p = get_preset("GBPJPY")
        assert p.spread_pips == 3.0
        assert p.max_positions == 1
        assert p.base_risk_pct == 0.003

    def test_eurusd_preset(self) -> None:
        """EURUSDプリセット（USDクオート）"""
        p = get_preset("EURUSD")
        assert p.pip_value == 10.0
        assert p.spread_pips == 1.0

    def test_undefined_symbol_returns_default(self) -> None:
        """未定義シンボルはデフォルト値を返す"""
        p = get_preset("XYZABC")
        default = SymbolPreset(symbol="XYZABC")
        assert p.spread_pips == default.spread_pips
        assert p.pip_value == default.pip_value

    def test_preset_immutability(self) -> None:
        """プリセットはfrozen dataclass"""
        p = get_preset("USDJPY")
        with pytest.raises(AttributeError):
            p.spread_pips = 999.0  # type: ignore[misc]


class TestPositionManagerConfigDefaults:
    """PositionManagerConfig デフォルト値の回帰テスト"""

    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        self.config = PositionManagerConfig()

    def test_partial_close_ratios(self) -> None:
        assert self.config.partial_close_1r_ratio == 0.05
        assert self.config.partial_close_2r_ratio == 0.05

    def test_trailing_settings(self) -> None:
        assert self.config.trailing_start_r == 0.5
        assert self.config.trailing_atr_multiplier == 2.0

    def test_breakeven_settings(self) -> None:
        assert self.config.breakeven_at_1r is True
        assert self.config.be_cushion_pips == 3.0

    def test_stagnation_settings(self) -> None:
        assert self.config.stagnation_exit_minutes == 120.0
        assert self.config.stagnation_min_mfe_r == 0.10

    def test_very_early_exit_disabled(self) -> None:
        """very_early_exitはデフォルトOFF（Phase3最大犯人）"""
        assert self.config.very_early_exit_enabled is False

    def test_profit_reversal_disabled(self) -> None:
        """profit_reversalはデフォルトOFF（exit簡素化で+68%改善）"""
        assert self.config.profit_reversal_enabled is False
        assert self.config.profit_reversal_mfe_r == 0.3
        assert self.config.profit_reversal_drop_r == 0.25

    def test_progressive_stagnation_disabled(self) -> None:
        """段階的STAGNATIONはデフォルトOFF"""
        assert self.config.progressive_stagnation_enabled is False

    def test_range_day_settings(self) -> None:
        assert self.config.range_day_be_disabled is True
        assert self.config.range_day_fast_be_enabled is True
        # exit簡素化: insurance/half_r OFF
        assert self.config.range_day_insurance_enabled is False
        assert self.config.range_day_half_r_partial_enabled is False

    def test_stag_override_none_by_default(self) -> None:
        """レジーム別stagnation上書きはデフォルトNone"""
        assert self.config.stag_trend_minutes is None
        assert self.config.stag_range_minutes is None
