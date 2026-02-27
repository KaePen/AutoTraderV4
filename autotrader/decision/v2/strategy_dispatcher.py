"""戦略ディスパッチャーモジュール。

レジームに応じて適切な戦略を選択・実行する。
各レジームには1つの戦略のみが割り当てられる。
"""

from __future__ import annotations

import logging

from autotrader.decision.v2.config import V2BotConfig
from autotrader.decision.v2.market_context import MarketContext
from autotrader.decision.v2.regime_classifier import (
    MarketRegimeV2,
)
from autotrader.decision.v2.strategies.base import (
    V2EntrySignal,
    V2StrategyBase,
)
from autotrader.decision.v2.strategies.breakout import (
    BreakoutStrategy,
)
from autotrader.decision.v2.strategies.range_revert import (
    RangeRevertStrategy,
)
from autotrader.decision.v2.strategies.trend_follow import (
    TrendFollowStrategy,
)

logger = logging.getLogger(__name__)


class StrategyDispatcher:
    """レジーム→戦略のルーティング。

    各レジームに対して1つの戦略を割り当て、
    現在のレジームに応じた戦略のevaluateを呼び出す。

    ルーティングルール:
    - TRENDING → TrendFollow
    - RANGING  → RangeRevert
    - QUIET    → Breakout
    - VOLATILE → NoTrade (None返却)

    Args:
        config: V2ボット設定。
    """

    def __init__(self, config: V2BotConfig) -> None:
        pip_unit = config.pip_unit
        self._strategies: dict[
            MarketRegimeV2, V2StrategyBase
        ] = {
            MarketRegimeV2.TRENDING: TrendFollowStrategy(
                config=config.trend_follow,
                pip_unit=pip_unit,
            ),
            MarketRegimeV2.RANGING: RangeRevertStrategy(
                config=config.range_revert,
                pip_unit=pip_unit,
            ),
            MarketRegimeV2.QUIET: BreakoutStrategy(
                config=config.breakout,
                pip_unit=pip_unit,
            ),
            # VOLATILE → 戦略なし
        }

    @property
    def breakout_strategy(self) -> BreakoutStrategy:
        """BreakoutStrategy への直接参照。

        quiet_bars カウンタ更新に使用。
        """
        strat = self._strategies[MarketRegimeV2.QUIET]
        assert isinstance(strat, BreakoutStrategy)
        return strat

    def dispatch(
        self,
        regime: MarketRegimeV2,
        ctx: MarketContext,
    ) -> V2EntrySignal | None:
        """レジームに対応する戦略を実行。

        Args:
            regime: 現在の市場レジーム。
            ctx: 現在の市場コンテキスト。

        Returns:
            戦略がシグナルを生成した場合V2EntrySignal、
            VOLATILE or シグナルなしの場合None。
        """
        strategy = self._strategies.get(regime)
        if strategy is None:
            logger.debug(
                "NoTrade: レジーム %s に対応する戦略なし",
                regime.value,
            )
            return None

        signal = strategy.evaluate(ctx)
        if signal is not None:
            logger.debug(
                "%s シグナル: %s conf=%.2f %s",
                strategy.name,
                signal.direction.value,
                signal.confidence,
                signal.reasoning,
            )
        return signal

    def get_strategy(
        self, regime: MarketRegimeV2,
    ) -> V2StrategyBase | None:
        """指定レジームの戦略を取得。"""
        return self._strategies.get(regime)
