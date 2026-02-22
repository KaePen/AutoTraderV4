"""制約機（Constraint）モジュール

取引可否・リスク統制を担当するモジュール。

Modules:
    hard_guard: ハードガード（絶対禁止条件）
    soft_guard: ソフトガード（ペナルティ適用条件）
    filters: 各種フィルター（トレンド、ADX等）
"""

from __future__ import annotations

from autotrader.constraint.hard_guard import HardGuard, HardGuardResult
from autotrader.constraint.soft_guard import SoftGuard, SoftGuardResult
from autotrader.constraint.result import ConstraintResult, ConstraintChecker
from autotrader.constraint.filters import TrendFilter, ADXFilter

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
