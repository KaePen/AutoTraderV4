"""戦略内コンセンサスモジュール

各戦略がTimeframeEvaluator結果を統合して方向を決定するロジック。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from autotrader.core.enums import SignalType

from .types import (
    InStrategyConsensusResult,
    StrategyId,
    StrategyTimeframes,
)

if TYPE_CHECKING:
    from autotrader.decision.unified.timeframe_evaluator import TimeframeSignal


@dataclass(frozen=True)
class InStrategyConsensusConfig:
    """戦略内コンセンサス設定

    Attributes:
        primary_weight: 主要TFの重み
        entry_weight: エントリーTFの重み
        confirm_weight: 確認TFの重み
        htf_ref_weight: HTF参照の基本重み
        min_confidence: 最小確度閾値
        score_margin_required: 買売スコア差の最小比率
    """

    primary_weight: float = 3.0
    entry_weight: float = 2.5
    confirm_weight: float = 2.0
    htf_ref_weight: float = 1.5
    min_confidence: float = 0.3
    score_margin_required: float = 0.15


class InStrategyConsensus:
    """戦略内コンセンサス統合器

    単一戦略内で複数時間足のシグナルを統合し、方向を決定する。

    計算手順:
    1. 各TFの方向を{-1, 0, +1}に変換
    2. TF役割に応じた重みを付与
    3. 買いスコア/売りスコアを計算
    4. 方向決定と確度計算
    """

    def __init__(
        self,
        strategy_id: StrategyId,
        strategy_tfs: StrategyTimeframes,
        config: InStrategyConsensusConfig | None = None,
    ) -> None:
        """初期化

        Args:
            strategy_id: 戦略識別子
            strategy_tfs: 戦略別時間足設定
            config: コンセンサス設定
        """
        self.strategy_id = strategy_id
        self.strategy_tfs = strategy_tfs
        self.config = config or InStrategyConsensusConfig()

    def consolidate(
        self,
        tf_signals: dict[str, TimeframeSignal],
    ) -> InStrategyConsensusResult:
        """シグナルを統合

        Args:
            tf_signals: 時間足別シグナル

        Returns:
            InStrategyConsensusResult: コンセンサス結果
        """
        if not tf_signals:
            return self._create_hold_result("シグナルなし")

        # 買い/売りスコアを計算
        buy_score = 0.0
        sell_score = 0.0
        buy_tfs: list[str] = []
        sell_tfs: list[str] = []

        for tf, signal in tf_signals.items():
            weight = self._get_weight(tf)
            direction_value = self._direction_to_value(signal.direction)

            # 強度を考慮したスコア
            weighted_score = weight * abs(direction_value) * signal.confidence

            if direction_value > 0:
                buy_score += weighted_score
                buy_tfs.append(tf)
            elif direction_value < 0:
                sell_score += weighted_score
                sell_tfs.append(tf)

        total_score = buy_score + sell_score

        # 方向決定
        if buy_score > sell_score:
            direction = SignalType.BUY
            aligned_tfs = tuple(buy_tfs)
            primary_score = buy_score
        elif sell_score > buy_score:
            direction = SignalType.SELL
            aligned_tfs = tuple(sell_tfs)
            primary_score = sell_score
        else:
            return self._create_hold_result("買売スコア同値")

        # スコアマージン確認
        margin_ratio = (primary_score - min(buy_score, sell_score)) / max(
            total_score, 0.01
        )
        if margin_ratio < self.config.score_margin_required:
            return self._create_hold_result(
                f"マージン不足: {margin_ratio:.2%} < {self.config.score_margin_required:.2%}"
            )

        # 確度計算（主要スコア / 最大可能スコア）
        max_possible = self._calculate_max_score(tf_signals)
        confidence = min(primary_score / max(max_possible, 0.01), 1.0)

        if confidence < self.config.min_confidence:
            return self._create_hold_result(
                f"確度不足: {confidence:.2%} < {self.config.min_confidence:.2%}"
            )

        reasoning = (
            f"{self.strategy_id.value}: {direction.value} "
            f"(buy={buy_score:.2f}, sell={sell_score:.2f}, "
            f"confidence={confidence:.2%})"
        )

        return InStrategyConsensusResult(
            direction=direction,
            primary_tf=self.strategy_tfs.primary_tf,
            aligned_tfs=aligned_tfs,
            total_score=total_score,
            buy_score=buy_score,
            sell_score=sell_score,
            confidence=confidence,
            reasoning=reasoning,
        )

    def _get_weight(self, tf: str) -> float:
        """時間足の重みを取得

        Args:
            tf: 時間足

        Returns:
            float: 重み
        """
        tfs = self.strategy_tfs

        if tf == tfs.primary_tf:
            return self.config.primary_weight
        elif tf == tfs.entry_tf:
            return self.config.entry_weight
        elif tf in tfs.confirm_tfs:
            return self.config.confirm_weight
        elif tf in tfs.htf_refs:
            # HTF参照の重みはstrategy_tfs.htf_weightで調整
            return self.config.htf_ref_weight * tfs.htf_weight
        else:
            return 0.5

    def _direction_to_value(self, direction: SignalType) -> int:
        """シグナル方向を数値に変換

        Args:
            direction: シグナル方向

        Returns:
            int: -1, 0, +1
        """
        if direction == SignalType.BUY:
            return 1
        elif direction == SignalType.SELL:
            return -1
        return 0

    def _calculate_max_score(
        self, tf_signals: dict[str, TimeframeSignal]
    ) -> float:
        """最大可能スコアを計算

        Args:
            tf_signals: 時間足別シグナル

        Returns:
            float: 全TFが同方向かつ確度1.0の場合のスコア
        """
        max_score = 0.0
        for tf in tf_signals:
            max_score += self._get_weight(tf)
        return max_score

    def _create_hold_result(self, reason: str) -> InStrategyConsensusResult:
        """HOLDの結果を作成

        Args:
            reason: 理由

        Returns:
            InStrategyConsensusResult: HOLD結果
        """
        return InStrategyConsensusResult(
            direction=SignalType.HOLD,
            primary_tf=self.strategy_tfs.primary_tf,
            aligned_tfs=(),
            total_score=0.0,
            buy_score=0.0,
            sell_score=0.0,
            confidence=0.0,
            reasoning=f"{self.strategy_id.value}: HOLD - {reason}",
        )
