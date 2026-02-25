"""ハードガード（絶対禁止条件）

取引を絶対に禁止する条件をチェック。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from autotrader.adapters.fundamental.schemas import (
    FundamentalContext,
)


class HardGuardReason(Enum):
    """ハードガード理由"""

    INSUFFICIENT_MARGIN = "insufficient_margin"
    MAX_DAILY_LOSS = "max_daily_loss"
    MAX_POSITION_LIMIT = "max_position_limit"
    DATA_QUALITY_ERROR = "data_quality_error"
    TRADING_HOURS = "trading_hours"
    HIGH_IMPACT_NEWS = "high_impact_news"
    FUNDAMENTAL_CAUTION = "fundamental_caution"
    LOW_LIQUIDITY_HOLIDAY = "low_liquidity_holiday"


@dataclass(frozen=True)
class HardGuardConfig:
    """ハードガード設定

    Attributes:
        max_daily_loss_pct: 日次最大損失率（%）
        max_position_count: 最大ポジション数
        min_margin_ratio: 最低証拠金維持率（%）
        blocked_hours: 取引禁止時間帯
        fundamental_caution_block_level: ブロック注意度閾値
        fundamental_holiday_liquidity_block: 休日流動性ブロック閾値
    """

    max_daily_loss_pct: float = 5.0
    max_position_count: int = 3
    min_margin_ratio: float = 150.0
    blocked_hours: tuple[int, ...] = (0, 23)
    fundamental_caution_block_level: int = 2
    fundamental_holiday_liquidity_block: float = 0.3


@dataclass(frozen=True)
class HardGuardResult:
    """ハードガードチェック結果

    Attributes:
        is_allowed: 取引許可
        reasons: 禁止理由リスト
        reason_codes: 禁止理由コードリスト
        checked_at: チェック日時
    """

    is_allowed: bool
    reasons: list[str] = field(default_factory=list)
    reason_codes: list[HardGuardReason] = field(
        default_factory=list
    )
    checked_at: datetime = field(default_factory=datetime.now)


class HardGuard:
    """ハードガードクラス

    取引を絶対に禁止する条件をチェックする。

    Args:
        config: ハードガード設定
    """

    def __init__(
        self, config: HardGuardConfig | None = None
    ) -> None:
        self.config = config or HardGuardConfig()

    def check_margin(
        self, context: dict
    ) -> tuple[bool, str | None]:
        """証拠金チェック

        Args:
            context: コンテキスト

        Returns:
            tuple[bool, str | None]: (OK, 理由)
        """
        margin_ratio = context.get("margin_ratio", 100.0)

        if margin_ratio < self.config.min_margin_ratio:
            return (
                False,
                f"証拠金維持率不足: {margin_ratio:.1f}% < "
                f"{self.config.min_margin_ratio}%",
            )
        return True, None

    def check_daily_loss(
        self, context: dict
    ) -> tuple[bool, str | None]:
        """日次損失チェック

        Args:
            context: コンテキスト

        Returns:
            tuple[bool, str | None]: (OK, 理由)
        """
        daily_pnl_pct = context.get("daily_pnl_pct", 0.0)

        if daily_pnl_pct < -self.config.max_daily_loss_pct:
            return (
                False,
                f"日次損失上限超過: {daily_pnl_pct:.2f}% < "
                f"-{self.config.max_daily_loss_pct}%",
            )
        return True, None

    def check_position_limit(
        self, context: dict
    ) -> tuple[bool, str | None]:
        """ポジション数チェック

        Args:
            context: コンテキスト

        Returns:
            tuple[bool, str | None]: (OK, 理由)
        """
        position_count = context.get("position_count", 0)

        if position_count >= self.config.max_position_count:
            return (
                False,
                f"ポジション上限: {position_count} >= "
                f"{self.config.max_position_count}",
            )
        return True, None

    def check_trading_hours(
        self, context: dict
    ) -> tuple[bool, str | None]:
        """取引時間チェック

        Args:
            context: コンテキスト

        Returns:
            tuple[bool, str | None]: (OK, 理由)
        """
        current_time: datetime | None = context.get(
            "current_time"
        )
        if current_time is None:
            return True, None

        hour = current_time.hour
        weekday = current_time.weekday()

        if weekday in (5, 6):
            return False, f"週末は取引禁止: {current_time}"

        if hour in self.config.blocked_hours:
            return False, f"取引禁止時間帯: {hour}時"

        return True, None

    def check_data_quality(
        self, context: dict
    ) -> tuple[bool, str | None]:
        """データ品質チェック

        Args:
            context: コンテキスト

        Returns:
            tuple[bool, str | None]: (OK, 理由)
        """
        data_quality = context.get("data_quality", "good")

        if data_quality == "error":
            return False, "データ品質エラー"
        return True, None

    def check_high_impact_news(
        self, context: dict
    ) -> tuple[bool, str | None]:
        """高インパクトニュースチェック

        Args:
            context: コンテキスト

        Returns:
            tuple[bool, str | None]: (OK, 理由)
        """
        has_news = context.get("high_impact_news", False)
        news_minutes = context.get("news_minutes_away", 60)

        if has_news and news_minutes < 15:
            return (
                False,
                f"高インパクトニュース{news_minutes}分前",
            )
        return True, None

    def check_fundamental(
        self, fundamental_ctx: FundamentalContext,
    ) -> tuple[bool, str | None, HardGuardReason | None]:
        """ファンダメンタルコンテキストチェック

        超重要指標日（caution_level >= 2）や
        休日の極度低流動性でブロックする。

        Args:
            fundamental_ctx: ファンダメンタルコンテキスト

        Returns:
            tuple[bool, str | None, HardGuardReason | None]:
                (OK, 理由, 理由コード)
        """
        cfg = self.config
        ctx = fundamental_ctx

        # 超重要指標日（NFP等）
        if ctx.event_caution_level >= (
            cfg.fundamental_caution_block_level
        ):
            return (
                False,
                f"超重要指標日: 注意度"
                f"{ctx.event_caution_level}",
                HardGuardReason.FUNDAMENTAL_CAUTION,
            )

        # 休日の極度低流動性
        if (
            ctx.is_holiday
            and ctx.liquidity_factor
            < cfg.fundamental_holiday_liquidity_block
        ):
            return (
                False,
                f"休日低流動性: {ctx.liquidity_factor:.2f}"
                f" < {cfg.fundamental_holiday_liquidity_block}",
                HardGuardReason.LOW_LIQUIDITY_HOLIDAY,
            )

        return True, None, None

    def check(
        self,
        context: dict,
        is_entry: bool = True,
        fundamental_ctx: FundamentalContext | None = None,
    ) -> HardGuardResult:
        """全ハードガードチェックを実行

        Args:
            context: コンテキスト情報
            is_entry: エントリー時のチェックか
            fundamental_ctx: ファンダメンタルコンテキスト

        Returns:
            HardGuardResult: チェック結果
        """
        reasons: list[str] = []
        reason_codes: list[HardGuardReason] = []

        checks = [
            (
                self.check_margin,
                HardGuardReason.INSUFFICIENT_MARGIN,
            ),
            (
                self.check_daily_loss,
                HardGuardReason.MAX_DAILY_LOSS,
            ),
            (
                self.check_trading_hours,
                HardGuardReason.TRADING_HOURS,
            ),
            (
                self.check_data_quality,
                HardGuardReason.DATA_QUALITY_ERROR,
            ),
        ]

        if is_entry:
            checks.extend([
                (
                    self.check_position_limit,
                    HardGuardReason.MAX_POSITION_LIMIT,
                ),
                (
                    self.check_high_impact_news,
                    HardGuardReason.HIGH_IMPACT_NEWS,
                ),
            ])

        for check_func, reason_code in checks:
            ok, reason = check_func(context)
            if not ok and reason:
                reasons.append(reason)
                reason_codes.append(reason_code)

        # ファンダメンタルチェック（エントリー時のみ）
        if is_entry and fundamental_ctx is not None:
            ok, reason, code = self.check_fundamental(
                fundamental_ctx
            )
            if not ok and reason and code:
                reasons.append(reason)
                reason_codes.append(code)

        is_allowed = len(reasons) == 0

        return HardGuardResult(
            is_allowed=is_allowed,
            reasons=reasons,
            reason_codes=reason_codes,
        )
