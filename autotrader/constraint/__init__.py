"""制約機（Constraint）モジュール

取引可否・リスク統制を担当するモジュール。

Modules:
    hard_guard: ハードガード（絶対禁止条件）
    soft_guard: ソフトガード（ペナルティ適用条件）
    filters: 各種フィルター（トレンド、ADX等）
"""

from __future__ import annotations

from autotrader.constraint.filters import ADXFilter, TrendFilter
from autotrader.constraint.hard_guard import HardGuard, HardGuardResult
from autotrader.constraint.result import ConstraintChecker, ConstraintResult
from autotrader.constraint.soft_guard import SoftGuard, SoftGuardResult

__all__ = [
    "HardGuard",
    "HardGuardResult",
    "SoftGuard",
    "SoftGuardResult",
    "ConstraintResult",
    "ConstraintChecker",
    "TrendFilter",
    "ADXFilter",
]
