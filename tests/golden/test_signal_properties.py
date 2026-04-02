"""シグナル不変条件のプロパティベーステスト

hypothesis を使って、シグナル生成ロジックの不変条件を検証する。
任意のパラメータ組み合わせでも成立すべき性質をテストする。
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from autotrader.decision.unified.config import (
    FilterConfig,
    RiskManagementConfig,
    SignalConfig,
    UnifiedBotConfig,
)
from autotrader.decision.unified.risk.position_manager import (
    PositionManagerConfig,
)


class TestConfigInvariants:
    """Config値の不変条件テスト"""

    @given(
        consensus_threshold=st.floats(
            min_value=5.0, max_value=15.0,
            allow_nan=False, allow_infinity=False,
        ),
    )
    @settings(max_examples=50)
    def test_consensus_threshold_positive(
        self, consensus_threshold: float,
    ) -> None:
        """consensus_thresholdは常に正"""
        config = UnifiedBotConfig(
            consensus_threshold=consensus_threshold,
        )
        assert config.consensus_threshold > 0

    @given(
        bca_min_edge=st.floats(
            min_value=0.0, max_value=1.0,
            allow_nan=False, allow_infinity=False,
        ),
    )
    @settings(max_examples=50)
    def test_bca_min_edge_in_range(
        self, bca_min_edge: float,
    ) -> None:
        """bca_min_edgeは0.0〜1.0の範囲内"""
        config = UnifiedBotConfig(bca_min_edge=bca_min_edge)
        assert 0.0 <= config.bca_min_edge <= 1.0

    @given(
        sl_min=st.floats(
            min_value=1.0, max_value=50.0,
            allow_nan=False, allow_infinity=False,
        ),
        sl_max=st.floats(
            min_value=50.0, max_value=200.0,
            allow_nan=False, allow_infinity=False,
        ),
    )
    @settings(max_examples=50)
    def test_sl_min_less_than_max(
        self, sl_min: float, sl_max: float,
    ) -> None:
        """SL最小値は最大値以下"""
        config = UnifiedBotConfig(
            sl_min_pips=sl_min,
            sl_max_pips_default=sl_max,
        )
        assert config.sl_min_pips <= config.sl_max_pips_default

    @given(
        penalty_cap=st.floats(
            min_value=0.0, max_value=1.0,
            allow_nan=False, allow_infinity=False,
        ),
    )
    @settings(max_examples=50)
    def test_penalty_cap_in_range(
        self, penalty_cap: float,
    ) -> None:
        """penalty_capは0.0〜1.0の範囲内"""
        config = UnifiedBotConfig(penalty_cap=penalty_cap)
        assert 0.0 <= config.penalty_cap <= 1.0


class TestSignalConfigExtraction:
    """to_signal_config()の不変条件テスト"""

    @given(
        threshold=st.floats(
            min_value=1.0, max_value=20.0,
            allow_nan=False, allow_infinity=False,
        ),
        bca_edge=st.floats(
            min_value=0.0, max_value=1.0,
            allow_nan=False, allow_infinity=False,
        ),
    )
    @settings(max_examples=50)
    def test_signal_config_preserves_values(
        self, threshold: float, bca_edge: float,
    ) -> None:
        """to_signal_config()は値を保持する"""
        config = UnifiedBotConfig(
            consensus_threshold=threshold,
            bca_min_edge=bca_edge,
        )
        signal = config.to_signal_config()
        assert signal.consensus_threshold == config.consensus_threshold
        assert signal.bca_min_edge == config.bca_min_edge
        assert signal.bca_enabled == config.bca_enabled

    def test_risk_config_preserves_values(self) -> None:
        """to_risk_management_config()は値を保持する"""
        config = UnifiedBotConfig(
            sl_min_pips=25.0,
            penalty_cap=0.4,
            max_positions=5,
        )
        risk = config.to_risk_management_config()
        assert risk.sl_min_pips == 25.0
        assert risk.penalty_cap == 0.4
        assert risk.max_positions == 5

    def test_filter_config_preserves_values(self) -> None:
        """to_filter_config()は値を保持する"""
        config = UnifiedBotConfig(
            weak_hours_enabled=False,
            regime_threshold_enabled=False,
        )
        filt = config.to_filter_config()
        assert filt.weak_hours_enabled is False
        assert filt.regime_threshold_enabled is False


class TestFromSubConfigs:
    """from_sub_configs() 往復変換テスト"""

    def test_roundtrip_signal(self) -> None:
        """SignalConfig → UnifiedBotConfig 往復で値保持"""
        original = UnifiedBotConfig(
            consensus_threshold=10.0,
            bca_min_edge=0.65,
        )
        signal = original.to_signal_config()
        rebuilt = UnifiedBotConfig.from_sub_configs(signal=signal)
        assert rebuilt.consensus_threshold == 10.0
        assert rebuilt.bca_min_edge == 0.65

    def test_roundtrip_risk(self) -> None:
        """RiskManagementConfig → UnifiedBotConfig 往復で値保持"""
        original = UnifiedBotConfig(
            sl_min_pips=15.0,
            max_positions=5,
        )
        risk = original.to_risk_management_config()
        rebuilt = UnifiedBotConfig.from_sub_configs(risk_mgmt=risk)
        assert rebuilt.sl_min_pips == 15.0
        assert rebuilt.max_positions == 5

    def test_roundtrip_all(self) -> None:
        """全サブConfig往復で値保持"""
        original = UnifiedBotConfig(
            consensus_threshold=8.5,
            sl_min_pips=18.0,
            weak_hours_enabled=False,
        )
        rebuilt = UnifiedBotConfig.from_sub_configs(
            signal=original.to_signal_config(),
            risk_mgmt=original.to_risk_management_config(),
            filter_cfg=original.to_filter_config(),
        )
        assert rebuilt.consensus_threshold == 8.5
        assert rebuilt.sl_min_pips == 18.0
        assert rebuilt.weak_hours_enabled is False


class TestPositionManagerConfigInvariants:
    """PositionManagerConfig の不変条件テスト"""

    @given(
        trailing_r=st.floats(
            min_value=0.1, max_value=5.0,
            allow_nan=False, allow_infinity=False,
        ),
    )
    @settings(max_examples=50)
    def test_trailing_start_r_positive(
        self, trailing_r: float,
    ) -> None:
        """trailing_start_rは常に正"""
        config = PositionManagerConfig(trailing_start_r=trailing_r)
        assert config.trailing_start_r > 0

    @given(
        stag_minutes=st.floats(
            min_value=10.0, max_value=500.0,
            allow_nan=False, allow_infinity=False,
        ),
    )
    @settings(max_examples=50)
    def test_stagnation_minutes_positive(
        self, stag_minutes: float,
    ) -> None:
        """stagnation_exit_minutesは常に正"""
        config = PositionManagerConfig(
            stagnation_exit_minutes=stag_minutes,
        )
        assert config.stagnation_exit_minutes > 0

    def test_partial_close_ratios_non_negative(self) -> None:
        """部分決済比率は非負"""
        config = PositionManagerConfig()
        assert config.partial_close_1r_ratio >= 0
        assert config.partial_close_2r_ratio >= 0
        assert config.range_day_half_r_partial_ratio >= 0
        assert config.universal_half_r_ratio >= 0

    def test_insurance_trigger_positive(self) -> None:
        """保険トリガーR値は正"""
        config = PositionManagerConfig()
        assert config.insurance_trigger_r > 0
