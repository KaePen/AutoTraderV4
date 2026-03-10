"""特徴量計算モジュール

トレード判断に使用する特徴量の計算を提供。
"""

from __future__ import annotations

from autotrader.calculator.features.mtf_features import MTFFeatures
from autotrader.calculator.features.regime_detector import (
    MarketRegimeDetector,
    RegimeDetectorConfig,
    RegimeResult,
)
from autotrader.calculator.features.trend_features import TrendFeatures
from autotrader.calculator.features.volatility_features import (
    VolatilityFeatures,
)
from autotrader.calculator.features.volume_analyzer import (
    compute_volume_features,
)

__all__ = [
    "TrendFeatures",
    "VolatilityFeatures",
    "MTFFeatures",
    "MarketRegimeDetector",
    "RegimeDetectorConfig",
    "RegimeResult",
    "compute_volume_features",
]
