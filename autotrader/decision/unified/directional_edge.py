"""方向性エッジ評価モジュール（BCA: Bidirectional Conviction Assessment）

ハイブリッド方式:
- ハードゲート: コンセンサスbuy_score/sell_scoreから方向性エッジを算出
- ペナルティ: 個別TFのbuy_strength/sell_strengthをHTF重み付きで評価

v2: 個別TFの逆方向強度をHTF重みで加重し、
より精密な連続ペナルティを生成する。
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
        directional_edge: コンセンサスベースの方向性エッジ
        opposition_ratio: コンセンサスベースの逆方向比率
        htf_opposition: HTF逆方向の加重平均強度（個別TFベース）
        ltf_opposition: LTF逆方向の加重平均強度（個別TFベース）
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
    """方向性エッジ評価器（v2: ハイブリッド方式）

    ハードゲート: コンセンサスbuy_score/sell_scoreベース
    ペナルティ: 個別TFの逆方向強度をHTF重みで加重
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

        コンセンサススコアで方向性エッジを算出（ハードゲート）、
        個別TFの逆方向強度で連続ペナルティを生成。

        Args:
            consensus: コンセンサス結果
            tf_signals: 時間足別シグナル
            tf_set: 時間足セット

        Returns:
            DirectionalEdgeResult: 評価結果
        """
        buy_score = consensus.buy_score
        sell_score = consensus.sell_score
        total = buy_score + sell_score

        # スコア合計が0の場合はブロック
        if total <= 0:
            return self._blocked("スコア合計=0")

        # 勝方向と敗方向のスコア（コンセンサスベース）
        if buy_score >= sell_score:
            winner = buy_score
            loser = sell_score
            winner_dir = SignalType.BUY
        else:
            winner = sell_score
            loser = buy_score
            winner_dir = SignalType.SELL

        # コンセンサスベースの方向性エッジ（ハードゲート用）
        directional_edge = (winner - loser) / total

        # コンセンサスベースの逆方向比率
        opposition_ratio = loser / winner if winner > 0 else 0.0

        # 個別TFベースのHTF/LTF逆方向強度
        htf_opp, ltf_opp = self._calc_tf_opposition(
            tf_signals, winner_dir,
        )

        # ペナルティ計算（個別TFベース）
        penalty = self._calc_penalty(
            opposition_ratio, htf_opp, ltf_opp,
        )

        # パス判定（コンセンサスベース）
        passed = directional_edge >= self._min_edge

        # 理由文生成
        reasoning = self._build_reasoning(
            directional_edge, opposition_ratio,
            htf_opp, penalty, passed,
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

    def _calc_tf_opposition(
        self,
        tf_signals: dict[str, TimeframeSignal],
        winner_dir: SignalType,
    ) -> tuple[float, float]:
        """個別TFの逆方向加重平均強度を計算

        全TFの逆方向strength（勝方向の反対側）を
        HTF重みで加重して平均化する。
        同方向TFの逆方向成分も含むため、v1より精密。

        Args:
            tf_signals: 時間足別シグナル
            winner_dir: 勝方向

        Returns:
            tuple[float, float]: (htf_opposition, ltf_opposition)
        """
        htf_opp_sum = 0.0
        ltf_opp_sum = 0.0
        htf_wt_sum = 0.0
        ltf_wt_sum = 0.0

        for tf, signal in tf_signals.items():
            weight = _HTF_WEIGHTS.get(tf, 1.0)

            # 全TFの逆方向強度を取得（同方向TFの逆方向成分も含む）
            if winner_dir == SignalType.BUY:
                opp_str = signal.sell_strength
            else:
                opp_str = signal.buy_strength

            if tf in _HTF_SET:
                htf_opp_sum += opp_str * weight
                htf_wt_sum += weight
            else:
                ltf_opp_sum += opp_str * weight
                ltf_wt_sum += weight

        # 加重平均で正規化
        htf_opp = (
            htf_opp_sum / htf_wt_sum if htf_wt_sum > 0 else 0.0
        )
        ltf_opp = (
            ltf_opp_sum / ltf_wt_sum if ltf_wt_sum > 0 else 0.0
        )

        return htf_opp, ltf_opp

    def _calc_penalty(
        self,
        opposition_ratio: float,
        htf_opp: float,
        ltf_opp: float,
    ) -> float:
        """連続ペナルティを計算

        コンセンサスの逆方向比率とTFレベルの逆方向強度を組み合わせ。

        Args:
            opposition_ratio: コンセンサスベース逆方向比率
            htf_opp: HTF加重平均逆方向強度
            ltf_opp: LTF加重平均逆方向強度

        Returns:
            float: ペナルティ値（0.0-1.0）
        """
        # 基本ペナルティ: 逆方向比率が0.3超で発生
        _opp_threshold = 0.3
        if opposition_ratio <= _opp_threshold:
            return 0.0

        # 線形ペナルティ基本値
        raw = (opposition_ratio - _opp_threshold) * 0.5

        # HTF逆方向が強い場合にペナルティ増幅（v2改良）
        # htf_oppが0.2以上で増幅開始、最大2.5倍
        htf_mult = 1.0 + min(htf_opp * 2.0, 1.5)

        penalty = raw * htf_mult * self._penalty_scale
        # 0.0-1.0にクランプ
        return min(max(penalty, 0.0), 1.0)

    def _build_reasoning(
        self,
        edge: float,
        opp_ratio: float,
        htf_opp: float,
        penalty: float,
        passed: bool,
    ) -> str:
        """理由文を生成"""
        if not passed:
            return (
                f"BCAブロック: edge={edge:.3f}"
                f"<{self._min_edge}"
                f"(opp={opp_ratio:.2f},"
                f" htf={htf_opp:.2f})"
            )
        if penalty > 0:
            return (
                f"BCAペナルティ: edge={edge:.3f},"
                f" penalty={penalty:.3f}"
                f"(opp={opp_ratio:.2f},"
                f" htf={htf_opp:.2f})"
            )
        return (
            f"BCAパス: edge={edge:.3f}"
            f"(opp={opp_ratio:.2f})"
        )

    @staticmethod
    def _blocked(reason: str) -> DirectionalEdgeResult:
        """ブロック結果を生成"""
        return DirectionalEdgeResult(
            directional_edge=0.0,
            opposition_ratio=0.0,
            htf_opposition=0.0,
            ltf_opposition=0.0,
            passed=False,
            penalty=0.0,
            reasoning=reason,
        )
