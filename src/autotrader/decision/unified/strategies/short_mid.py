"""短中期戦略

M15を主要時間足とし、H1でエントリー、H4で確認する中期戦略。
勝率重視でTP/SL比率を1:1に近く設定。
"""

from __future__ import annotations

from autotrader.core.enums import MarketRegime

from .base import BaseStrategy, StrategyConfig
from .in_strategy_consensus import InStrategyConsensusConfig
from .types import StrategyContext, StrategyId, StrategyTimeframes


class ShortMidStrategy(BaseStrategy):
    """短中期戦略

    特徴:
    - TFセット: M15(primary), H1(entry), H4(confirm)
    - htf_weight: 0.5（中程度）
    - TP/SL比率: 1.0-1.5
    - 適合レジーム: TREND=1.3x
    """

    # 戦略固有のデフォルト設定
    DEFAULT_CONFIG = StrategyConfig(
        min_edge_score=0.12,  # 緩和
        max_spread_atr_ratio=0.30,
        allowed_hours_utc=None,
        regime_weights={
            MarketRegime.HIGH_VOL: 1.0,
            MarketRegime.TREND: 1.3,
            MarketRegime.RANGE: 0.2,  # RANGE時はDAY抑制
            MarketRegime.LOW_VOL: 0.3,  # 低ボラ時もDAY抑制
        },
    )

    # 戦略固有の時間足設定
    TIMEFRAMES = StrategyTimeframes(
        primary_tf="M15",
        entry_tf="H1",
        confirm_tfs=("H4",),
        htf_refs=("H4",),
        htf_weight=0.5,
        tp_sl_ratio_range=(1.1, 1.4),  # 勝率55%・PF1.01達成
    )

    # 戦略固有のコンセンサス設定
    DEFAULT_CONSENSUS_CONFIG = InStrategyConsensusConfig(
        primary_weight=3.0,
        entry_weight=2.5,
        confirm_weight=1.8,
        htf_ref_weight=1.5,
        min_confidence=0.45,
        score_margin_required=0.18,
    )

    def __init__(
        self,
        config: StrategyConfig | None = None,
        consensus_config: InStrategyConsensusConfig | None = None,
    ) -> None:
        """初期化

        Args:
            config: 戦略設定
            consensus_config: コンセンサス設定
        """
        super().__init__(
            config=config or self.DEFAULT_CONFIG,
            consensus_config=consensus_config or self.DEFAULT_CONSENSUS_CONFIG,
        )

    @property
    def strategy_id(self) -> StrategyId:
        """戦略識別子を返す"""
        return StrategyId.SHORT_MID

    @property
    def timeframes(self) -> StrategyTimeframes:
        """時間足設定を返す"""
        return self.TIMEFRAMES

    def _passes_pre_filters(self, context: StrategyContext) -> bool:
        """RANGE時のDAY戦略制限

        RANGE + トレンド弱 → DAY不発動

        Args:
            context: 戦略コンテキスト

        Returns:
            bool: 通過する場合True
        """
        if not super()._passes_pre_filters(context):
            return False

        # RANGE時はトレンドが明確な場合のみ許可
        regime = context.regime
        if regime == MarketRegime.RANGE:
            trend_strength = context.regime_result.trend_strength
            if trend_strength < 0.5:
                return False

        return True

    def _get_regime_fit_factor(self, regime: MarketRegime) -> float:
        """レジーム適合係数を返す"""
        weights = self.config.regime_weights or self.DEFAULT_CONFIG.regime_weights
        if weights:
            return weights.get(regime, 1.0)
        return 1.0
