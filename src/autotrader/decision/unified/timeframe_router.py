"""タイムフレームルーターモジュール

TradingPlanに基づいて必要なTFセットを構築する。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from autotrader.decision.unified.mode_selector import TradingPlan


class TimeframeRole(str, Enum):
    """時間足の役割"""

    PRIMARY = "primary"      # 主要（シグナル判断基準）
    ENTRY = "entry"          # エントリー（タイミング）
    CONFIRM = "confirm"      # 確認（フィルタリング）
    MANAGE = "manage"        # 管理（ポジション管理）
    OTHER = "other"          # その他


@dataclass(frozen=True)
class TimeframeSet:
    """時間足セット

    Attributes:
        primary_tf: 主要時間足
        entry_tf: エントリー時間足
        confirm_tfs: 確認用時間足リスト
        manage_tf: 管理用時間足
    """

    primary_tf: str
    entry_tf: str
    confirm_tfs: tuple[str, ...]
    manage_tf: str

    @property
    def all_tfs(self) -> list[str]:
        """全時間足のリスト（重複なし）

        Returns:
            list[str]: 時間足リスト
        """
        tfs = {self.primary_tf, self.entry_tf, self.manage_tf}
        tfs.update(self.confirm_tfs)
        return sorted(tfs, key=_tf_to_minutes)

    def get_role(self, tf: str) -> TimeframeRole:
        """時間足の役割を取得

        Args:
            tf: 時間足

        Returns:
            TimeframeRole: 役割
        """
        if tf == self.primary_tf:
            return TimeframeRole.PRIMARY
        elif tf == self.entry_tf:
            return TimeframeRole.ENTRY
        elif tf in self.confirm_tfs:
            return TimeframeRole.CONFIRM
        elif tf == self.manage_tf:
            return TimeframeRole.MANAGE
        else:
            return TimeframeRole.OTHER

    def get_weight(self, tf: str) -> float:
        """時間足の重みを取得

        Args:
            tf: 時間足

        Returns:
            float: 重み
        """
        role = self.get_role(tf)
        weights = {
            TimeframeRole.PRIMARY: 3.0,
            TimeframeRole.ENTRY: 2.0,
            TimeframeRole.CONFIRM: 1.5,
            TimeframeRole.MANAGE: 1.0,
            TimeframeRole.OTHER: 0.5,
        }
        return weights[role]


def _tf_to_minutes(tf: str) -> int:
    """時間足を分単位に変換

    Args:
        tf: 時間足文字列

    Returns:
        int: 分単位
    """
    mapping = {
        "M1": 1, "M5": 5, "M15": 15, "M30": 30,
        "H1": 60, "H4": 240, "H8": 480, "D1": 1440, "W1": 10080,
    }
    return mapping.get(tf, 60)


class TimeframeRouter:
    """タイムフレームルーター

    TradingPlanに基づいて必要なTFセットを構築する。
    """

    # 標準のTF階層
    TF_HIERARCHY = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1", "W1"]

    def __init__(self) -> None:
        """初期化"""
        pass

    def route(self, plan: TradingPlan) -> TimeframeSet:
        """TradingPlanからTimeframeSetを構築

        Args:
            plan: トレーディングプラン

        Returns:
            TimeframeSet: 時間足セット
        """
        return TimeframeSet(
            primary_tf=plan.primary_tf,
            entry_tf=plan.entry_tf,
            confirm_tfs=tuple(plan.confirm_tfs),
            manage_tf=plan.manage_tf,
        )

    def get_required_tfs(self, plan: TradingPlan) -> list[str]:
        """プランに必要な時間足リストを取得

        Args:
            plan: トレーディングプラン

        Returns:
            list[str]: 時間足リスト
        """
        tf_set = self.route(plan)
        return tf_set.all_tfs

    def get_higher_tfs(self, base_tf: str, count: int = 2) -> list[str]:
        """指定TFより上位のTFを取得

        Args:
            base_tf: 基準時間足
            count: 取得数

        Returns:
            list[str]: 上位時間足リスト
        """
        try:
            idx = self.TF_HIERARCHY.index(base_tf)
        except ValueError:
            return []

        higher_tfs = self.TF_HIERARCHY[idx + 1:idx + 1 + count]
        return higher_tfs

    def get_lower_tf(self, base_tf: str) -> str | None:
        """指定TFより下位のTFを取得

        Args:
            base_tf: 基準時間足

        Returns:
            str | None: 下位時間足
        """
        try:
            idx = self.TF_HIERARCHY.index(base_tf)
        except ValueError:
            return None

        if idx > 0:
            return self.TF_HIERARCHY[idx - 1]
        return None

    def validate_tf_hierarchy(self, tf_set: TimeframeSet) -> bool:
        """TFセットの階層整合性を検証

        Args:
            tf_set: 時間足セット

        Returns:
            bool: 整合性があればTrue
        """
        try:
            entry_idx = self.TF_HIERARCHY.index(tf_set.entry_tf)
            primary_idx = self.TF_HIERARCHY.index(tf_set.primary_tf)

            # entry_tf <= primary_tf
            if entry_idx > primary_idx:
                return False

            # confirm_tfs >= primary_tf
            for confirm_tf in tf_set.confirm_tfs:
                confirm_idx = self.TF_HIERARCHY.index(confirm_tf)
                if confirm_idx < primary_idx:
                    return False

            return True
        except ValueError:
            return False
