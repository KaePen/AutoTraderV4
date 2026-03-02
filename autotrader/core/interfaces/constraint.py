"""制約機インターフェース

取引可否・リスク統制のデータ型。
未使用の Guard / ConstraintCheckerInterface は削除済み。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConstraintAction(Enum):
    """制約アクション"""

    ALLOW = "allow"
    DENY = "deny"
    PENALIZE = "penalize"


@dataclass(frozen=True)
class ConstraintCheckResult:
    """制約チェック結果

    Attributes:
        action: アクション
        is_allowed: 許可されているか
        penalty: ペナルティ（0-1）
        reasons: 理由リスト
    """

    action: ConstraintAction
    is_allowed: bool
    penalty: float
    reasons: list[str]

    def get_adjusted_confidence(self, original: float) -> float:
        """ペナルティ適用後の確度を取得

        Args:
            original: 元の確度

        Returns:
            float: 調整後の確度
        """
        return original * (1 - self.penalty)
