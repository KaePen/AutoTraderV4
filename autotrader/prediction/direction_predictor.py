"""方向予測モジュール

LightGBM 3クラス分類器（UP/DOWN/FLAT）による方向予測。
ATR相対ラベリングによりレジーム適応的に動作する。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from autotrader.core.enums import SignalType
from autotrader.prediction.config import PredictionConfig
from autotrader.prediction.feature_builder import FeatureBuilder

logger = logging.getLogger(__name__)

# ラベル定義
LABEL_DOWN = 0
LABEL_FLAT = 1
LABEL_UP = 2

LABEL_TO_SIGNAL = {
    LABEL_DOWN: SignalType.SELL,
    LABEL_FLAT: SignalType.HOLD,
    LABEL_UP: SignalType.BUY,
}


@dataclass(frozen=True)
class PredictionResult:
    """予測結果

    Attributes:
        direction: 予測方向（BUY/SELL/HOLD）
        probability: P(predicted_direction) — 0.0-1.0
        probabilities: 全クラスの確率 [P(DOWN), P(FLAT), P(UP)]
        confidence: 確信度（最大確率 - 2番目の確率）
        horizon_bars: 予測ホライズン（足数）
        features_used: 使用した非NaN特徴量数
        model_version: モデルバージョン
    """

    direction: SignalType
    probability: float
    probabilities: tuple[float, float, float]
    confidence: float
    horizon_bars: int
    features_used: int
    model_version: str


@dataclass(frozen=True)
class TrainingMetrics:
    """訓練メトリクス

    Attributes:
        accuracy: 全体精度
        per_class_accuracy: クラス別精度 {DOWN, FLAT, UP}
        confusion_matrix: 混同行列
        feature_importance: 特徴量重要度上位
        n_train: 訓練サンプル数
        n_test: テストサンプル数
        label_distribution: ラベル分布 {DOWN, FLAT, UP}
    """

    accuracy: float
    per_class_accuracy: dict[str, float]
    confusion_matrix: list[list[int]]
    feature_importance: list[tuple[str, float]]
    n_train: int
    n_test: int
    label_distribution: dict[str, int]


class DirectionPredictor:
    """LightGBM方向分類器

    H4/D1時間足データから将来の方向を予測する3クラス分類器。
    ATR相対ラベリングによりレジーム適応的に動作。

    Usage:
        predictor = DirectionPredictor(config)
        predictor.train(X_train, y_train)
        result = predictor.predict(features)
    """

    def __init__(self, config: PredictionConfig | None = None) -> None:
        self._config = config or PredictionConfig()
        self._model = None  # lightgbm.LGBMClassifier (lazy)
        self._feature_builder = FeatureBuilder()
        self._model_version: str = "untrained"
        self._feature_names: list[str] = []

    @property
    def is_trained(self) -> bool:
        """モデルが訓練済みか"""
        return self._model is not None

    @property
    def model_version(self) -> str:
        return self._model_version

    def build_labels(
        self,
        df: pd.DataFrame,
        atr_col: str = "atr_14",
    ) -> pd.Series:
        """方向ラベルを構築

        Args:
            df: OHLCV + テクニカル指標DataFrame
            atr_col: ATRカラム名

        Returns:
            pd.Series: ラベル（0=DOWN, 1=FLAT, 2=UP）、
                先頭/末尾にNaN含む
        """
        cfg = self._config
        close = df["close"]
        atr = df[atr_col]

        # N足先の価格変化
        future_close = close.shift(-cfg.direction_horizon_bars)
        price_change = future_close - close

        # ATR相対で分類
        threshold = atr * cfg.direction_atr_label_mult
        labels = pd.Series(LABEL_FLAT, index=df.index, dtype=np.int64)
        labels[price_change > threshold] = LABEL_UP
        labels[price_change < -threshold] = LABEL_DOWN

        # 先読み不可能な末尾をNaNに
        labels.iloc[-cfg.direction_horizon_bars:] = -1

        return labels

    def train(
        self,
        X: np.ndarray | pd.DataFrame,
        y: np.ndarray | pd.Series,
        feature_names: list[str] | None = None,
        version: str = "v1",
    ) -> TrainingMetrics:
        """モデルを訓練

        Args:
            X: 特徴量行列 (n_samples, n_features)
            y: ラベル (n_samples,)
            feature_names: 特徴量名
            version: モデルバージョン文字列

        Returns:
            TrainingMetrics: 訓練結果メトリクス
        """
        import lightgbm as lgb
        from sklearn.metrics import (
            accuracy_score,
            confusion_matrix,
        )

        cfg = self._config

        if isinstance(X, pd.DataFrame):
            if feature_names is None:
                feature_names = list(X.columns)
            X = X.values
        if isinstance(y, pd.Series):
            y = y.values

        # NaN含有行を除外
        valid_mask = ~np.isnan(X).any(axis=1) & (y >= 0)
        X_clean = X[valid_mask]
        y_clean = y[valid_mask]

        if len(X_clean) < cfg.min_training_samples:
            raise ValueError(
                f"訓練サンプル不足: {len(X_clean)} < "
                f"{cfg.min_training_samples}"
            )

        # ラベル分布
        unique, counts = np.unique(y_clean, return_counts=True)
        label_dist = {
            ["DOWN", "FLAT", "UP"][int(u)]: int(c)
            for u, c in zip(unique, counts)
        }
        logger.info(f"ラベル分布: {label_dist}")

        # LightGBM訓練
        model = lgb.LGBMClassifier(
            n_estimators=cfg.n_estimators,
            learning_rate=cfg.learning_rate,
            max_depth=cfg.max_depth,
            num_leaves=cfg.num_leaves,
            min_child_samples=cfg.min_child_samples,
            feature_fraction=cfg.feature_fraction,
            class_weight=cfg.class_weight,
            objective="multiclass",
            num_class=3,
            random_state=42,
            verbose=-1,
        )

        model.fit(X_clean, y_clean)

        # 訓練データでの評価（OOS評価は呼び出し側で実施）
        y_pred = model.predict(X_clean)
        acc = accuracy_score(y_clean, y_pred)
        cm = confusion_matrix(y_clean, y_pred, labels=[0, 1, 2])

        # クラス別精度
        per_class = {}
        for i, name in enumerate(["DOWN", "FLAT", "UP"]):
            mask = y_clean == i
            if mask.sum() > 0:
                per_class[name] = float(
                    accuracy_score(y_clean[mask], y_pred[mask])
                )
            else:
                per_class[name] = 0.0

        # 特徴量重要度
        importance = model.feature_importances_
        if feature_names and len(feature_names) == len(importance):
            feat_imp = sorted(
                zip(feature_names, importance),
                key=lambda x: x[1],
                reverse=True,
            )[:20]
        else:
            feat_imp = [
                (f"f{i}", float(v)) for i, v in enumerate(importance)
            ][:20]

        self._model = model
        self._model_version = version
        self._feature_names = feature_names or []

        metrics = TrainingMetrics(
            accuracy=float(acc),
            per_class_accuracy=per_class,
            confusion_matrix=cm.tolist(),
            feature_importance=[(n, float(v)) for n, v in feat_imp],
            n_train=len(X_clean),
            n_test=0,
            label_distribution=label_dist,
        )

        logger.info(
            f"訓練完了 v={version}: acc={acc:.3f}, "
            f"samples={len(X_clean)}, features={X_clean.shape[1]}"
        )
        return metrics

    def predict(self, features: np.ndarray) -> PredictionResult:
        """方向を予測

        Args:
            features: 特徴量ベクトル (n_features,) or (1, n_features)

        Returns:
            PredictionResult
        """
        if self._model is None:
            raise RuntimeError("モデル未訓練。train() を先に実行してください")

        if features.ndim == 1:
            features = features.reshape(1, -1)

        n_valid = int(np.sum(~np.isnan(features)))

        # NaNをモデルが処理できるように（LightGBMはNaN対応）
        proba = self._model.predict_proba(features)[0]
        p_down, p_flat, p_up = float(proba[0]), float(proba[1]), float(proba[2])

        # 最大確率クラスを選択
        max_idx = int(np.argmax(proba))
        max_prob = float(proba[max_idx])

        # 確信度（最大 - 2番目）
        sorted_proba = sorted(proba, reverse=True)
        confidence = sorted_proba[0] - sorted_proba[1]

        # 閾値チェック: 確率が低い場合はFLAT
        cfg = self._config
        if max_idx != LABEL_FLAT and max_prob < cfg.direction_threshold:
            direction = SignalType.HOLD
            probability = p_flat
        else:
            direction = LABEL_TO_SIGNAL[max_idx]
            probability = max_prob

        return PredictionResult(
            direction=direction,
            probability=probability,
            probabilities=(p_down, p_flat, p_up),
            confidence=confidence,
            horizon_bars=cfg.direction_horizon_bars,
            features_used=n_valid,
            model_version=self._model_version,
        )

    def predict_batch(
        self, X: np.ndarray | pd.DataFrame
    ) -> list[PredictionResult]:
        """バッチ予測

        Args:
            X: 特徴量行列 (n_samples, n_features)

        Returns:
            list[PredictionResult]
        """
        if self._model is None:
            raise RuntimeError("モデル未訓練")

        if isinstance(X, pd.DataFrame):
            X = X.values

        proba_all = self._model.predict_proba(X)
        results = []
        cfg = self._config

        for i in range(len(X)):
            proba = proba_all[i]
            p_down, p_flat, p_up = (
                float(proba[0]),
                float(proba[1]),
                float(proba[2]),
            )
            max_idx = int(np.argmax(proba))
            max_prob = float(proba[max_idx])
            sorted_proba = sorted(proba, reverse=True)
            confidence = sorted_proba[0] - sorted_proba[1]
            n_valid = int(np.sum(~np.isnan(X[i])))

            if max_idx != LABEL_FLAT and max_prob < cfg.direction_threshold:
                direction = SignalType.HOLD
                probability = p_flat
            else:
                direction = LABEL_TO_SIGNAL[max_idx]
                probability = max_prob

            results.append(
                PredictionResult(
                    direction=direction,
                    probability=probability,
                    probabilities=(p_down, p_flat, p_up),
                    confidence=confidence,
                    horizon_bars=cfg.direction_horizon_bars,
                    features_used=n_valid,
                    model_version=self._model_version,
                )
            )
        return results

    def save(self, path: str | Path) -> None:
        """モデルをディスクに保存

        Args:
            path: 保存先ディレクトリパス
        """
        if self._model is None:
            raise RuntimeError("モデル未訓練")

        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)

        # LightGBMモデル保存
        model_path = path / "model.txt"
        self._model.booster_.save_model(str(model_path))

        # メタデータ保存
        meta = {
            "version": self._model_version,
            "feature_names": self._feature_names,
            "config": {
                "direction_tf": self._config.direction_tf,
                "direction_horizon_bars": self._config.direction_horizon_bars,
                "direction_threshold": self._config.direction_threshold,
                "direction_atr_label_mult": (
                    self._config.direction_atr_label_mult
                ),
                "n_estimators": self._config.n_estimators,
                "learning_rate": self._config.learning_rate,
                "max_depth": self._config.max_depth,
                "num_leaves": self._config.num_leaves,
            },
        }
        meta_path = path / "metadata.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        logger.info(f"モデル保存: {path}")

    def load(self, path: str | Path) -> None:
        """モデルをディスクから読み込み

        Args:
            path: モデルディレクトリパス
        """
        import lightgbm as lgb

        path = Path(path)
        model_path = path / "model.txt"
        meta_path = path / "metadata.json"

        if not model_path.exists():
            raise FileNotFoundError(f"モデルファイル未検出: {model_path}")

        # メタデータ読み込み
        if meta_path.exists():
            with open(meta_path) as f:
                meta = json.load(f)
            self._model_version = meta.get("version", "loaded")
            self._feature_names = meta.get("feature_names", [])
        else:
            self._model_version = "loaded"
            self._feature_names = []

        # LightGBMモデル読み込み
        booster = lgb.Booster(model_file=str(model_path))
        # LGBMClassifierにラップ
        model = lgb.LGBMClassifier()
        model._Booster = booster
        model._n_classes = 3
        model.fitted_ = True
        self._model = model

        logger.info(f"モデル読込: {path} (v={self._model_version})")


def evaluate_oos(
    predictor: DirectionPredictor,
    X_test: np.ndarray | pd.DataFrame,
    y_test: np.ndarray | pd.Series,
) -> dict[str, float]:
    """Out-of-Sample評価

    Args:
        predictor: 訓練済みDirectionPredictor
        X_test: テスト特徴量
        y_test: テストラベル

    Returns:
        dict: {accuracy, per_class_accuracy, random_baseline}
    """
    from sklearn.metrics import accuracy_score

    if isinstance(X_test, pd.DataFrame):
        X_test = X_test.values
    if isinstance(y_test, pd.Series):
        y_test = y_test.values

    # NaN除外
    valid_mask = ~np.isnan(X_test).any(axis=1) & (y_test >= 0)
    X_clean = X_test[valid_mask]
    y_clean = y_test[valid_mask]

    if len(X_clean) == 0:
        return {"accuracy": 0.0, "random_baseline": 0.333}

    # モデル予測
    y_pred = predictor._model.predict(X_clean)
    acc = accuracy_score(y_clean, y_pred)

    # ランダムベースライン（最頻クラスを常に予測）
    unique, counts = np.unique(y_clean, return_counts=True)
    random_baseline = float(counts.max()) / len(y_clean)

    # クラス別精度
    per_class = {}
    for i, name in enumerate(["DOWN", "FLAT", "UP"]):
        mask = y_clean == i
        if mask.sum() > 0:
            per_class[name] = float(
                accuracy_score(y_clean[mask], y_pred[mask])
            )
        else:
            per_class[name] = 0.0

    return {
        "accuracy": float(acc),
        "random_baseline": random_baseline,
        "edge_over_random": float(acc) - random_baseline,
        "n_samples": len(y_clean),
        "per_class_accuracy": per_class,
    }
