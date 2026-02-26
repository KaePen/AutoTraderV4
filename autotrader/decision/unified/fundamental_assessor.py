"""ファンダメンタルリスク評価器

FundamentalContext（短期）と FundamentalMemorySnapshot（中長期）
を統合し、トレードロジック向けの評価結果を生成する。

設計原則:
- 相関要因は max() で統合、独立要因は sum()（破綻2修正）
- PRE/POST/CONVERGING/NORMAL のイベントフェーズ区別（破綻3修正）
- 閾値調整に上限（破綻4修正）
- 矛盾検知（破綻6修正）
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from autotrader.adapters.fundamental.schemas import (
    FundamentalContext,
    FundamentalMemorySnapshot,
)


class EventPhase(str, Enum):
    """イベントフェーズ"""

    PRE_EVENT = "pre_event"
    POST_EVENT = "post_event"
    CONVERGING = "converging"
    NORMAL = "normal"


class RiskCategory(str, Enum):
    """リスクカテゴリ"""

    NORMAL = "normal"
    CAUTION = "caution"
    HIGH = "high"
    BLOCK = "block"


@dataclass(frozen=True)
class FundamentalAssessorConfig:
    """アセッサー設定

    Attributes:
        caution_threshold: CAUTIONレベル閾値
        high_threshold: HIGHレベル閾値
        block_threshold: BLOCKレベル閾値
        caution_base: event_caution_level=1 の基本リスク
        vol_high_threshold: 高ボラ閾値
        vol_risk_scale: ボラリスクスケール
        convergence_risk_threshold: 収束リスク閾値
        cluster_risk_per_event: イベント密集リスク（1件あたり）
        cluster_min_count: 密集判定最小件数
        holiday_risk_scale: 祝日リスクスケール
        disagreement_threshold: 矛盾検知閾値
        disagreement_risk: 矛盾リスク加算値
        disagreement_confidence_scale: 矛盾時確信度スケール
        conviction_boost_max: コンビクションブースト最大値
        max_threshold_raise: 閾値上げ上限
        max_threshold_lower: 閾値下げ上限
        direction_penalty_scale: 逆行ペナルティスケール
    """

    # リスクレベル閾値
    caution_threshold: float = 0.4
    high_threshold: float = 0.7
    block_threshold: float = 0.9

    # 相関グループ: イベントリスク（max()で統合）
    # caution_level=1: 中程度注意（閾値未達でも意識する程度）
    caution_base: float = 0.2
    vol_high_threshold: float = 1.5
    vol_risk_scale: float = 0.15
    convergence_risk_threshold: float = 0.8
    convergence_risk_scale: float = 0.25

    # 独立: イベント密集リスク
    cluster_risk_per_event: float = 0.05
    cluster_min_count: int = 3

    # 独立: 祝日リスク
    holiday_risk_scale: float = 0.25

    # 独立: 矛盾リスク
    disagreement_threshold: float = 0.8
    disagreement_risk: float = 0.1
    disagreement_confidence_scale: float = 0.5

    # 閾値調整（控えめに: max +2.0, -0.5）
    conviction_boost_max: float = 0.5
    max_threshold_raise: float = 2.0
    max_threshold_lower: float = 0.5
    direction_penalty_scale: float = 2.0


@dataclass(frozen=True)
class FundamentalAssessment:
    """ファンダメンタル評価結果

    Attributes:
        risk_level: 総合リスクレベル (0.0-1.0)
        risk_category: リスクカテゴリ
        effective_bias: 統合方向バイアス (-1~+1)
        bias_confidence: バイアス確信度 (0-1)
        event_dominance: イベント支配度 (0-1)
        event_phase: イベントフェーズ
        disagreement: 矛盾度
        lot_multiplier: ロット倍率 (0.2-1.0)
        trailing_sl_multiplier: トレーリングSL倍率
    """

    risk_level: float
    risk_category: RiskCategory
    effective_bias: float
    bias_confidence: float
    event_dominance: float
    event_phase: EventPhase
    disagreement: float
    lot_multiplier: float = 1.0
    trailing_sl_multiplier: float = 1.0

    # 内部パラメータ（閾値調整計算用）
    _config: FundamentalAssessorConfig | None = None

    def get_threshold_adjustment(
        self,
        signal_direction: float,
    ) -> float:
        """シグナル方向に対する閾値調整値を計算

        正: 閾値引き上げ（エントリー困難化）
        負: 閾値引き下げ（コンビクションブースト）

        Args:
            signal_direction: シグナル方向 (+1=BUY, -1=SELL)

        Returns:
            float: 閾値調整値
        """
        cfg = self._config or FundamentalAssessorConfig()

        # リスクレベルによる基本調整
        if self.risk_category == RiskCategory.NORMAL:
            risk_adj = 0.0
        elif self.risk_category == RiskCategory.CAUTION:
            # 0.4-0.7 → 0.1-0.3（控えめ: 方向調整を主因子にする）
            t = (self.risk_level - cfg.caution_threshold) / (
                cfg.high_threshold - cfg.caution_threshold
            )
            risk_adj = 0.1 + t * 0.2
        elif self.risk_category == RiskCategory.HIGH:
            # 0.7-0.9 → 0.5-1.0
            t = (self.risk_level - cfg.high_threshold) / (
                cfg.block_threshold - cfg.high_threshold
            )
            risk_adj = 0.5 + t * 0.5
        else:
            # BLOCK: 事実上エントリー不可
            return cfg.max_threshold_raise

        # 方向バイアスによる調整
        alignment = signal_direction * self.effective_bias
        direction_adj = 0.0
        if (
            self.bias_confidence > 0.2
            and abs(self.effective_bias) > 0.15
        ):
            if alignment < 0:
                # 逆行: event_dominanceが高いほど強くペナルティ
                direction_adj = (
                    abs(alignment)
                    * self.bias_confidence
                    * max(self.event_dominance, 0.3)
                    * cfg.direction_penalty_scale
                )
            else:
                # 一致: risk_adjを方向一致度で軽減
                risk_adj *= max(
                    1.0 - alignment * 0.7, 0.0,
                )
                # コンビクションブースト
                direction_adj = -(
                    min(alignment, 1.0)
                    * self.bias_confidence
                    * cfg.conviction_boost_max
                )

        total = risk_adj + direction_adj
        # 上限/下限の適用（破綻4修正）
        return max(
            -cfg.max_threshold_lower,
            min(total, cfg.max_threshold_raise),
        )


class FundamentalRiskAssessor:
    """ファンダメンタルリスク評価器

    FundamentalContext（短期スナップショット）と
    FundamentalMemorySnapshot（中長期蓄積）を統合評価する。

    Args:
        config: アセッサー設定
    """

    def __init__(
        self,
        config: FundamentalAssessorConfig | None = None,
    ) -> None:
        self.config = config or FundamentalAssessorConfig()

    def assess(
        self,
        ctx: FundamentalContext,
        memory: FundamentalMemorySnapshot,
    ) -> FundamentalAssessment:
        """ファンダメンタル評価を実行

        Args:
            ctx: 短期ファンダメンタルコンテキスト
            memory: 中長期メモリスナップショット

        Returns:
            FundamentalAssessment: 評価結果
        """
        # 1. イベントフェーズ判定
        phase = self._determine_phase(ctx)

        # 2. リスクレベル計算
        risk = self._calculate_risk(ctx, memory)

        # 3. リスクカテゴリ判定
        category = self._categorize_risk(risk)

        # 4. event_dominance 計算
        dominance = self._calculate_event_dominance(ctx)

        # 5. 統合バイアス計算
        eff_bias, eff_conf = self._calculate_effective_bias(
            ctx, memory, dominance,
        )

        # 6. ロット倍率計算
        lot_mult = self._calculate_lot_multiplier(
            risk, ctx,
        )

        # 7. トレーリングSL倍率
        sl_mult = self._calculate_trailing_sl_mult(ctx)

        return FundamentalAssessment(
            risk_level=risk,
            risk_category=category,
            effective_bias=eff_bias,
            bias_confidence=eff_conf,
            event_dominance=dominance,
            event_phase=phase,
            disagreement=memory.disagreement,
            lot_multiplier=lot_mult,
            trailing_sl_multiplier=sl_mult,
            _config=self.config,
        )

    def _determine_phase(
        self, ctx: FundamentalContext,
    ) -> EventPhase:
        """イベントフェーズ判定"""
        if ctx.has_high_impact_within_30min:
            return EventPhase.PRE_EVENT

        if ctx.event_caution_level >= 1:
            if ctx.convergence_progress < 0.3:
                return EventPhase.POST_EVENT
            if ctx.convergence_progress < 0.8:
                return EventPhase.CONVERGING

        return EventPhase.NORMAL

    def _calculate_risk(
        self,
        ctx: FundamentalContext,
        memory: FundamentalMemorySnapshot,
    ) -> float:
        """リスクレベル計算

        相関要因は max()、独立要因は sum() で統合。
        """
        cfg = self.config

        # --- 相関グループ（単一イベントの複数兆候）→ max ---
        caution_risk = (
            cfg.caution_base
            if ctx.event_caution_level >= 1
            else 0.0
        )

        vol_risk = 0.0
        if ctx.volatility_multiplier > cfg.vol_high_threshold:
            vol_risk = min(
                (ctx.volatility_multiplier
                 - cfg.vol_high_threshold)
                * cfg.vol_risk_scale,
                0.35,
            )

        convergence_risk = 0.0
        if ctx.convergence_progress < cfg.convergence_risk_threshold:
            convergence_risk = (
                (cfg.convergence_risk_threshold
                 - ctx.convergence_progress)
                * cfg.convergence_risk_scale
            )

        event_risk = max(
            caution_risk, vol_risk, convergence_risk,
        )

        # --- 独立要因 → sum ---
        cluster_risk = 0.0
        if ctx.active_event_count >= cfg.cluster_min_count:
            cluster_risk = min(
                (ctx.active_event_count
                 - cfg.cluster_min_count + 1)
                * cfg.cluster_risk_per_event,
                0.25,
            )

        holiday_risk = 0.0
        if ctx.is_holiday:
            holiday_risk = (
                cfg.holiday_risk_scale
                * (1.0 - ctx.liquidity_factor)
            )

        disagreement_risk = 0.0
        if memory.disagreement > cfg.disagreement_threshold:
            disagreement_risk = cfg.disagreement_risk

        # PRE_EVENT追加リスク
        pre_event_risk = 0.0
        if ctx.has_high_impact_within_30min:
            pre_event_risk = 0.15

        total = (
            event_risk
            + cluster_risk
            + holiday_risk
            + disagreement_risk
            + pre_event_risk
        )
        return min(total, 1.0)

    def _categorize_risk(self, risk: float) -> RiskCategory:
        """リスクカテゴリ判定"""
        cfg = self.config
        if risk >= cfg.block_threshold:
            return RiskCategory.BLOCK
        if risk >= cfg.high_threshold:
            return RiskCategory.HIGH
        if risk >= cfg.caution_threshold:
            return RiskCategory.CAUTION
        return RiskCategory.NORMAL

    def _calculate_event_dominance(
        self, ctx: FundamentalContext,
    ) -> float:
        """イベント支配度

        テクニカル指標がどの程度信頼できないかの指標。
        高いほどファンダメンタルが支配的。
        """
        if ctx.event_caution_level == 0:
            return 0.0

        surprise = abs(ctx.surprise_score)
        convergence_inv = 1.0 - ctx.convergence_progress
        # ボラティリティも考慮
        vol_factor = max(
            ctx.volatility_multiplier - 1.0, 0.0,
        )

        dominance = convergence_inv * max(
            surprise, min(vol_factor, 1.0),
        )
        return min(dominance, 1.0)

    def _calculate_effective_bias(
        self,
        ctx: FundamentalContext,
        memory: FundamentalMemorySnapshot,
        dominance: float,
    ) -> tuple[float, float]:
        """統合バイアスと確信度を計算

        短期（イベント直後のdirection_bias）と
        中長期（メモリの蓄積バイアス）を
        dominance で重み付けする。

        Returns:
            tuple[float, float]: (effective_bias, confidence)
        """
        # 短期バイアス（イベントContextから）
        short_bias = ctx.direction_bias
        short_weight = dominance

        # 中長期バイアス（メモリから）
        long_bias = memory.composite_bias
        long_weight = memory.composite_confidence * (
            1.0 - dominance * 0.5
        )

        total_weight = short_weight + long_weight
        if total_weight < 0.05:
            return 0.0, 0.0

        eff_bias = (
            short_bias * short_weight
            + long_bias * long_weight
        ) / total_weight

        # 確信度: 矛盾があれば低下
        raw_conf = min(total_weight, 1.0)
        if memory.disagreement > self.config.disagreement_threshold:
            raw_conf *= self.config.disagreement_confidence_scale

        return eff_bias, raw_conf

    def _calculate_lot_multiplier(
        self,
        risk: float,
        ctx: FundamentalContext,
    ) -> float:
        """リスクレベルに基づくロット倍率"""
        cfg = self.config

        if risk >= cfg.block_threshold:
            return 0.0  # エントリーなし

        if risk >= cfg.high_threshold:
            # 0.7-0.9 → 0.3-0.6
            t = (risk - cfg.high_threshold) / (
                cfg.block_threshold - cfg.high_threshold
            )
            return 0.6 - t * 0.3

        if risk >= cfg.caution_threshold:
            # 0.4-0.7 → 0.7-0.9（控えめな削減）
            t = (risk - cfg.caution_threshold) / (
                cfg.high_threshold - cfg.caution_threshold
            )
            return 0.9 - t * 0.2

        return 1.0

    def _calculate_trailing_sl_mult(
        self,
        ctx: FundamentalContext,
    ) -> float:
        """収束進捗に基づくトレーリングSL倍率

        低収束時はSLを引き締める。
        """
        if ctx.convergence_progress < 0.3:
            return 0.7
        if ctx.convergence_progress < 0.5:
            return 0.85
        return 1.0
