"""スキャルピング戦略

M5を主要時間足とし、M1でタイミングを取る短期戦略。
勝率重視でTP/SL比率を1:1に近く設定。
"""

from __future__ import annotations

from autotrader.core.enums import MarketRegime

from .base import BaseStrategy, StrategyConfig
from .in_strategy_consensus import InStrategyConsensusConfig
from .registry import register_strategy
from .types import StrategyId, StrategyTimeframes


@register_strategy("scalp")
class ScalpStrategy(BaseStrategy):
    """スキャルピング戦略

    特徴:
    - TFセット: M5(primary), M1(entry), M15(confirm), H1(htf_ref)
    - htf_weight: 0.5（HTF衝突時はブロック）
    - TP/SL比率: 0.9-1.3（勝率重視）
    - 適合レジーム: TREND=1.2x
    """

    # 戦略固有のデフォルト設定
    DEFAULT_CONFIG = StrategyConfig(
        min_edge_score=0.10,
        max_spread_atr_ratio=0.30,
        allowed_hours_utc=None,
        regime_weights={
            MarketRegime.HIGH_VOL: 1.1,
            MarketRegime.TREND: 1.2,
            MarketRegime.RANGE: 0.8,  # RANGE時のScalp優位
            MarketRegime.LOW_VOL: 0.5,
        },
    )

    # 戦略固有の時間足設定
    TIMEFRAMES = StrategyTimeframes(
        primary_tf="M5",
        entry_tf="M1",
        confirm_tfs=("M15",),
        htf_refs=("H1",),
        htf_weight=0.5,  # HTF衝突時はブロック
        tp_sl_ratio_range=(1.0, 1.3),  # 勝率56%・PF1.01達成
    )

    # 戦略固有のコンセンサス設定
    DEFAULT_CONSENSUS_CONFIG = InStrategyConsensusConfig(
        primary_weight=3.0,
        entry_weight=2.5,
        confirm_weight=1.5,
        htf_ref_weight=1.2,
        min_confidence=0.40,
        score_margin_required=0.15,
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
        return StrategyId.SCALP

    @property
    def timeframes(self) -> StrategyTimeframes:
        """時間足設定を返す"""
        return self.TIMEFRAMES

