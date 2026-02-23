"""NoTrade戦略

市場条件が不利な場合に「取引しない」を積極的に選択する戦略。
RANGE+高ペナルティ時にedge_scoreを高く返し、他戦略より優先させる。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from autotrader.core.enums import MarketRegime, SignalType

from .base import BaseStrategy, StrategyConfig
from .in_strategy_consensus import InStrategyConsensusConfig
from .types import (
    EdgeScoreComponents,
    InStrategyConsensusResult,
    ProposedTrade,
    StrategyContext,
    StrategyId,
    StrategyTimeframes,
)

if TYPE_CHECKING:
    from autotrader.core.entities import Candle


# NoTrade専用の最小TFセット（評価はしないがインターフェース互換）
_NOTRADE_TIMEFRAMES = StrategyTimeframes(
    primary_tf="M15",
    entry_tf="M15",
    confirm_tfs=(),
    htf_refs=(),
    htf_weight=0.0,
    tp_sl_ratio_range=(1.0, 1.0),
)

_NOTRADE_CONFIG = StrategyConfig(
    min_edge_score=0.0,
    max_spread_atr_ratio=1.0,
    allowed_hours_utc=None,
    regime_weights={
        MarketRegime.HIGH_VOL: 1.0,
        MarketRegime.TREND: 1.0,
        MarketRegime.RANGE: 1.0,
        MarketRegime.LOW_VOL: 1.0,
    },
)

_NOTRADE_CONSENSUS = InStrategyConsensusConfig(
    primary_weight=1.0,
    entry_weight=1.0,
    confirm_weight=1.0,
    htf_ref_weight=1.0,
    min_confidence=0.0,
    score_margin_required=0.0,
)


class NoTradeStrategy(BaseStrategy):
    """NoTrade戦略

    市場条件が悪い時に「取引しない」を選択肢として提供する。
    SoftGuardペナルティが高い時 + RANGE時にedge_scoreを高く返し、
    他戦略のシグナルより「見送り」が勝つようにする。
    """

    def __init__(self) -> None:
        """初期化"""
        super().__init__(
            config=_NOTRADE_CONFIG,
            consensus_config=_NOTRADE_CONSENSUS,
        )

    @property
    def strategy_id(self) -> StrategyId:
        """戦略識別子を返す"""
        return StrategyId.NO_TRADE

    @property
    def timeframes(self) -> StrategyTimeframes:
        """時間足設定を返す"""
        return _NOTRADE_TIMEFRAMES

    def _get_regime_fit_factor(self, regime: MarketRegime) -> float:
        """レジーム適合係数を返す"""
        return 1.0

    def evaluate(
        self,
        context: StrategyContext,
        tf_data: dict[str, pd.DataFrame],
        candle: Candle | None = None,
    ) -> ProposedTrade:
        """NoTrade評価

        SoftGuardペナルティとレジームから「見送りの価値」を算出する。
        ペナルティが高い or RANGE時はedge_score↑ → 取引抑制。

        Args:
            context: 戦略コンテキスト
            tf_data: 時間足別データ
            candle: 現在のローソク足

        Returns:
            ProposedTrade: HOLD提案（edge_score付き）
        """
        # ソフトガード係数を計算
        sg_factor = self._calculate_soft_guard_factor(context)
        # ペナルティが大きいほどNoTradeのedge_scoreが高い
        penalty = 1.0 - sg_factor

        # RANGE時ボーナス
        regime = context.regime
        regime_bonus = 0.0
        if regime == MarketRegime.RANGE:
            trend_strength = context.regime_result.trend_strength
            # トレンドが弱いほどNoTradeの価値が高い
            regime_bonus = 0.3 * (1.0 - trend_strength)
        elif regime == MarketRegime.LOW_VOL:
            regime_bonus = 0.15

        # NoTradeのedge_score: ペナルティ + レジームボーナス
        # 他戦略のedge_scoreは通常0.1-0.3程度
        # NoTradeが0.2を超えると有力候補になる
        notrade_score = min(1.0, penalty * 0.5 + regime_bonus)

        _ptf = self.timeframes.primary_tf
        hold_consensus = InStrategyConsensusResult(
            direction=SignalType.HOLD,
            primary_tf=_ptf,
            aligned_tfs=(),
            total_score=0.0,
            buy_score=0.0,
            sell_score=0.0,
            confidence=notrade_score,
            reasoning="NoTrade: 見送り判断",
        )

        edge_components = EdgeScoreComponents(
            base_confidence=notrade_score,
            score_margin_factor=1.0,
            regime_fit_factor=1.0,
            cost_factor=1.0,
            htf_conflict_factor=1.0,
            soft_guard_factor=1.0,
        )

        return ProposedTrade(
            strategy_id=StrategyId.NO_TRADE,
            direction=SignalType.HOLD,
            edge_score=notrade_score,
            edge_components=edge_components,
            consensus=hold_consensus,
            primary_tf=_ptf,
            sl_pips=0.0,
            tp_pips=0.0,
            reasoning=(
                f"NoTrade: penalty={penalty:.2f} "
                f"regime_bonus={regime_bonus:.2f} "
                f"score={notrade_score:.2%}"
            ),
        )
