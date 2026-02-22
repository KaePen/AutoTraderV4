"""制約チェック結果

ハードガードとソフトガードの統合結果を管理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from autotrader.constraint.hard_guard import HardGuard, HardGuardResult
from autotrader.constraint.soft_guard import SoftGuard, SoftGuardResult


class ConstraintAction(Enum):
    """制約アクション"""

    ALLOW = "allow"
    DENY = "deny"
    PENALIZE = "penalize"


@dataclass(frozen=True)
class ConstraintResult:
    """制約チェック総合結果

    Attributes:
        action: アクション（許可/禁止/ペナルティ）
        hard_guard: ハードガード結果
        soft_guard: ソフトガード結果
        total_penalty: 総合ペナルティ（0-1）
        reasons: 理由リスト
        checked_at: チェック日時
    """

    action: ConstraintAction
    hard_guard: HardGuardResult
    soft_guard: SoftGuardResult
    total_penalty: float
    reasons: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=datetime.now)

    def is_allowed(self) -> bool:
        """取引が許可されているか

        Returns:
            bool: 許可されていればTrue
        """
        return self.action != ConstraintAction.DENY

    def get_adjusted_confidence(self, original: float) -> float:
        """ペナルティ適用後の確度を取得

        Args:
            original: 元の確度

        Returns:
            float: 調整後の確度
        """
        return original * (1 - self.total_penalty)


class ConstraintChecker:
    """制約チェッカー

    ハードガードとソフトガードを統合して制約チェックを実行。

    Args:
        hard_guard: ハードガードインスタンス
        soft_guard: ソフトガードインスタンス
    """

    def __init__(
        self,
        hard_guard: HardGuard | None = None,
        soft_guard: SoftGuard | None = None,
    ) -> None:
        self.hard_guard = hard_guard or HardGuard()
        self.soft_guard = soft_guard or SoftGuard()

    def check(
        self,
        context: dict,
        is_entry: bool = True,
    ) -> ConstraintResult:
        """制約チェックを実行

        Args:
            context: コンテキスト情報
            is_entry: エントリー時のチェックか

        Returns:
            ConstraintResult: チェック結果
        """
        hard_result = self.hard_guard.check(context, is_entry=is_entry)

        if not hard_result.is_allowed:
            return ConstraintResult(
                action=ConstraintAction.DENY,
                hard_guard=hard_result,
                soft_guard=self.soft_guard.create_empty_result(),
                total_penalty=1.0,
                reasons=hard_result.reasons,
            )

        soft_result = self.soft_guard.check(context, is_entry=is_entry)

        if soft_result.total_penalty > 0:
            action = ConstraintAction.PENALIZE
        else:
            action = ConstraintAction.ALLOW

        reasons = hard_result.reasons + soft_result.reasons

        return ConstraintResult(
            action=action,
            hard_guard=hard_result,
            soft_guard=soft_result,
            total_penalty=soft_result.total_penalty,
            reasons=reasons,
        )

    def check_entry(self, context: dict) -> ConstraintResult:
        """エントリー時の制約チェック

        Args:
            context: コンテキスト情報

        Returns:
            ConstraintResult: チェック結果
        """
        return self.check(context, is_entry=True)

    def check_holding(self, context: dict) -> ConstraintResult:
        """保有中の制約チェック

        Args:
            context: コンテキスト情報

        Returns:
            ConstraintResult: チェック結果
        """
        return self.check(context, is_entry=False)
