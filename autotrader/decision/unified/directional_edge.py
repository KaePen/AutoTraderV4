"""方向性エッジ評価モジュール（BCA: Bidirectional Conviction Assessment）

両方向スコアを連続的に評価し、方向性確信度が低い
エントリーをフィルタリングする。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from autotrader.core.enums import SignalType

if TYPE_CHECKING:
    from autotrader.decision.unified.mode_aware_consensus import (
        ConsensusResult,
    )
    from autotrader.decision.unified.timeframe_evaluator import (
        TimeframeSignal,
    )
    from autotrader.decision.unified.timeframe_router import (
        TimeframeSet,
    )

# HTF重み: 上位足ほど逆方向シグナルのペナルティが大きい
_HTF_WEIGHTS: dict[str, float] = {
    "D1": 3.5,
    "H8": 3.0,
    "H4": 3.0,
    "H1": 2.5,
    "M30": 2.0,
    "M15": 1.5,
    "M5": 1.0,
    "M1": 0.5,
}

# HTFとみなす時間足（H1以上）
_HTF_SET = frozenset({"D1", "H8", "H4", "H1"})


@dataclass(frozen=True)
class DirectionalEdgeResult:
    """方向性エッジ評価結果

    Attributes:
        directional_edge: 方向性エッジ (winner - loser) / (winner + loser)
        opposition_ratio: 逆方向比率 loser / winner
        htf_opposition: HTF逆方向の加重強度
        ltf_opposition: LTF逆方向の加重強度
        passed: min_edge以上か
        penalty: SoftGuardに渡すペナルティ
        reasoning: 判断理由
    """

    directional_edge: float
    opposition_ratio: float
    htf_opposition: float
    ltf_opposition: float
    passed: bool
    penalty: float
    reasoning: str


class DirectionalEdgeAssessor:
    """方向性エッジ評価器

    コンセンサスのbuy_score/sell_scoreから方向性エッジを算出し、
    逆方向の強度をHTFウェイトで重み付けしてペナルティを生成する。
    """

    def __init__(
        self,
        min_edge: float = 0.25,
        penalty_scale: float = 1.0,
    ) -> None:
        """初期化

        Args:
            min_edge: 最小方向性エッジ閾値（これ未満でブロック）
            penalty_scale: ペナルティスケール係数
        """
        self._min_edge = min_edge
        self._penalty_scale = penalty_scale

    def assess(
        self,
        consensus: ConsensusResult,
        tf_signals: dict[str, TimeframeSignal],
        tf_set: TimeframeSet,
    ) -> DirectionalEdgeResult:
        """方向性エッジを評価

        Args:
            consensus: コンセンサス結果
            tf_signals: 時間足別シグナル（TimeframeEvaluator由来）
            tf_set: 時間足セット

        Returns:
            DirectionalEdgeResult: 評価結果
        """
        buy_score = consensus.buy_score
        sell_score = consensus.sell_score
        total = buy_score + sell_score

        # スコア合計が0の場合はブロック
        if total <= 0:
            return DirectionalEdgeResult(
                directional_edge=0.0,
                opposition_ratio=0.0,
                htf_opposition=0.0,
                ltf_opposition=0.0,
                passed=False,
                penalty=0.0,
                reasoning="スコア合計=0",
            )

        # 勝方向と敗方向のスコア
        if buy_score >= sell_score:
            winner = buy_score
            loser = sell_score
            winner_dir = SignalType.BUY
        else:
            winner = sell_score
            loser = buy_score
            winner_dir = SignalType.SELL

        # 方向性エッジ: (winner - loser) / (winner + loser)
        directional_edge = (winner - loser) / total

        # 逆方向比率: loser / winner
        opposition_ratio = loser / winner if winner > 0 else 0.0

        # HTF/LTF逆方向の加重強度を計算
        htf_opp, ltf_opp = self._calc_opposition_by_tier(
            tf_signals, winner_dir,
        )

        # ペナルティ計算（逆方向比率ベース）
        # opposition_ratio が 0.3 以上でペナルティ発生
        _opp_threshold = 0.3
        penalty = 0.0
        if opposition_ratio > _opp_threshold:
            # 線形ペナルティ: (ratio - threshold) * scale
            _raw = (opposition_ratio - _opp_threshold) * 0.5
            # HTF逆方向がある場合はペナルティ増幅
            _htf_mult = 1.0 + htf_opp * 0.5
            penalty = _raw * _htf_mult * self._penalty_scale
            # 0.0-1.0にクランプ
            penalty = min(max(penalty, 0.0), 1.0)

        # パス判定
        passed = directional_edge >= self._min_edge

        # 理由文生成
        if not passed:
            reasoning = (
                f"BCAブロック: edge={directional_edge:.3f}"
                f"<{self._min_edge}"
                f"(opp={opposition_ratio:.2f},"
                f" htf={htf_opp:.2f})"
            )
        elif penalty > 0:
            reasoning = (
                f"BCAペナルティ: edge={directional_edge:.3f},"
                f" penalty={penalty:.3f}"
                f"(opp={opposition_ratio:.2f},"
                f" htf={htf_opp:.2f})"
            )
        else:
            reasoning = (
                f"BCAパス: edge={directional_edge:.3f}"
                f"(opp={opposition_ratio:.2f})"
            )

        return DirectionalEdgeResult(
            directional_edge=directional_edge,
            opposition_ratio=opposition_ratio,
            htf_opposition=htf_opp,
            ltf_opposition=ltf_opp,
            passed=passed,
            penalty=penalty,
            reasoning=reasoning,
        )

    def _calc_opposition_by_tier(
        self,
        tf_signals: dict[str, TimeframeSignal],
        winner_dir: SignalType,
    ) -> tuple[float, float]:
        """HTF/LTF別の逆方向加重強度を計算

        Args:
            tf_signals: 時間足別シグナル
            winner_dir: 勝方向

        Returns:
            tuple[float, float]: (htf_opposition, ltf_opposition)
        """
        htf_opp = 0.0
        ltf_opp = 0.0
        htf_weight_sum = 0.0
        ltf_weight_sum = 0.0

        for tf, signal in tf_signals.items():
            # 勝方向と同方向または HOLDはスキップ
            if signal.direction == winner_dir:
                continue
            if signal.direction == SignalType.HOLD:
                continue

            weight = _HTF_WEIGHTS.get(tf, 1.0)

            # 逆方向の強度を取得
            if winner_dir == SignalType.BUY:
                opp_strength = signal.sell_strength
            else:
                opp_strength = signal.buy_strength

            weighted_opp = weight * opp_strength

            if tf in _HTF_SET:
                htf_opp += weighted_opp
                htf_weight_sum += weight
            else:
                ltf_opp += weighted_opp
                ltf_weight_sum += weight

        # 正規化（重み合計で割る）
        if htf_weight_sum > 0:
            htf_opp /= htf_weight_sum
        if ltf_weight_sum > 0:
            ltf_opp /= ltf_weight_sum

        return htf_opp, ltf_opp
