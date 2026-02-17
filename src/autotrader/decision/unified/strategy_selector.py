"""戦略セレクターモジュール

StrategyPoolの評価結果から最適な戦略を選択する。
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.core.enums import SignalType

from .strategies import (
    PoolEvaluationResult,
    ProposedTrade,
    SelectionResult,
    StrategyContext,
    StrategyId,
)


@dataclass(frozen=True)
class SelectorConfig:
    """セレクター設定

    Attributes:
        min_edge_score: 最小エッジスコア閾値
        edge_score_margin: 次点との最小差分
        prefer_current_strategy: 保有中戦略を優先するか
        current_strategy_bonus: 保有中戦略への加算ボーナス
    """

    min_edge_score: float = 0.30
    edge_score_margin: float = 0.05
    prefer_current_strategy: bool = True
    current_strategy_bonus: float = 0.05


class StrategySelector:
    """戦略セレクター

    PoolEvaluationResultからedge_scoreが最大の戦略を選択する。

    選択ロジック:
    1. アクション可能な提案をフィルタ
    2. edge_scoreで降順ソート
    3. 最大edge_scoreが閾値以上なら選択
    4. 次点との差分が十分か確認
    """

    def __init__(self, config: SelectorConfig | None = None) -> None:
        """初期化

        Args:
            config: セレクター設定
        """
        self.config = config or SelectorConfig()

    def choose(
        self,
        pool_result: PoolEvaluationResult,
        context: StrategyContext,
    ) -> SelectionResult:
        """最適な戦略を選択

        NoTrade提案との比較を含む。NoTradeのedge_scoreが
        最良のアクション可能提案を上回る場合はHOLDを返す。

        Args:
            pool_result: プール評価結果
            context: 戦略コンテキスト

        Returns:
            SelectionResult: 選択結果
        """
        # NoTrade提案を取得
        notrade_score = 0.0
        for p in pool_result.proposals:
            if p.strategy_id == StrategyId.NO_TRADE:
                notrade_score = p.edge_score
                break

        # アクション可能な提案のみ
        actionable = pool_result.actionable_proposals
        if not actionable:
            return SelectionResult(
                chosen=None,
                all_proposals=tuple(pool_result.proposals),
                reasoning="アクション可能な提案なし",
            )

        # edge_scoreを調整（保有中戦略にボーナス）
        adjusted = self._apply_current_strategy_bonus(actionable, context)

        # edge_scoreで降順ソート
        sorted_proposals = sorted(
            adjusted, key=lambda x: x[1], reverse=True
        )

        best_proposal, best_score = sorted_proposals[0]

        # NoTradeとの比較: NoTradeが上回れば見送り
        if notrade_score > best_score:
            return SelectionResult(
                chosen=None,
                all_proposals=tuple(pool_result.proposals),
                reasoning=(
                    f"NoTrade優勢: "
                    f"notrade={notrade_score:.2%} > "
                    f"best={best_score:.2%}"
                ),
            )

        # 最小エッジスコアチェック
        if best_score < self.config.min_edge_score:
            return SelectionResult(
                chosen=None,
                all_proposals=tuple(pool_result.proposals),
                reasoning=(
                    f"最大edge_score({best_score:.2%})が閾値未満"
                ),
            )

        # 次点との差分チェック
        if len(sorted_proposals) > 1:
            second_score = sorted_proposals[1][1]
            margin = best_score - second_score
            if margin < self.config.edge_score_margin:
                # 差分が小さい場合は選択に慎重になる
                # ただしエントリーは許可（HOLDにはしない）
                pass

        # 選択理由を構築
        reasoning = self._build_reasoning(
            best_proposal, best_score, sorted_proposals, context
        )

        return SelectionResult(
            chosen=best_proposal,
            all_proposals=tuple(pool_result.proposals),
            reasoning=reasoning,
        )

    def _apply_current_strategy_bonus(
        self,
        proposals: list[ProposedTrade],
        context: StrategyContext,
    ) -> list[tuple[ProposedTrade, float]]:
        """保有中戦略にボーナスを適用

        Args:
            proposals: 提案リスト
            context: コンテキスト

        Returns:
            list[tuple[ProposedTrade, float]]: (提案, 調整後スコア)のリスト
        """
        result: list[tuple[ProposedTrade, float]] = []

        for proposal in proposals:
            adjusted_score = proposal.edge_score

            # 保有中の戦略と同じならボーナス
            if (
                self.config.prefer_current_strategy
                and context.current_strategy_id is not None
                and proposal.strategy_id == context.current_strategy_id
            ):
                adjusted_score += self.config.current_strategy_bonus

            result.append((proposal, adjusted_score))

        return result

    def _build_reasoning(
        self,
        chosen: ProposedTrade,
        chosen_score: float,
        sorted_proposals: list[tuple[ProposedTrade, float]],
        context: StrategyContext,
    ) -> str:
        """選択理由を構築

        Args:
            chosen: 選択された提案
            chosen_score: 選択された提案の調整後スコア
            sorted_proposals: ソート済み提案リスト
            context: コンテキスト

        Returns:
            str: 選択理由
        """
        parts = [
            f"選択: {chosen.strategy_id.value}",
            f"edge={chosen_score:.2%}",
            f"direction={chosen.direction.value}",
        ]

        # 他の候補との比較
        if len(sorted_proposals) > 1:
            others = []
            for proposal, score in sorted_proposals[1:]:
                others.append(f"{proposal.strategy_id.value}={score:.2%}")
            parts.append(f"他候補: {', '.join(others)}")

        # レジーム情報
        parts.append(f"regime={context.regime.value}")

        return " | ".join(parts)

    def choose_for_continuation(
        self,
        pool_result: PoolEvaluationResult,
        context: StrategyContext,
    ) -> SelectionResult:
        """ポジション保有中の継続判断

        保有中の戦略のみを評価し、継続/クローズを判断。

        Args:
            pool_result: プール評価結果
            context: 戦略コンテキスト

        Returns:
            SelectionResult: 選択結果
        """
        if context.current_strategy_id is None:
            return SelectionResult(
                chosen=None,
                all_proposals=tuple(pool_result.proposals),
                reasoning="保有中の戦略なし",
            )

        # 現在の戦略の提案を探す
        current_proposal = None
        for proposal in pool_result.proposals:
            if proposal.strategy_id == context.current_strategy_id:
                current_proposal = proposal
                break

        if current_proposal is None:
            return SelectionResult(
                chosen=None,
                all_proposals=tuple(pool_result.proposals),
                reasoning=f"{context.current_strategy_id.value}の提案なし",
            )

        # HOLDの場合は継続しない
        if current_proposal.direction == SignalType.HOLD:
            return SelectionResult(
                chosen=None,
                all_proposals=tuple(pool_result.proposals),
                reasoning=f"{context.current_strategy_id.value}がHOLD提案",
            )

        return SelectionResult(
            chosen=current_proposal,
            all_proposals=tuple(pool_result.proposals),
            reasoning=f"継続: {context.current_strategy_id.value}",
        )
