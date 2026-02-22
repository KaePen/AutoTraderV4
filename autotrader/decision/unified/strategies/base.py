"""戦略基底クラス

全戦略（Scalp, ShortMid, Swing）の共通インターフェースと基本実装。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from autotrader.constraint.soft_guard import SoftGuard
from autotrader.core.enums import MarketRegime, SignalType

from .in_strategy_consensus import InStrategyConsensus, InStrategyConsensusConfig
from .types import (
    EdgeScoreComponents,
    ProposedTrade,
    StrategyContext,
    StrategyId,
    StrategyTimeframes,
)

if TYPE_CHECKING:
    from autotrader.core.entities import Candle
    from autotrader.decision.unified.timeframe_evaluator import (
        TimeframeEvaluator,
        TimeframeSignal,
    )


@dataclass(frozen=True)
class StrategyConfig:
    """戦略基本設定

    Attributes:
        min_edge_score: 最小エッジスコア閾値
        max_spread_atr_ratio: 最大スプレッド/ATR比率
        allowed_hours_utc: 許可するUTC時間帯（None=全時間帯）
        regime_weights: レジーム別適合係数
        min_confidence: コンセンサス最低確度（0.0-1.0）
    """

    min_edge_score: float = 0.2
    max_spread_atr_ratio: float = 0.3
    allowed_hours_utc: tuple[int, ...] | None = None
    regime_weights: dict[MarketRegime, float] | None = None
    min_confidence: float = 0.0


class BaseStrategy(ABC):
    """戦略基底クラス

    各戦略はこのクラスを継承し、以下を実装:
    - strategy_id: 戦略識別子
    - timeframes: 時間足設定
    - evaluate(): 提案生成
    """

    def __init__(
        self,
        config: StrategyConfig | None = None,
        consensus_config: InStrategyConsensusConfig | None = None,
        soft_guard: SoftGuard | None = None,
    ) -> None:
        """初期化

        Args:
            config: 戦略設定
            consensus_config: コンセンサス設定
            soft_guard: ソフトガードインスタンス
        """
        self.config = config or StrategyConfig()
        self._consensus = InStrategyConsensus(
            strategy_id=self.strategy_id,
            strategy_tfs=self.timeframes,
            config=consensus_config,
        )
        self._evaluators: dict[str, TimeframeEvaluator] = {}
        self._soft_guard = soft_guard or SoftGuard()

    @property
    @abstractmethod
    def strategy_id(self) -> StrategyId:
        """戦略識別子を返す"""
        ...

    @property
    @abstractmethod
    def timeframes(self) -> StrategyTimeframes:
        """時間足設定を返す"""
        ...

    @abstractmethod
    def _get_regime_fit_factor(self, regime: MarketRegime) -> float:
        """レジーム適合係数を返す

        Args:
            regime: 現在のレジーム

        Returns:
            float: 適合係数（1.0=標準、>1.0=有利、<1.0=不利）
        """
        ...

    def set_evaluators(
        self, evaluators: dict[str, TimeframeEvaluator]
    ) -> None:
        """時間足評価器を設定

        Args:
            evaluators: 時間足 -> 評価器のマップ
        """
        self._evaluators = evaluators

    def evaluate(
        self,
        context: StrategyContext,
        tf_data: dict[str, pd.DataFrame],
        candle: Candle | None = None,
    ) -> ProposedTrade:
        """戦略を評価して提案を生成

        Args:
            context: 戦略コンテキスト
            tf_data: 時間足別データ
            candle: 現在のローソク足

        Returns:
            ProposedTrade: 提案トレード
        """
        # 事前フィルター
        if not self._passes_pre_filters(context):
            return self._create_hold_proposal("事前フィルター不通過", context)

        # 各時間足のシグナルを評価
        tf_signals = self._evaluate_timeframes(tf_data, candle)
        if not tf_signals:
            return self._create_hold_proposal("シグナル評価失敗", context)

        # 戦略内コンセンサス
        consensus = self._consensus.consolidate(tf_signals)
        if consensus.direction == SignalType.HOLD:
            return self._create_hold_proposal(consensus.reasoning, context)

        # HTF整合性チェック
        htf_factor = self._calculate_htf_factor(
            consensus.direction, tf_signals
        )

        # エッジスコア構成要素を計算
        edge_components = self._calculate_edge_components(
            context, consensus, htf_factor
        )

        # 最小エッジスコアチェック
        if edge_components.edge_score < self.config.min_edge_score:
            return self._create_hold_proposal(
                f"エッジスコア不足: {edge_components.edge_score:.2%}",
                context,
            )

        # SL/TP計算
        sl_tp = self._calculate_sl_tp(tf_signals, context)
        if sl_tp is None:
            return self._create_hold_proposal(
                "実効TP/SL比率不足（コスト考慮後<1.0）", context
            )
        sl_pips, tp_pips = sl_tp

        reasoning = (
            f"{self.strategy_id.value}: {consensus.direction.value} "
            f"edge={edge_components.edge_score:.2%} "
            f"htf_factor={htf_factor:.2f}"
        )

        return ProposedTrade(
            strategy_id=self.strategy_id,
            direction=consensus.direction,
            edge_score=edge_components.edge_score,
            edge_components=edge_components,
            consensus=consensus,
            primary_tf=self.timeframes.primary_tf,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            reasoning=reasoning,
        )

    def _passes_pre_filters(self, context: StrategyContext) -> bool:
        """事前フィルターを通過するか

        Args:
            context: 戦略コンテキスト

        Returns:
            bool: 通過する場合True
        """
        # 時間帯フィルター
        if self.config.allowed_hours_utc is not None:
            if context.hour_utc not in self.config.allowed_hours_utc:
                return False

        # スプレッドフィルター（簡易版、詳細は戦略別で実装可能）
        # context.spread_pipsのチェックはATRが必要なので後段で

        return True

    def _evaluate_timeframes(
        self,
        tf_data: dict[str, pd.DataFrame],
        candle: Candle | None,
    ) -> dict[str, TimeframeSignal]:
        """各時間足を評価

        Args:
            tf_data: 時間足別データ
            candle: ローソク足

        Returns:
            dict[str, TimeframeSignal]: 時間足別シグナル
        """
        signals: dict[str, TimeframeSignal] = {}

        for tf in self.timeframes.all_tfs:
            if tf not in self._evaluators:
                continue

            df = tf_data.get(tf)
            if df is None or df.empty:
                continue

            evaluator = self._evaluators[tf]
            row = df.iloc[-1]

            signal = evaluator.evaluate(row, candle)
            signals[tf] = signal

        return signals

    def _calculate_htf_factor(
        self,
        direction: SignalType,
        tf_signals: dict[str, TimeframeSignal],
    ) -> float:
        """HTF整合係数を計算（厳格版）

        HTFが明確にエントリー方向と逆の場合は0を返す。

        Args:
            direction: 決定方向
            tf_signals: 時間足別シグナル

        Returns:
            float: HTF整合係数（0.0-1.0）
        """
        htf_refs = self.timeframes.htf_refs
        htf_weight = self.timeframes.htf_weight

        if not htf_refs or htf_weight == 0.0:
            return 1.0

        aligned_count = 0
        conflict_count = 0

        for htf in htf_refs:
            if htf not in tf_signals:
                continue

            htf_signal = tf_signals[htf]
            if htf_signal.direction == direction:
                aligned_count += 1
            elif htf_signal.direction != SignalType.HOLD:
                conflict_count += 1

        total_htf = aligned_count + conflict_count
        if total_htf == 0:
            return 1.0

        # HTFと衝突がある場合のペナルティ
        if conflict_count > 0:
            if conflict_count >= 2:
                # 複数HTFが逆行 → 完全ブロック
                return 0.0
            # 単一HTF逆行 → 減衰（htf_weightに応じて）
            decay = 0.5 - htf_weight * 0.25
            return max(0.3, decay)

        # 整合率を計算
        alignment_ratio = aligned_count / total_htf

        # htf_weightで影響度を調整
        factor = 1.0 - htf_weight * (1.0 - alignment_ratio)

        return max(0.0, min(1.0, factor))

    def _calculate_edge_components(
        self,
        context: StrategyContext,
        consensus,
        htf_factor: float,
    ) -> EdgeScoreComponents:
        """エッジスコア構成要素を計算

        Args:
            context: 戦略コンテキスト
            consensus: コンセンサス結果
            htf_factor: HTF整合係数

        Returns:
            EdgeScoreComponents: エッジスコア構成要素
        """
        # 基本確度
        base_confidence = consensus.confidence

        # スコアマージン係数
        total = consensus.buy_score + consensus.sell_score
        if total > 0:
            dominant = max(consensus.buy_score, consensus.sell_score)
            margin = (dominant - min(consensus.buy_score, consensus.sell_score))
            score_margin_factor = min(1.0 + margin / total, 1.2)
        else:
            score_margin_factor = 1.0

        # レジーム適合係数
        regime_fit_factor = self._get_regime_fit_factor(context.regime)

        # コスト係数（スプレッド考慮）
        cost_factor = self._calculate_cost_factor(context)

        # ソフトガード係数
        soft_guard_factor = self._calculate_soft_guard_factor(
            context
        )

        return EdgeScoreComponents(
            base_confidence=base_confidence,
            score_margin_factor=score_margin_factor,
            regime_fit_factor=regime_fit_factor,
            cost_factor=cost_factor,
            htf_conflict_factor=htf_factor,
            soft_guard_factor=soft_guard_factor,
        )

    def _calculate_cost_factor(self, context: StrategyContext) -> float:
        """コスト係数を計算

        Args:
            context: 戦略コンテキスト

        Returns:
            float: コスト係数（0.5-1.0）
        """
        # スプレッドが広いほど係数が下がる
        # 基準: 1.0pip = 1.0、3.0pip以上 = 0.5
        spread = context.spread_pips
        if spread <= 1.0:
            return 1.0
        elif spread >= 3.0:
            return 0.5
        else:
            return 1.0 - (spread - 1.0) * 0.25

    def _calculate_soft_guard_factor(
        self, context: StrategyContext
    ) -> float:
        """ソフトガード係数を計算

        SoftGuard.check()のペナルティをedge_scoreに反映する。

        Args:
            context: 戦略コンテキスト

        Returns:
            float: ソフトガード係数（0.1-1.0）
        """
        from datetime import datetime, timezone

        sg_context = {
            "spread_pips": context.spread_pips,
            "current_time": datetime(
                2023, 1, 1, context.hour_utc, tzinfo=timezone.utc,
            ),
        }
        result = self._soft_guard.check(sg_context, is_entry=True)
        return max(0.1, 1.0 - result.total_penalty)

    # スリッページ想定（pips）
    SLIPPAGE_PIPS: float = 0.5

    def _calculate_sl_tp(
        self,
        tf_signals: dict[str, TimeframeSignal],
        context: StrategyContext,
    ) -> tuple[float, float] | None:
        """SL/TP距離を計算

        実効TP/SL比率（スプレッド・スリッページ考慮後）が
        1.0未満の場合はNoneを返す（HOLD扱い）。

        Args:
            tf_signals: 時間足別シグナル
            context: 戦略コンテキスト

        Returns:
            tuple[float, float] | None: (SL pips, TP pips)。
                実効比率不足時はNone。
        """
        primary_tf = self.timeframes.primary_tf

        if primary_tf in tf_signals:
            primary_signal = tf_signals[primary_tf]
            sl_pips = primary_signal.sl_pips
            tp_pips = primary_signal.tp_pips
        else:
            # フォールバック
            sl_pips = 20.0
            tp_pips = 40.0

        # TP/SL比率の範囲内に収める
        min_ratio, max_ratio = self.timeframes.tp_sl_ratio_range
        current_ratio = tp_pips / max(sl_pips, 0.01)

        if current_ratio < min_ratio:
            tp_pips = sl_pips * min_ratio
        elif current_ratio > max_ratio:
            tp_pips = sl_pips * max_ratio

        # 実効TP/SL比率チェック（スプレッド+スリッページ考慮）
        cost_pips = context.spread_pips + self.SLIPPAGE_PIPS
        effective_tp = tp_pips - cost_pips
        effective_sl = sl_pips + cost_pips
        if effective_sl > 0 and effective_tp / effective_sl < 0.8:
            return None

        return sl_pips, tp_pips

    def _create_hold_proposal(
        self,
        reason: str,
        context: StrategyContext,
    ) -> ProposedTrade:
        """HOLD提案を作成

        Args:
            reason: 理由
            context: コンテキスト

        Returns:
            ProposedTrade: HOLD提案
        """
        from .types import InStrategyConsensusResult

        hold_consensus = InStrategyConsensusResult(
            direction=SignalType.HOLD,
            primary_tf=self.timeframes.primary_tf,
            aligned_tfs=(),
            total_score=0.0,
            buy_score=0.0,
            sell_score=0.0,
            confidence=0.0,
            reasoning=reason,
        )

        hold_components = EdgeScoreComponents(
            base_confidence=0.0,
            score_margin_factor=1.0,
            regime_fit_factor=1.0,
            cost_factor=1.0,
            htf_conflict_factor=1.0,
        )

        return ProposedTrade(
            strategy_id=self.strategy_id,
            direction=SignalType.HOLD,
            edge_score=0.0,
            edge_components=hold_components,
            consensus=hold_consensus,
            primary_tf=self.timeframes.primary_tf,
            sl_pips=0.0,
            tp_pips=0.0,
            reasoning=f"{self.strategy_id.value}: HOLD - {reason}",
        )
