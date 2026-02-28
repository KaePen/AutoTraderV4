"""方向性エッジ評価器（BCA v2 ハイブリッド）のユニットテスト

v2: コンセンサスベースのハードゲート + 個別TFベースのペナルティ。
"""

from __future__ import annotations

import pytest

from autotrader.core.enums import SignalType
from autotrader.decision.unified.directional_edge import (
    DirectionalEdgeAssessor,
    DirectionalEdgeResult,
)
from autotrader.decision.unified.mode_aware_consensus import (
    ConsensusResult,
)
from autotrader.decision.unified.timeframe_evaluator import (
    TimeframeSignal,
)
from autotrader.decision.unified.timeframe_router import (
    TimeframeSet,
)


# --- ヘルパー ---

def _make_consensus(
    buy_score: float,
    sell_score: float,
    direction: SignalType = SignalType.BUY,
) -> ConsensusResult:
    """テスト用ConsensusResult生成"""
    return ConsensusResult(
        direction=direction,
        score=max(buy_score, sell_score),
        threshold=8.0,
        aligned_tfs=["M15", "H1"],
        reasoning="テスト",
        buy_score=buy_score,
        sell_score=sell_score,
    )


def _make_tf_signal(
    tf: str,
    direction: SignalType,
    buy_strength: float = 0.0,
    sell_strength: float = 0.0,
) -> TimeframeSignal:
    """テスト用TimeframeSignal生成"""
    return TimeframeSignal(
        timeframe=tf,
        direction=direction,
        buy_strength=buy_strength,
        sell_strength=sell_strength,
        confidence=0.5,
        sl_pips=20.0,
        tp_pips=30.0,
        reason="テスト",
    )


def _default_tf_set() -> TimeframeSet:
    """テスト用TimeframeSet"""
    return TimeframeSet(
        primary_tf="M15",
        entry_tf="M5",
        confirm_tfs=("H1", "H4"),
        manage_tf="M15",
    )


# --- 基本テスト ---

class TestDirectionalEdgeAssessor:
    """DirectionalEdgeAssessorの基本テスト"""

    def test_strong_buy_passes(self):
        """強いBUYシグナルはパスする"""
        assessor = DirectionalEdgeAssessor(min_edge=0.25)
        # edge = (10 - 2) / (10 + 2) ≈ 0.667
        consensus = _make_consensus(
            buy_score=10.0, sell_score=2.0,
        )
        tf_signals = {
            "M5": _make_tf_signal(
                "M5", SignalType.BUY,
                buy_strength=0.8, sell_strength=0.1,
            ),
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.7, sell_strength=0.2,
            ),
            "H1": _make_tf_signal(
                "H1", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.1,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.passed is True
        assert result.directional_edge == pytest.approx(
            0.667, abs=0.01,
        )

    def test_balanced_signals_blocked(self):
        """拮抗シグナルはブロックされる"""
        assessor = DirectionalEdgeAssessor(min_edge=0.25)
        # edge = (6.0 - 5.5) / (6.0 + 5.5) ≈ 0.043
        consensus = _make_consensus(
            buy_score=6.0, sell_score=5.5,
        )
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.5, sell_strength=0.4,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.passed is False
        assert "BCAブロック" in result.reasoning

    def test_zero_scores_blocked(self):
        """スコア合計が0の場合ブロック"""
        assessor = DirectionalEdgeAssessor()
        consensus = _make_consensus(
            buy_score=0.0, sell_score=0.0,
            direction=SignalType.HOLD,
        )
        result = assessor.assess(
            consensus, {}, _default_tf_set(),
        )
        assert result.passed is False
        assert result.directional_edge == 0.0

    def test_one_direction_only(self):
        """片方向のみのスコアでedge=1.0"""
        assessor = DirectionalEdgeAssessor(min_edge=0.25)
        consensus = _make_consensus(
            buy_score=8.0, sell_score=0.0,
        )
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.8, sell_strength=0.0,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.passed is True
        assert result.directional_edge == 1.0
        assert result.opposition_ratio == 0.0
        assert result.penalty == 0.0


# --- ペナルティ計算テスト ---

class TestBcaPenalty:
    """ペナルティ計算のテスト"""

    def test_no_penalty_low_opposition(self):
        """逆方向比率が低い場合ペナルティ0"""
        assessor = DirectionalEdgeAssessor(min_edge=0.1)
        # opp_ratio = 1/8 = 0.125 < 0.3
        consensus = _make_consensus(
            buy_score=8.0, sell_score=1.0,
        )
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.8, sell_strength=0.0,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.penalty == 0.0

    def test_penalty_moderate_opposition(self):
        """中程度の逆方向比率でペナルティ発生"""
        assessor = DirectionalEdgeAssessor(
            min_edge=0.1, penalty_scale=1.0,
        )
        # opp_ratio = 3/6 = 0.5 > 0.3
        consensus = _make_consensus(
            buy_score=6.0, sell_score=3.0,
        )
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.3,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.penalty > 0.0
        # edge = (6-3)/(6+3) = 0.333 > 0.1
        assert result.passed is True

    def test_penalty_scale_effect(self):
        """ペナルティスケールの効果"""
        # opp_ratio = 3.5/6 ≈ 0.583 > 0.3
        consensus = _make_consensus(
            buy_score=6.0, sell_score=3.5,
        )
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.35,
            ),
        }

        result_low = DirectionalEdgeAssessor(
            min_edge=0.1, penalty_scale=0.5,
        ).assess(consensus, tf_signals, _default_tf_set())

        result_high = DirectionalEdgeAssessor(
            min_edge=0.1, penalty_scale=2.0,
        ).assess(consensus, tf_signals, _default_tf_set())

        assert result_high.penalty > result_low.penalty

    def test_penalty_clamped_to_1(self):
        """ペナルティは1.0にクランプされる"""
        assessor = DirectionalEdgeAssessor(
            min_edge=0.01, penalty_scale=100.0,
        )
        consensus = _make_consensus(
            buy_score=5.5, sell_score=5.0,
        )
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.5, sell_strength=0.5,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.penalty <= 1.0


