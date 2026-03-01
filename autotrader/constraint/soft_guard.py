"""ソフトガード（ペナルティ適用条件）

取引は許可するが、確度にペナルティを適用する条件をチェック。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autotrader.decision.unified.fundamental_assessor import (
        FundamentalAssessment,
    )


class SoftGuardReason(Enum):
    """ソフトガード理由"""

    HIGH_SPREAD = "high_spread"
    OFF_HOURS = "off_hours"
    LOW_VOLATILITY = "low_volatility"
    HIGH_VOLATILITY = "high_volatility"
    RECENT_LOSS = "recent_loss"
    MTF_CONFLICT = "mtf_conflict"
    WEAK_TREND = "weak_trend"
    # Phase 2b: ファンダメンタル
    FUNDAMENTAL_RISK = "fundamental_risk"


@dataclass(frozen=True)
class SoftGuardConfig:
    """ソフトガード設定

    Attributes:
        spread_threshold_pips: スプレッド警告閾値（pips）
        spread_penalty_rate: スプレッドペナルティ率
        optimal_hours: 最適取引時間帯（UTC）
        off_hours_penalty: オフタイムペナルティ
        reject_hours: 取引拒否時間帯（UTC）
        min_volatility_atr_ratio: 最低ボラティリティ
        max_volatility_atr_ratio: 最大ボラティリティ
        volatility_penalty: ボラティリティペナルティ
        recent_loss_threshold: 連敗閾値
        recent_loss_penalty: 連敗ペナルティ
        dynamic_spread_enabled: 動的スプレッド調整を有効化
    """

    spread_threshold_pips: float = 2.0
    spread_penalty_rate: float = 0.1
    optimal_hours: tuple[int, ...] = tuple(range(8, 18))
    off_hours_penalty: float = 0.15
    reject_hours: tuple[int, ...] = (22, 23, 0, 1, 2, 3)
    min_volatility_atr_ratio: float = 0.5
    max_volatility_atr_ratio: float = 2.0
    volatility_penalty: float = 0.1
    recent_loss_threshold: int = 3
    recent_loss_penalty: float = 0.2
    dynamic_spread_enabled: bool = False


@dataclass(frozen=True)
class SoftGuardResult:
    """ソフトガードチェック結果

    Attributes:
        total_penalty: 総合ペナルティ（0-1）
        penalties: 個別ペナルティ辞書
        reasons: 理由リスト
        reason_codes: 理由コードリスト
        checked_at: チェック日時
    """

    total_penalty: float
    penalties: dict[SoftGuardReason, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    reason_codes: list[SoftGuardReason] = field(default_factory=list)
    checked_at: datetime = field(default_factory=datetime.now)


class DynamicSpreadEstimator:
    """動的スプレッド見積もりクラス

    セッション・時間帯・ボラティリティ・イベント前フラグに応じて
    スプレッドを動的に調整する。
    """

    # 低流動性時間帯（TOKYO早朝、UTC17-21時）
    LOW_LIQUIDITY_HOURS: tuple[int, ...] = (17, 18, 19, 20, 21)

    def estimate_spread(
        self,
        base_spread: float,
        session: str,
        hour: int,
        atr_ratio: float,
        is_pre_event: bool = False,
    ) -> float:
        """動的スプレッド見積もり

        Args:
            base_spread: 基本スプレッド（pips）
            session: セッション名（TOKYO/LONDON/NEWYORK）
            hour: UTC時刻
            atr_ratio: ATR / ATR_MA（ボラティリティ比）
            is_pre_event: イベント前フラグ

        Returns:
            float: 調整後スプレッド（pips）
        """
        adjusted = base_spread

        # 低流動性時間帯（TOKYO早朝）
        if session == "TOKYO" and hour in self.LOW_LIQUIDITY_HOURS:
            adjusted *= 1.5

        # 高ボラティリティ時
        if atr_ratio > 1.5:
            adjusted *= min(atr_ratio, 2.0)

        # イベント前
        if is_pre_event:
            adjusted *= 2.0

        return adjusted


class SoftGuard:
    """ソフトガードクラス

    取引は許可するが、条件に応じてペナルティを適用する。

    Args:
        config: ソフトガード設定
    """

    def __init__(self, config: SoftGuardConfig | None = None) -> None:
        self.config = config or SoftGuardConfig()
        self.spread_estimator = DynamicSpreadEstimator()

    def check_spread(self, context: dict) -> tuple[float, str | None]:
        """スプレッドチェック

        Args:
            context: コンテキスト

        Returns:
            tuple[float, str | None]: (ペナルティ, 理由)
        """
        base_spread = context.get("spread_pips", 0.0)

        # 動的スプレッド調整
        if self.config.dynamic_spread_enabled:
            current_time: datetime | None = context.get("current_time")
            session = context.get("session", "UNKNOWN")
            hour = current_time.hour if current_time else 0
            atr_ratio = context.get("atr_ratio", 1.0)
            is_pre_event = context.get("is_pre_event", False)

            spread_pips = self.spread_estimator.estimate_spread(
                base_spread=base_spread,
                session=session,
                hour=hour,
                atr_ratio=atr_ratio,
                is_pre_event=is_pre_event,
            )
        else:
            spread_pips = base_spread

        if spread_pips > self.config.spread_threshold_pips:
            excess = spread_pips - self.config.spread_threshold_pips
            penalty = min(
                self.config.spread_penalty_rate * (1 + excess / 2), 0.5
            )
            return penalty, f"高スプレッド: {spread_pips:.1f}pips"
        return 0.0, None

    def check_session_hours(self, context: dict) -> tuple[float, str | None]:
        """セッション時間チェック

        Args:
            context: コンテキスト

        Returns:
            tuple[float, str | None]: (ペナルティ, 理由)
        """
        current_time: datetime | None = context.get("current_time")
        if current_time is None:
            return 0.0, None

        hour = current_time.hour

        # 低流動性時間帯は拒否（ペナルティ1.0で事実上ブロック）
        if hour in self.config.reject_hours:
            return 1.0, f"低流動性時間帯拒否: {hour}時"

        if hour not in self.config.optimal_hours:
            return self.config.off_hours_penalty, f"オフタイム取引: {hour}時"
        return 0.0, None

    def check_volatility(self, context: dict) -> tuple[float, str | None]:
        """ボラティリティチェック

        Args:
            context: コンテキスト

        Returns:
            tuple[float, str | None]: (ペナルティ, 理由)
        """
        atr_ratio = context.get("atr_ratio", 1.0)

        if atr_ratio < self.config.min_volatility_atr_ratio:
            return (
                self.config.volatility_penalty,
                f"低ボラティリティ: ATR比{atr_ratio:.2f}",
            )
        if atr_ratio > self.config.max_volatility_atr_ratio:
            return (
                self.config.volatility_penalty,
                f"高ボラティリティ: ATR比{atr_ratio:.2f}",
            )
        return 0.0, None

    def check_recent_performance(
        self, context: dict
    ) -> tuple[float, str | None]:
        """直近パフォーマンスチェック

        Args:
            context: コンテキスト

        Returns:
            tuple[float, str | None]: (ペナルティ, 理由)
        """
        recent_losses = context.get("recent_losses", 0)

        if recent_losses >= self.config.recent_loss_threshold:
            return self.config.recent_loss_penalty, f"連敗中: {recent_losses}連敗"
        return 0.0, None

    def check_mtf_conflict(self, context: dict) -> tuple[float, str | None]:
        """MTFコンフリクトチェック

        Args:
            context: コンテキスト

        Returns:
            tuple[float, str | None]: (ペナルティ, 理由)
        """
        mtf_alignment = context.get("mtf_alignment", "aligned")

        if mtf_alignment in ("conflicting", "mixed"):
            return 0.15, f"MTF不整合: {mtf_alignment}"
        return 0.0, None

    def check_trend_strength(self, context: dict) -> tuple[float, str | None]:
        """トレンド強度チェック

        Args:
            context: コンテキスト

        Returns:
            tuple[float, str | None]: (ペナルティ, 理由)
        """
        trend_strength = context.get("trend_strength", 0.5)

        if trend_strength < 0.3:
            return 0.1, f"弱トレンド: 強度{trend_strength:.2f}"
        return 0.0, None

    def check(
        self,
        context: dict,
        is_entry: bool = True,
        fundamental_assessment: FundamentalAssessment | None = None,
    ) -> SoftGuardResult:
        """全ソフトガードチェックを実行

        Args:
            context: コンテキスト情報
            is_entry: エントリー時のチェックか
            fundamental_assessment: ファンダメンタル評価結果

        Returns:
            SoftGuardResult: チェック結果
        """
        penalties: dict[SoftGuardReason, float] = {}
        reasons: list[str] = []
        reason_codes: list[SoftGuardReason] = []

        checks = [
            (self.check_spread, SoftGuardReason.HIGH_SPREAD),
            (self.check_session_hours, SoftGuardReason.OFF_HOURS),
            (self.check_volatility, SoftGuardReason.HIGH_VOLATILITY),
        ]

        if is_entry:
            checks.extend([
                (self.check_recent_performance, SoftGuardReason.RECENT_LOSS),
                (self.check_mtf_conflict, SoftGuardReason.MTF_CONFLICT),
                (self.check_trend_strength, SoftGuardReason.WEAK_TREND),
            ])

        for check_func, reason_code in checks:
            penalty, reason = check_func(context)
            if penalty > 0 and reason:
                penalties[reason_code] = penalty
                reasons.append(reason)
                reason_codes.append(reason_code)

        # Phase 2b: ファンダメンタルリスクペナルティ
        if fundamental_assessment is not None:
            fund_pen, fund_reason = (
                self._check_fundamental(fundamental_assessment)
            )
            if fund_pen > 0 and fund_reason:
                penalties[SoftGuardReason.FUNDAMENTAL_RISK] = (
                    fund_pen
                )
                reasons.append(fund_reason)
                reason_codes.append(
                    SoftGuardReason.FUNDAMENTAL_RISK,
                )

        total_penalty = min(sum(penalties.values()), 0.8)

        return SoftGuardResult(
            total_penalty=total_penalty,
            penalties=penalties,
            reasons=reasons,
            reason_codes=reason_codes,
        )

    @staticmethod
    def _check_fundamental(
        assessment: FundamentalAssessment,
    ) -> tuple[float, str | None]:
        """ファンダメンタルリスクチェック

        FundamentalAssessmentのリスクレベルを
        SoftGuardペナルティに変換する。

        Args:
            assessment: ファンダメンタル評価結果

        Returns:
            tuple[float, str | None]: (ペナルティ, 理由)
        """
        from autotrader.decision.unified.fundamental_assessor import (
            RiskCategory,
        )

        if assessment.risk_category == RiskCategory.NORMAL:
            return 0.0, None

        if assessment.risk_category == RiskCategory.BLOCK:
            return 0.5, (
                f"ファンダBLOCK: risk={assessment.risk_level:.2f}"
            )

        if assessment.risk_category == RiskCategory.HIGH:
            return 0.3, (
                f"ファンダHIGH: risk={assessment.risk_level:.2f}"
            )

        # CAUTION
        return 0.1, (
            f"ファンダCAUTION: risk={assessment.risk_level:.2f}"
        )

    def create_empty_result(self) -> SoftGuardResult:
        """空の結果を作成

        Returns:
            SoftGuardResult: ペナルティなしの結果
        """
        return SoftGuardResult(total_penalty=0.0)
