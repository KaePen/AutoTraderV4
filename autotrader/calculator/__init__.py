"""計算機（Calculator）モジュール

テクニカル指標・特徴量の算出を担当するモジュール。

Modules:
    technical: テクニカル指標計算
    features: 特徴量計算
    precompute: バッチ事前計算エンジン
"""

from __future__ import annotations

from autotrader.calculator.precompute import PrecomputeEngine
from autotrader.calculator.scoring import (
    normalize_atr_by_price,
    score_rsi_continuous,
    score_rsi_discrete,
)

__all__ = [
    "PrecomputeEngine",
    "normalize_atr_by_price",
    "score_rsi_continuous",
    "score_rsi_discrete",
]
