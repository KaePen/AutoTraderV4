"""ModeAwareScoreConsensusのユニットテスト"""

from __future__ import annotations


from autotrader.core.enums import SignalType
from autotrader.decision.unified.scoring.consensus import (
    ConsensusConfig,
    ModeAwareScoreConsensus,
)
from autotrader.decision.unified.scoring.timeframe_evaluator import (
    TimeframeSignal,
)
from autotrader.decision.unified.mode_selector import TradingPlan


def _make_signal(
    tf: str,
    direction: SignalType,
    strength: float,
    sl_pips: float = 15.0,
    tp_pips: float = 30.0,
) -> TimeframeSignal:
    """テスト用 TimeframeSignal を簡易作成する

    Args:
        tf: 時間足
        direction: シグナル方向
        strength: 純強度（0-1）
        sl_pips: SL pips
        tp_pips: TP pips

    Returns:
        TimeframeSignal: テスト用シグナル
    """
    if direction == SignalType.BUY:
        buy_s, sell_s = strength, 0.0
    elif direction == SignalType.SELL:
        buy_s, sell_s = 0.0, strength
    else:
        buy_s, sell_s = 0.0, 0.0
    return TimeframeSignal(
        timeframe=tf,
        direction=direction,
        buy_strength=buy_s,
        sell_strength=sell_s,
        confidence=strength,
        sl_pips=sl_pips,
        tp_pips=tp_pips,
        reason="test",
    )


class TestModeAwareScoreConsensus:
    """ModeAwareScoreConsensusのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.consensus = ModeAwareScoreConsensus()
        self.universal_plan = TradingPlan(
            mode="UNIVERSAL",
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
            "M5": _make_signal("M5", SignalType.BUY, 0.8, 15.0, 30.0),
            "M15": _make_signal("M15", SignalType.BUY, 0.9, 20.0, 40.0),
            "H1": _make_signal("H1", SignalType.BUY, 0.7, 25.0, 50.0),
            "H4": _make_signal("H4", SignalType.BUY, 0.6, 30.0, 60.0),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        assert result.direction == SignalType.BUY
        assert result.score > result.threshold
        assert len(result.aligned_tfs) == 4

    def test_sell_consensus(self) -> None:
        """SELLコンセンサス"""
        signals = {
            "M5": _make_signal("M5", SignalType.SELL, 0.8, 15.0, 30.0),
            "M15": _make_signal("M15", SignalType.SELL, 0.9, 20.0, 40.0),
            "H1": _make_signal("H1", SignalType.SELL, 0.7, 25.0, 50.0),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        assert result.direction == SignalType.SELL
        assert "SELL" in result.reasoning

    def test_hold_on_low_score(self) -> None:
        """スコア不足でHOLD"""
        signals = {
            "M5": _make_signal("M5", SignalType.BUY, 0.3, 15.0, 30.0),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        assert result.direction == SignalType.HOLD
        assert "スコア不足" in result.reasoning

    def test_hold_on_mixed_signals(self) -> None:
        """方向混在でHOLD"""
        signals = {
            "M5": _make_signal("M5", SignalType.BUY, 0.5, 15.0, 30.0),
            "M15": _make_signal(
                "M15", SignalType.SELL, 0.5, 20.0, 40.0,
            ),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        # 相殺されてスコア不足
        assert result.direction == SignalType.HOLD

    def test_primary_tf_weight(self) -> None:
        """primary_tfの重み付け"""
        signals = {
            "M15": _make_signal(
                "M15", SignalType.BUY, 1.0, 20.0, 40.0,
            ),
            "M5": _make_signal("M5", SignalType.BUY, 0.8, 15.0, 30.0),
            "H1": _make_signal("H1", SignalType.BUY, 0.5, 25.0, 50.0),
        }

        result = self.consensus.consolidate(signals, self.universal_plan)

        # primary_tf(3.0*1.0) + entry_tf(2.0*0.8) + H1(1.5*0.5) = 5.35
        assert result.direction == SignalType.BUY


class TestUniversalModeConsensus:
    """UNIVERSALモードのコンセンサステスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.consensus = ModeAwareScoreConsensus()
        self.universal_plan = TradingPlan(
            mode="UNIVERSAL",
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
            "UNIVERSAL"
        )
        assert threshold == 4.5

    def test_universal_consolidate_uses_threshold_4_5(self) -> None:
        """UNIVERSALモードのconsolidateでthreshold=4.5が使われる"""
        signals = {
            "M5": _make_signal("M5", SignalType.BUY, 0.5, 10.0, 20.0),
        }
        result = self.consensus.consolidate(signals, self.universal_plan)
        assert result.threshold == 4.5

    def test_universal_mode_custom_threshold(self) -> None:
        """UNIVERSALモードのカスタム閾値"""
        config = ConsensusConfig(threshold=3.0)
        consensus = ModeAwareScoreConsensus(config)
        threshold = consensus.get_threshold_for_mode(
            "UNIVERSAL"
        )
        assert threshold == 3.0


class TestConsensusConfig:
    """ConsensusConfigのテスト"""

    def test_custom_weights(self) -> None:
        """カスタム重み"""
        config = ConsensusConfig(
            primary_weight=5.0,
            entry_weight=1.0,
            confirm_weight=1.0,
        )
        consensus = ModeAwareScoreConsensus(config)

        plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
        )

        signals = {
            "M15": _make_signal(
                "M15", SignalType.BUY, 0.8, 20.0, 40.0,
            ),
        }

        result = consensus.consolidate(signals, plan)

        # 高い重み(5.0) * direction(1) * strength(0.8) * decay
        assert result.score >= 2.0

    def test_custom_thresholds(self) -> None:
        """カスタム閾値"""
        config = ConsensusConfig(
            threshold=2.0,
        )
        consensus = ModeAwareScoreConsensus(config)

        threshold = consensus.get_threshold_for_mode(
            "UNIVERSAL",
        )

        assert threshold == 2.0
