"""フィルター結果データクラス

全フィルターモジュール共通の結果データ構造。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FilterResult:
    """フィルター結果

    Attributes:
        skip: スキップするかどうか
        reason: スキップ理由（スキップしない場合は空文字）
    """

    skip: bool
    reason: str = ""


@dataclass
class ManagerFilterResult:
    """統合フィルターマネージャー結果

    Attributes:
        skip: スキップするかどうか
        reason: スキップ理由
        filter_name: 発動したフィルター名
        confidence_adjustment: 確度調整値（-1.0 ~ 0.0）
    """

    skip: bool
    reason: str = ""
    filter_name: str = ""
    confidence_adjustment: float = 0.0