# --- HTF逆方向テスト ---

class TestHtfOpposition:
    """HTF逆方向の重み付けテスト（v2: 全TFの逆方向強度を取得）"""

    def test_htf_has_higher_opp_when_selling(self):
        """HTFのsell_strengthがhtf_oppositionに反映される"""
        assessor = DirectionalEdgeAssessor(min_edge=0.1)
        consensus = _make_consensus(
            buy_score=8.0, sell_score=2.0,
        )

        # H4(HTF)にsell_strengthがある場合
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.7, sell_strength=0.1,
            ),
            "H4": _make_tf_signal(
                "H4", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.4,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        # H4のsell_strength=0.4がHTF逆方向に反映
        assert result.htf_opposition == pytest.approx(
            0.4, abs=0.01,
        )
        # M15のsell_strength=0.1がLTF逆方向に反映
        assert result.ltf_opposition == pytest.approx(
            0.1, abs=0.01,
        )

    def test_all_aligned_zero_sell_strength(self):
        """sell_strength=0の場合、逆方向は0"""
        assessor = DirectionalEdgeAssessor(min_edge=0.1)
        consensus = _make_consensus(
            buy_score=10.0, sell_score=1.0,
        )
        tf_signals = {
            "M5": _make_tf_signal(
                "M5", SignalType.BUY,
                buy_strength=0.8, sell_strength=0.0,
            ),
            "H1": _make_tf_signal(
                "H1", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.0,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.htf_opposition == 0.0
        assert result.ltf_opposition == 0.0

    def test_htf_opposition_amplifies_penalty(self):
        """HTF逆方向が強い場合ペナルティが増幅される"""
        # opp_ratio = 3.5/6.5 ≈ 0.538 > 0.3
        consensus = _make_consensus(
            buy_score=6.5, sell_score=3.5,
        )

        # HTFにsell_strengthがない場合
        tf_no_htf_opp = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.3,
            ),
        }
        result_no_htf = DirectionalEdgeAssessor(
            min_edge=0.1, penalty_scale=1.0,
        ).assess(consensus, tf_no_htf_opp, _default_tf_set())

        # H1にsell_strength=0.5がある場合
        tf_with_htf_opp = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.3,
            ),
            "H1": _make_tf_signal(
                "H1", SignalType.SELL,
                buy_strength=0.2, sell_strength=0.5,
            ),
        }
        result_with_htf = DirectionalEdgeAssessor(
            min_edge=0.1, penalty_scale=1.0,
        ).assess(consensus, tf_with_htf_opp, _default_tf_set())

        # HTF逆方向があるほうがペナルティ大
        assert result_with_htf.penalty >= result_no_htf.penalty
        assert result_with_htf.htf_opposition > 0


