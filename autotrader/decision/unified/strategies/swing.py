"""スイング戦略

H1を主要時間足とし、H4でエントリー、D1で確認する長期戦略。
高品質シグナルのみエントリー。
"""

from __future__ import annotations

from autotrader.core.enums import MarketRegime

from .base import BaseStrategy, StrategyConfig
from .in_strategy_consensus import InStrategyConsensusConfig
from .types import StrategyId, StrategyTimeframes


class SwingStrategy(BaseStrategy):
    """スイング戦略

    特徴:
    - TFセット: H1(primary), H4(entry), D1(confirm)
    - htf_weight: 0.8（強い参照）
    - TP/SL比率: 1.2-2.0
    - 適合レジーム: TREND=1.4x
    """

    # 戦略固有のデフォルト設定
    DEFAULT_CONFIG = StrategyConfig(
        min_edge_score=0.15,  # 緩和
        max_spread_atr_ratio=0.35,
        allowed_hours_utc=None,
        regime_weights={
            MarketRegime.HIGH_VOL: 0.8,
            MarketRegime.TREND: 1.4,
            MarketRegime.RANGE: 0.1,  # RANGE時はほぼ候補外
            MarketRegime.LOW_VOL: 0.5,
        },
    )

    # 戦略固有の時間足設定
    TIMEFRAMES = StrategyTimeframes(
        primary_tf="H1",
        entry_tf="H4",
        confirm_tfs=("D1",),
        htf_refs=("D1",),
        htf_weight=0.8,
        tp_sl_ratio_range=(1.2, 1.6),  # 勝率54%・PF1.01達成
    )

    # 戦略固有のコンセンサス設定
    DEFAULT_CONSENSUS_CONFIG = InStrategyConsensusConfig(
        primary_weight=3.0,
        entry_weight=2.5,
        confirm_weight=2.0,
        htf_ref_weight=2.0,
        min_confidence=0.50,
        score_margin_required=0.20,
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
        return StrategyId.SWING

    @property
    def timeframes(self) -> StrategyTimeframes:
        """時間足設定を返す"""
        return self.TIMEFRAMES

    def _get_regime_fit_factor(self, regime: MarketRegime) -> float:
        """レジーム適合係数を返す"""
        weights = self.config.regime_weights or self.DEFAULT_CONFIG.regime_weights
        if weights:
            return weights.get(regime, 1.0)
        return 1.0
