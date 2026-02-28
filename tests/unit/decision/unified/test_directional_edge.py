"""方向性エッジ評価器（BCA v2）のユニットテスト

v2: 個別TF加重ベースでの方向性エッジ算出をテスト。
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
        consensus = _make_consensus(
            buy_score=10.0, sell_score=2.0,
        )
        # M5(w=1.0), M15(w=1.5), H1(w=2.5)
        # support = 0.8*1.0 + 0.7*1.5 + 0.6*2.5 = 3.35
        # opp    = 0.1*1.0 + 0.2*1.5 + 0.1*2.5 = 0.65
        # edge   = (3.35-0.65)/(3.35+0.65) = 0.675
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
            0.675, abs=0.01,
        )

    def test_balanced_signals_blocked(self):
        """拮抗シグナルはブロックされる"""
        assessor = DirectionalEdgeAssessor(min_edge=0.25)
        consensus = _make_consensus(
            buy_score=6.0, sell_score=5.5,
        )
        # M15(w=1.5): support=0.5*1.5=0.75, opp=0.4*1.5=0.60
        # edge = (0.75-0.60)/(0.75+0.60) = 0.15/1.35 ≈ 0.111
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

    def test_empty_tf_signals_blocked(self):
        """TFシグナルなしの場合ブロック"""
        assessor = DirectionalEdgeAssessor()
        consensus = _make_consensus(
            buy_score=8.0, sell_score=1.0,
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
        consensus = _make_consensus(
            buy_score=8.0, sell_score=1.0,
        )
        # M15(w=1.5): support=0.8*1.5=1.2, opp=0.0*1.5=0
        # opp_ratio = 0/1.2 = 0 < 0.3
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
        consensus = _make_consensus(
            buy_score=6.0, sell_score=3.0,
        )
        # M15(w=1.5): support=0.6*1.5=0.9, opp=0.3*1.5=0.45
        # opp_ratio = 0.45/0.9 = 0.5 > 0.3
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
        # edge = (0.9-0.45)/(0.9+0.45) = 0.333 > 0.1
        assert result.passed is True

    def test_penalty_scale_effect(self):
        """ペナルティスケールの効果"""
        consensus = _make_consensus(
            buy_score=6.0, sell_score=3.5,
        )
        # M15(w=1.5): support=0.6*1.5=0.9, opp=0.35*1.5=0.525
        # opp_ratio = 0.525/0.9 ≈ 0.583 > 0.3
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
    """HTF逆方向の重み付けテスト"""

    def test_htf_opposition_higher_weight(self):
        """HTF逆方向はLTF逆方向より大きなhtf_opposition"""
        assessor = DirectionalEdgeAssessor(min_edge=0.1)
        consensus = _make_consensus(
            buy_score=7.0, sell_score=3.5,
        )

        # HTF(H4, w=3.0)が逆方向の強い売り
        tf_signals_htf = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.7, sell_strength=0.1,
            ),
            "H4": _make_tf_signal(
                "H4", SignalType.SELL,
                buy_strength=0.1, sell_strength=0.6,
            ),
        }
        result_htf = assessor.assess(
            consensus, tf_signals_htf, _default_tf_set(),
        )

        # LTF(M5, w=1.0)が逆方向の強い売り
        tf_signals_ltf = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.7, sell_strength=0.1,
            ),
            "M5": _make_tf_signal(
                "M5", SignalType.SELL,
                buy_strength=0.1, sell_strength=0.6,
            ),
        }
        result_ltf = assessor.assess(
            consensus, tf_signals_ltf, _default_tf_set(),
        )

        # HTFケースはHTF逆方向が高い
        assert result_htf.htf_opposition > result_ltf.htf_opposition
        # LTFケースはLTF逆方向が高い
        assert result_ltf.ltf_opposition > result_htf.ltf_opposition

    def test_all_aligned_low_opposition(self):
        """全TFが同方向（sell_strength=0）の場合、逆方向は0"""
        assessor = DirectionalEdgeAssessor(min_edge=0.1)
        consensus = _make_consensus(
            buy_score=10.0, sell_score=1.0,
        )
        tf_signals = {
            "M5": _make_tf_signal(
                "M5", SignalType.BUY,
                buy_strength=0.8, sell_strength=0.0,
            ),
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.7, sell_strength=0.0,
            ),
            "H1": _make_tf_signal(
                "H1", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.0,
            ),
            "H4": _make_tf_signal(
                "H4", SignalType.BUY,
                buy_strength=0.5, sell_strength=0.0,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.htf_opposition == 0.0
        assert result.ltf_opposition == 0.0
        assert result.directional_edge == 1.0

    def test_mixed_htf_opposition(self):
        """HTFに逆方向売り強度がある場合のペナルティ増幅"""
        assessor = DirectionalEdgeAssessor(
            min_edge=0.1, penalty_scale=1.0,
        )
        consensus = _make_consensus(
            buy_score=7.0, sell_score=4.0,
        )

        # HTFなし: M5とM15のみ（両方ある程度の逆方向あり）
        tf_no_htf = {
            "M5": _make_tf_signal(
                "M5", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.35,
            ),
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.35,
            ),
        }
        result_no_htf = assessor.assess(
            consensus, tf_no_htf, _default_tf_set(),
        )

        # HTFあり: H1に逆方向あり
        tf_with_htf = {
            "M5": _make_tf_signal(
                "M5", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.35,
            ),
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.6, sell_strength=0.35,
            ),
            "H1": _make_tf_signal(
                "H1", SignalType.SELL,
                buy_strength=0.2, sell_strength=0.7,
            ),
        }
        result_with_htf = assessor.assess(
            consensus, tf_with_htf, _default_tf_set(),
        )

        # HTF逆方向があるほうがペナルティ大
        assert result_with_htf.penalty >= result_no_htf.penalty
        assert result_with_htf.htf_opposition > 0


# --- エッジケーステスト ---

class TestEdgeCases:
    """エッジケースのテスト"""

    def test_sell_direction_winner(self):
        """SELL方向が勝者の場合も正しく評価"""
        assessor = DirectionalEdgeAssessor(min_edge=0.25)
        consensus = _make_consensus(
            buy_score=2.0, sell_score=10.0,
            direction=SignalType.SELL,
        )
        # M15(w=1.5): support=sell=0.8*1.5=1.2, opp=buy=0.1*1.5=0.15
        # edge = (1.2-0.15)/(1.2+0.15) = 1.05/1.35 ≈ 0.778
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
            0.778, abs=0.01,
        )

    def test_min_edge_boundary(self):
        """min_edge境界値テスト"""
        consensus = _make_consensus(
            buy_score=8.0, sell_score=2.0,
        )
        # M15(w=1.5): support=0.75*1.5=1.125, opp=0.25*1.5=0.375
        # edge = (1.125-0.375)/(1.125+0.375) = 0.75/1.5 = 0.5
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.75, sell_strength=0.25,
            ),
        }

        # edge=0.5 >= min_edge=0.5 → パス
        result_pass = DirectionalEdgeAssessor(
            min_edge=0.5,
        ).assess(consensus, tf_signals, _default_tf_set())
        assert result_pass.passed is True

        # edge=0.5 < min_edge=0.51 → ブロック
        result_block = DirectionalEdgeAssessor(
            min_edge=0.51,
        ).assess(consensus, tf_signals, _default_tf_set())
        assert result_block.passed is False

    def test_hold_signals_contribute_zero(self):
        """HOLDシグナル（strength=0）は逆方向に寄与しない"""
        assessor = DirectionalEdgeAssessor(min_edge=0.1)
        consensus = _make_consensus(
            buy_score=8.0, sell_score=2.0,
        )
        tf_signals = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.8, sell_strength=0.0,
            ),
            "H1": _make_tf_signal(
                "H1", SignalType.HOLD,
                buy_strength=0.0, sell_strength=0.0,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        # H1のsell_strength=0なのでHTF逆方向=0
        assert result.htf_opposition == 0.0
        assert result.directional_edge == 1.0

    def test_reasoning_format(self):
        """reasoningフォーマット確認"""
        assessor = DirectionalEdgeAssessor(min_edge=0.25)

        consensus = _make_consensus(
            buy_score=10.0, sell_score=1.0,
        )
        # パスケース: 強いBUYシグナル
        tf_pass = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.9, sell_strength=0.05,
            ),
        }
        result_pass = assessor.assess(
            consensus, tf_pass, _default_tf_set(),
        )
        assert "BCAパス" in result_pass.reasoning

        # ブロックケース: 拮抗シグナル
        consensus_block = _make_consensus(
            buy_score=5.5, sell_score=5.0,
        )
        tf_block = {
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.5, sell_strength=0.45,
            ),
        }
        result_block = assessor.assess(
            consensus_block, tf_block, _default_tf_set(),
        )
        assert "BCAブロック" in result_block.reasoning

    def test_multiple_tfs_weighted(self):
        """複数TFのHTF重み付けが正しく動作"""
        assessor = DirectionalEdgeAssessor(min_edge=0.1)
        consensus = _make_consensus(
            buy_score=8.0, sell_score=3.0,
        )
        # HTF寄り: H4(w=3.0)が強いBUY, M5(w=1.0)が弱い
        tf_signals = {
            "H4": _make_tf_signal(
                "H4", SignalType.BUY,
                buy_strength=0.9, sell_strength=0.05,
            ),
            "H1": _make_tf_signal(
                "H1", SignalType.BUY,
                buy_strength=0.7, sell_strength=0.1,
            ),
            "M15": _make_tf_signal(
                "M15", SignalType.BUY,
                buy_strength=0.5, sell_strength=0.3,
            ),
            "M5": _make_tf_signal(
                "M5", SignalType.SELL,
                buy_strength=0.3, sell_strength=0.6,
            ),
        }
        result = assessor.assess(
            consensus, tf_signals, _default_tf_set(),
        )
        assert result.passed is True
        # HTF(H4,H1)はbuyが強いのでedgeは正
        assert result.directional_edge > 0.2
        # M5が逆方向なのでltf_oppositionが存在
        assert result.ltf_opposition > 0
