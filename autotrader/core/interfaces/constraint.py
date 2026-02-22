"""制約機インターフェース

取引可否・リスク統制の抽象インターフェース。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
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


class Guard(ABC):
    """ガードインターフェース（ハード/ソフト共通）"""

    @abstractmethod
    def check(self, context: dict, is_entry: bool = True) -> dict:
        """チェックを実行

        Args:
            context: コンテキスト情報
            is_entry: エントリー時のチェックか

        Returns:
            dict: チェック結果
        """
        ...


class ConstraintCheckerInterface(ABC):
    """制約チェッカーインターフェース

    ハードガードとソフトガードを統合してチェック。
    """

    @abstractmethod
    def check_entry(self, context: dict) -> ConstraintCheckResult:
        """エントリー時の制約チェック

        Args:
            context: コンテキスト情報

        Returns:
            ConstraintCheckResult: チェック結果
        """
        ...

    @abstractmethod
    def check_holding(self, context: dict) -> ConstraintCheckResult:
        """保有中の制約チェック

        Args:
            context: コンテキスト情報

        Returns:
            ConstraintCheckResult: チェック結果
        """
        ...
