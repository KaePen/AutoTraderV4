"""ModeAwareScoreConsensusのユニットテスト"""

from __future__ import annotations

import pytest

from autotrader.core.enums import SignalType, TradingStrategyMode
from autotrader.decision.unified.mode_aware_consensus import (
    ConsensusConfig,
    ModeAwareScoreConsensus,
    TimeframeSignal,
)
from autotrader.decision.unified.mode_selector import TradingPlan


class TestModeAwareScoreConsensus:
    """ModeAwareScoreConsensusのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.consensus = ModeAwareScoreConsensus()
        self.universal_plan = TradingPlan(
            mode=TradingStrategyMode.UNIVERSAL,
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1", "H4"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
        )

    def test_buy_consensus(self) -> None:
        """BUYコンセンサス"""
        signals = {
            "M5": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.8,
                sl_pips=15.0,
                tp_pips=30.0,
            ),
            "M15": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.9,
                sl_pips=20.0,
                tp_pips=40.0,
            ),
            "H1": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.7,
                sl_pips=25.0,
                tp_pips=50.0,
            ),
            "H4": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.6,
                sl_pips=30.0,
                tp_pips=60.0,
            ),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        assert result.direction == SignalType.BUY
        assert result.score > result.threshold
        assert len(result.aligned_tfs) == 4

    def test_sell_consensus(self) -> None:
        """SELLコンセンサス"""
        signals = {
            "M5": TimeframeSignal(
                direction=SignalType.SELL,
                strength=0.8,
                sl_pips=15.0,
                tp_pips=30.0,
            ),
            "M15": TimeframeSignal(
                direction=SignalType.SELL,
                strength=0.9,
                sl_pips=20.0,
                tp_pips=40.0,
            ),
            "H1": TimeframeSignal(
                direction=SignalType.SELL,
                strength=0.7,
                sl_pips=25.0,
                tp_pips=50.0,
            ),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        assert result.direction == SignalType.SELL
        assert "SELL" in result.reasoning

    def test_hold_on_low_score(self) -> None:
        """スコア不足でHOLD"""
        signals = {
            "M5": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.3,  # 低強度
                sl_pips=15.0,
                tp_pips=30.0,
            ),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        assert result.direction == SignalType.HOLD
        assert "スコア不足" in result.reasoning

    def test_hold_on_mixed_signals(self) -> None:
        """方向混在でHOLD"""
        signals = {
            "M5": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.5,
                sl_pips=15.0,
                tp_pips=30.0,
            ),
            "M15": TimeframeSignal(
                direction=SignalType.SELL,  # 逆方向
                strength=0.5,
                sl_pips=20.0,
                tp_pips=40.0,
            ),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        # 相殺されてスコア不足
        assert result.direction == SignalType.HOLD

    def test_primary_tf_weight(self) -> None:
        """primary_tfの重み付け"""
        # primary_tf (M15) とentry_tf (M5) でBUY
        signals = {
            "M15": TimeframeSignal(
                direction=SignalType.BUY,
                strength=1.0,  # 最大強度
                sl_pips=20.0,
                tp_pips=40.0,
            ),
            "M5": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.8,
                sl_pips=15.0,
                tp_pips=30.0,
            ),
            "H1": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.5,
                sl_pips=25.0,
                tp_pips=50.0,
            ),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        # primary_tf(3.0×1.0=3.0) + entry_tf(2.0×0.8=1.6) + H1(1.5×0.5=0.75) = 5.35 > 4.0
        assert result.direction == SignalType.BUY


class TestUniversalModeConsensus:
    """UNIVERSALモードのコンセンサステスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.consensus = ModeAwareScoreConsensus()
        self.universal_plan = TradingPlan(
            mode=TradingStrategyMode.UNIVERSAL,
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["M1", "H1", "H4", "H8", "D1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.1, 1.4),
        )

    def test_universal_threshold_is_4_5(self) -> None:
        """UNIVERSALモードの閾値は4.5"""
        threshold = self.consensus.get_threshold_for_mode(
            TradingStrategyMode.UNIVERSAL
        )
        assert threshold == 4.5

    def test_universal_consolidate_uses_threshold_4_5(self) -> None:
        """UNIVERSALモードのconsolidateでthreshold=4.5が使われる"""
        signals = {
            "M5": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.5,
                sl_pips=10.0,
                tp_pips=20.0,
            ),
        }
        result = self.consensus.consolidate(signals, self.universal_plan)
        assert result.threshold == 4.5

    def test_universal_mode_custom_threshold(self) -> None:
        """UNIVERSALモードのカスタム閾値"""
        config = ConsensusConfig(threshold=3.0)
        consensus = ModeAwareScoreConsensus(config)
        threshold = consensus.get_threshold_for_mode(
            TradingStrategyMode.UNIVERSAL
        )
        assert threshold == 3.0


class TestConsensusConfig:
    """ConsensusConfigのテスト"""

    def test_custom_weights(self) -> None:
        """カスタム重み"""
        config = ConsensusConfig(
            primary_weight=5.0,  # 高い重み
            entry_weight=1.0,
            confirm_weight=1.0,
        )
        consensus = ModeAwareScoreConsensus(config)

        plan = TradingPlan(
            mode=TradingStrategyMode.UNIVERSAL,
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
        )

        signals = {
            "M15": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.8,
                sl_pips=20.0,
                tp_pips=40.0,
            ),
        }

        result = consensus.consolidate(signals, plan)

        # 高い重み(5.0) * direction(1) * strength(0.8) * decay = 2.4
        # decayファクターにより減少
        assert result.score >= 2.0

    def test_custom_thresholds(self) -> None:
        """カスタム閾値"""
        config = ConsensusConfig(
            threshold=2.0,  # 低い閾値
        )
        consensus = ModeAwareScoreConsensus(config)

        threshold = consensus.get_threshold_for_mode(
            TradingStrategyMode.UNIVERSAL,
        )

        assert threshold == 2.0
