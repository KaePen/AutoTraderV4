"""戦略プールモジュール

全戦略を管理し、並列評価を実行する。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pandas as pd

from .strategies import (
    BaseStrategy,
    PoolEvaluationResult,
    StrategyContext,
    StrategyId,
    get_registered_strategies,
)

if TYPE_CHECKING:
    from autotrader.core.entities import Candle

    from .timeframe_evaluator import TimeframeEvaluator


class StrategyPool:
    """戦略プール

    全戦略（Scalp, ShortMid, Swing）を保持し、
    同一時点で並列評価してProposedTradeのリストを返す。
    """

    def __init__(
        self,
        strategies: list[BaseStrategy] | None = None,
    ) -> None:
        """初期化

        Args:
            strategies: 戦略リスト（Noneの場合はレジストリから自動取得）
        """
        if strategies is None:
            self._strategies: list[BaseStrategy] = (
                get_registered_strategies()
            )
        else:
            self._strategies = strategies

        self._evaluators: dict[str, TimeframeEvaluator] = {}

    @property
    def strategy_ids(self) -> list[StrategyId]:
        """登録済み戦略IDリスト"""
        return [s.strategy_id for s in self._strategies]

    @property
    def required_timeframes(self) -> set[str]:
        """全戦略が必要とする時間足の集合"""
        tfs: set[str] = set()
        for strategy in self._strategies:
            tfs.update(strategy.timeframes.all_tfs)
        return tfs

    def set_evaluators(
        self, evaluators: dict[str, TimeframeEvaluator]
    ) -> None:
        """時間足評価器を設定

        Args:
            evaluators: 時間足 -> 評価器のマップ
        """
        self._evaluators = evaluators
        # 各戦略にも設定
        for strategy in self._strategies:
            strategy.set_evaluators(evaluators)

    def get_strategy(self, strategy_id: StrategyId) -> BaseStrategy | None:
        """戦略を取得

        Args:
            strategy_id: 戦略識別子

        Returns:
            BaseStrategy | None: 戦略（存在しない場合None）
        """
        for strategy in self._strategies:
            if strategy.strategy_id == strategy_id:
                return strategy
        return None

    def evaluate_all(
        self,
        context: StrategyContext,
        tf_data: dict[str, pd.DataFrame],
        candle: Candle | None = None,
    ) -> PoolEvaluationResult:
        """全戦略を評価

        Args:
            context: 戦略コンテキスト
            tf_data: 時間足別データ
            candle: 現在のローソク足

        Returns:
            PoolEvaluationResult: 評価結果
        """
        result = PoolEvaluationResult(
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            regime=context.regime,
        )

        for strategy in self._strategies:
            proposal = strategy.evaluate(context, tf_data, candle)
            result.proposals.append(proposal)

        return result

    def evaluate_single(
        self,
        strategy_id: StrategyId,
        context: StrategyContext,
        tf_data: dict[str, pd.DataFrame],
        candle: Candle | None = None,
    ):
        """単一戦略を評価

        ポジション保有中の継続評価用。

        Args:
            strategy_id: 戦略識別子
            context: 戦略コンテキスト
            tf_data: 時間足別データ
            candle: 現在のローソク足

        Returns:
            ProposedTrade | None: 提案（戦略が存在しない場合None）
        """
        strategy = self.get_strategy(strategy_id)
        if strategy is None:
            return None

        return strategy.evaluate(context, tf_data, candle)
