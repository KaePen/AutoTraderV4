"""モード対応コンセンサスモジュール

ALL/MAJORITY/WEIGHTEDを廃止し、単一コンセンサスルールに統合。
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.core.enums import SignalType, TradingStrategyMode
from autotrader.decision.unified.mode_selector import TradingPlan
from autotrader.decision.unified.timeframe_evaluator import (
    TimeframeSignal,
)
from autotrader.decision.unified.timeframe_router import (
    TimeframeRole,
    TimeframeSet,
)


@dataclass(frozen=True)
class ConsensusResult:
    """コンセンサス結果

    Attributes:
        direction: 最終シグナル方向
        score: 統合スコア
        threshold: 適用閾値
        aligned_tfs: 同方向TFリスト
        reasoning: 判断理由
        buy_score: BUY方向の加重スコア（多面分析用）
        sell_score: SELL方向の加重スコア（多面分析用）
        dynamic_entry_tf: 動的選択されたエントリーTF（UNIVERSALモード用）
    """

    direction: SignalType
    score: float
    threshold: float
    aligned_tfs: list[str]
    reasoning: str
    buy_score: float = 0.0
    sell_score: float = 0.0
    dynamic_entry_tf: str | None = None


@dataclass(frozen=True)
class ConsensusConfig:
    """コンセンサス設定

    Attributes:
        primary_weight: 主要TFの重み
        entry_weight: エントリーTFの重み
        confirm_weight: 確認TFの重み
        manage_weight: 管理TFの重み
        other_weight: その他TFの重み
        threshold: コンセンサス閾値（UNIVERSALモード）
        enable_counter_signal: 多面分析（逆方向シグナル活用）の有効化
    """

    primary_weight: float = 3.0
    entry_weight: float = 2.5
    confirm_weight: float = 2.0
    manage_weight: float = 1.5
    other_weight: float = 1.0
    threshold: float = 4.5
    enable_counter_signal: bool = True


class ModeAwareScoreConsensus:
    """モード対応スコアコンセンサス

    TradingPlanに従って重み付けスコアを計算し、
    モード別閾値で判定する。

    計算手順:
    1. 各TFの方向を{-1, 0, +1}に正規化
    2. TF役割に応じた重みを付与
    3. 加重合計スコアを計算
    4. モード別閾値で判定
    """

    def __init__(self, config: ConsensusConfig | None = None) -> None:
        """初期化

        Args:
            config: コンセンサス設定
        """
        self.config = config or ConsensusConfig()

        # 設定から重みを構築
        self.role_weights = {
            TimeframeRole.PRIMARY: self.config.primary_weight,
            TimeframeRole.ENTRY: self.config.entry_weight,
            TimeframeRole.CONFIRM: self.config.confirm_weight,
            TimeframeRole.MANAGE: self.config.manage_weight,
            TimeframeRole.OTHER: self.config.other_weight,
        }

        self.threshold = self.config.threshold

    def consolidate(
        self,
        tf_signals: dict[str, TimeframeSignal],
        plan: TradingPlan,
        threshold_override: float | None = None,
    ) -> ConsensusResult:
        """シグナルを統合

        Args:
            tf_signals: 時間足別シグナル
            plan: トレーディングプラン
            threshold_override: 閾値オーバーライド（アダプティブ調整用）

        Returns:
            ConsensusResult: コンセンサス結果
        """
        from autotrader.decision.unified.timeframe_router import (
            TimeframeRouter,
        )

        router = TimeframeRouter()
        tf_set = router.route(plan)

        # 各TFのスコアを計算
        buy_score = 0.0
        sell_score = 0.0
        buy_tfs: list[str] = []
        sell_tfs: list[str] = []
        buy_individual: list[float] = []
        sell_individual: list[float] = []

        for tf, signal in tf_signals.items():
            weight = self._get_weight(tf, tf_set, plan.mode)
            direction_value = self._direction_to_value(signal.direction)

            # 強度を考慮したスコア（net_strength: 買い-売りの純強度）
            strength = abs(signal.net_strength)
            weighted_score = weight * abs(direction_value) * strength

            if direction_value > 0:
                buy_score += weighted_score
                buy_tfs.append(tf)
                buy_individual.append(weighted_score)
            elif direction_value < 0:
                sell_score += weighted_score
                sell_tfs.append(tf)
                sell_individual.append(weighted_score)

        # 最終スコアと方向を決定
        if buy_score > sell_score:
            final_score = buy_score
            aligned_tfs = buy_tfs
            direction = SignalType.BUY
        elif sell_score > buy_score:
            final_score = sell_score
            aligned_tfs = sell_tfs
            direction = SignalType.SELL
        else:
            final_score = 0.0
            aligned_tfs = []
            direction = SignalType.HOLD

        # 閾値判定（アダプティブオーバーライド対応）
        threshold = (
            threshold_override
            if threshold_override is not None
            else self.threshold
        )

        if final_score < threshold:
            reasoning = (
                f"スコア不足: {final_score:.2f} < 閾値{threshold:.2f} "
                f"({plan.mode.value})"
            )
            return ConsensusResult(
                direction=SignalType.HOLD,
                score=final_score,
                threshold=threshold,
                aligned_tfs=aligned_tfs,
                reasoning=reasoning,
                buy_score=buy_score,
                sell_score=sell_score,
            )

        # TF方向一致率チェック（低品質シグナルの排除）
        # 過剰フィッティング防止のため単一閾値50%（多数決原則）を使用
        total_tfs = len(tf_signals)
        if total_tfs > 0 and direction != SignalType.HOLD:
            aligned_count = len(aligned_tfs)
            alignment_ratio = aligned_count / total_tfs
            # 全モード共通: 50%以上のTFが同方向に一致すること
            if alignment_ratio < 0.50:
                return ConsensusResult(
                    direction=SignalType.HOLD,
                    score=final_score,
                    threshold=threshold,
                    aligned_tfs=aligned_tfs,
                    reasoning=(
                        f"TF整合率不足: {aligned_count}/{total_tfs}="
                        f"{alignment_ratio:.0%} < 50%"
                        f"({plan.mode.value})"
                    ),
                    buy_score=buy_score,
                    sell_score=sell_score,
                )

        # 競合シグナル比率チェック（BUY/SELL拮抗の排除）
        # 過剰フィッティング防止のため単一閾値60%（拮抗回避）を使用
        if final_score > 0 and direction != SignalType.HOLD:
            competing_score = (
                sell_score if direction == SignalType.BUY else buy_score
            )
            conflict_ratio = competing_score / final_score
            # 全モード共通: 競合スコアが勝方向の60%未満であること
            if conflict_ratio > 0.60:
                # 多面分析: 競合方向も閾値超えなら逆方向シグナルとして採用
                if self.config.enable_counter_signal:
                    counter_direction = (
                        SignalType.SELL
                        if direction == SignalType.BUY
                        else SignalType.BUY
                    )
                    counter_tfs = (
                        sell_tfs
                        if direction == SignalType.BUY
                        else buy_tfs
                    )
                    if competing_score >= threshold:
                        return ConsensusResult(
                            direction=counter_direction,
                            score=competing_score,
                            threshold=threshold,
                            aligned_tfs=counter_tfs,
                            reasoning=(
                                f"多面分析: 競合{conflict_ratio:.0%}超 → "
                                f"{counter_direction.value}"
                                f"({competing_score:.2f}>={threshold:.2f})"
                                f"({plan.mode.value})"
                            ),
                            buy_score=buy_score,
                            sell_score=sell_score,
                        )
                return ConsensusResult(
                    direction=SignalType.HOLD,
                    score=final_score,
                    threshold=threshold,
                    aligned_tfs=aligned_tfs,
                    reasoning=(
                        f"競合シグナル強度過大: {conflict_ratio:.0%}"
                        f" > 60%({plan.mode.value})"
                    ),
                    buy_score=buy_score,
                    sell_score=sell_score,
                )

        # スコア均一性チェック（一部TFのみ高スコアの偏りを除外）
        # 変動係数(CV) > 1.0: 1TFのスコアが平均より1標準偏差超過の偏り
        # 過剰フィッティング防止のため単一閾値（モード共通1.0）を使用
        aligned_individual = (
            buy_individual
            if direction == SignalType.BUY
            else sell_individual
        )
        if len(aligned_individual) >= 2:
            _mean_s = sum(aligned_individual) / len(aligned_individual)
            if _mean_s > 0:
                _std_s = (
                    sum(
                        (s - _mean_s) ** 2
                        for s in aligned_individual
                    ) / len(aligned_individual)
                ) ** 0.5
                _cv = _std_s / _mean_s
                if _cv > 1.0:
                    return ConsensusResult(
                        direction=SignalType.HOLD,
                        score=final_score,
                        threshold=threshold,
                        aligned_tfs=aligned_tfs,
                        reasoning=(
                            f"スコア偏り過大(CV={_cv:.2f}>1.0)"
                            f"({plan.mode.value})"
                        ),
                        buy_score=buy_score,
                        sell_score=sell_score,
                    )

        reasoning = (
            f"{direction.value}シグナル: スコア{final_score:.2f} >= "
            f"閾値{threshold:.2f}, 整合TF={aligned_tfs}"
        )

        return ConsensusResult(
            direction=direction,
            score=final_score,
            threshold=threshold,
            aligned_tfs=aligned_tfs,
            reasoning=reasoning,
            buy_score=buy_score,
            sell_score=sell_score,
        )

    def _get_weight(
        self,
        tf: str,
        tf_set: TimeframeSet,
        mode: TradingStrategyMode | None = None,
    ) -> float:
        """時間足の重みを取得

        Args:
            tf: 時間足
            tf_set: 時間足セット
            mode: 互換性のため残存（未使用）

        Returns:
            float: 重み
        """
        role = tf_set.get_role(tf)
        return self.role_weights.get(role, self.config.other_weight)

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

    def get_threshold_for_mode(
        self,
        mode: TradingStrategyMode | None = None,
    ) -> float:
        """閾値を取得

        Args:
            mode: 互換性のため残存（未使用）

        Returns:
            float: 閾値
        """
        return self.threshold

    def check_entry_conditions(
        self,
        tf_signals: dict[str, TimeframeSignal],
        plan: "TradingPlan",
        completed_tf: str,
    ) -> ConsensusResult:
        """エントリー条件をチェック

        entry_tf足確定時のみエントリー判断を行う。

        Args:
            tf_signals: 時間足別シグナル
            plan: トレーディングプラン
            completed_tf: 確定した時間足

        Returns:
            ConsensusResult: コンセンサス結果
        """
        # UNIVERSALモード: 全TF確定でエントリー判断（dynamic_entry_tf優先）
        # 通常のコンセンサス処理
        result = self.consolidate(tf_signals, plan)

        # entry_tfの方向と整合性チェック（dynamic_entry_tf優先）
        entry_tf_key = plan.dynamic_entry_tf or plan.entry_tf
        entry_signal = tf_signals.get(entry_tf_key)
        if entry_signal is None:
            return ConsensusResult(
                direction=SignalType.HOLD,
                score=result.score,
                threshold=result.threshold,
                aligned_tfs=result.aligned_tfs,
                reasoning="entry_tfシグナルなし",
            )

        # コンセンサスとentry_tfの方向が一致しない場合はスキップ
        # ただし多面分析による逆方向採用で"多面分析"が含まれる場合は許容
        is_counter_signal = "多面分析" in result.reasoning
        if (
            result.direction != SignalType.HOLD
            and entry_signal.direction != SignalType.HOLD
            and result.direction != entry_signal.direction
            and not is_counter_signal
        ):
            return ConsensusResult(
                direction=SignalType.HOLD,
                score=result.score,
                threshold=result.threshold,
                aligned_tfs=result.aligned_tfs,
                reasoning=(
                    f"コンセンサス({result.direction.value})"
                    f"とentry_tf({entry_signal.direction.value})不一致"
                ),
                buy_score=result.buy_score,
                sell_score=result.sell_score,
            )

        return result
