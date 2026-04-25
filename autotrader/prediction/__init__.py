"""予測モジュール（実験コード）

ML予測実験の結果:
- テクニカル指標によるFX方向予測はOOSで機能しない（全8実験FAIL）
- ボラティリティ予測のみ統計的に有意だがトレード改善には不十分
- 詳細: scripts/train_prediction_model.py

本パッケージは実験記録として保持。
本番で使用するのはHYBRIDモード（UNIVERSAL + REACTIVE H1ブレイクアウト）。
"""

from autotrader.prediction.config import PredictionConfig
from autotrader.prediction.direction_predictor import (
    DirectionPredictor,
    PredictionResult,
)
from autotrader.prediction.feature_builder import FeatureBuilder

__all__ = [
    "DirectionPredictor",
    "FeatureBuilder",
    "PredictionConfig",
    "PredictionResult",
]