# --- エッジケーステスト ---

class TestEdgeCases:
    """エッジケースのテスト"""

    def test_sell_direction_winner(self):
        """SELL方向が勝者の場合も正しく評価"""
        assessor = DirectionalEdgeAssessor(min_edge=0.25)
        # edge = (10 - 2) / (10 + 2) ≈ 0.667
        consensus = _make_consensus(
            buy_score=2.0, sell_score=10.0,
            direction=SignalType.SELL,
        )
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.SELL,
                buy_strength=0.1, sell_strength=0.8,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.passed is True
        assert result.directional_edge == pytest.approx(
            0.667, abs=0.01,
        )
        # SELL方向が勝者なのでbuy_strengthが逆方向
        assert result.ltf_opposition == pytest.approx(
            0.1, abs=0.01,
        )

    def test_min_edge_boundary(self):
        """min_edge境界値テスト"""
        # edge = (8 - 2) / (8 + 2) = 0.6
        consensus = _make_consensus(
            buy_score=8.0, sell_score=2.0,
        )
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.75, sell_strength=0.25,
            ),
        }

        # edge=0.6 >= min_edge=0.6 → パス
        result_pass = DirectionalEdgeAssessor(
            min_edge=0.6,
        ).assess(consensus, tf_signals, _default_tf_set())
        assert result_pass.passed is True

        # edge=0.6 < min_edge=0.61 → ブロック
        result_block = DirectionalEdgeAssessor(
            min_edge=0.61,
        ).assess(consensus, tf_signals, _default_tf_set())
        assert result_block.passed is False

    def test_empty_tf_signals_still_evaluated(self):
        """空のtf_signalsでもコンセンサスベースで判定"""
        assessor = DirectionalEdgeAssessor(min_edge=0.25)
        # edge = (8 - 1) / (8 + 1) ≈ 0.778
        consensus = _make_consensus(
            buy_score=8.0, sell_score=1.0,
        )
        result = assessor.assess(
            consensus, {}, _default_tf_set(),
        )
        assert result.passed is True
        assert result.htf_opposition == 0.0
        assert result.ltf_opposition == 0.0

    def test_reasoning_format(self):
        """reasoningフォーマット確認"""
        assessor = DirectionalEdgeAssessor(min_edge=0.25)

        # パスケース
        consensus_pass = _make_consensus(
            buy_score=10.0, sell_score=1.0,
        )
        result_pass = assessor.assess(
            consensus_pass, {}, _default_tf_set(),
        )
        assert "BCAパス" in result_pass.reasoning

        # ブロックケース
        consensus_block = _make_consensus(
            buy_score=5.5, sell_score=5.0,
        )
        result_block = assessor.assess(
            consensus_block, {}, _default_tf_set(),
        )
        assert "BCAブロック" in result_block.reasoning

    def test_v2_all_tfs_opposition_counted(self):
        """v2: 同方向TFの逆方向成分も計算に含まれる"""
        assessor = DirectionalEdgeAssessor(min_edge=0.1)
        consensus = _make_consensus(
            buy_score=8.0, sell_score=2.0,
        )
        # H1はBUY方向だがsell_strength=0.3がある
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.7, sell_strength=0.1,
            ),
            "H1": _make_tf_signal(
                "H1", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.3,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        # H1のsell_strength=0.3がHTF逆方向に反映
        # （v1ではH1はBUY方向なのでスキップされていた）
        assert result.htf_opposition == pytest.approx(
            0.3, abs=0.01,
        )
