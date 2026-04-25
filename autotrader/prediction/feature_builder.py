"""ML特徴量構築モジュール

PrecomputeEngine出力のテクニカル指標・特徴量からML用の特徴量行列を構築する。
全特徴量は過去データのみから構成され、先読みバイアスを含まない。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# PrecomputeEngine出力カラムから使用する特徴量の定義
# カテゴリ別に整理（欠損時はNaN）
_TREND_FEATURES = [
    "trend_strength",
    "ma_alignment",
    "slope_consistency",
    "deviation_score",
]
_MOMENTUM_FEATURES = [
    "rsi_14",
    "macd_histogram",
    "stoch_k",
    "stoch_d",
]
_VOLATILITY_FEATURES = [
    "normalized_atr",
    "bb_squeeze",
    "range_expansion",
    "volatility_trend",
    "bb_width",
    "bb_percent_b",
]
_STRUCTURE_FEATURES = [
    "structure_direction",
    "bos_signal",
    "choch_signal",
]
_PRICE_FEATURES = [
    "adx_14",
    "plus_di_14",
    "minus_di_14",
]


@dataclass(frozen=True)
class FeatureSpec:
    """特徴量仕様

    Attributes:
        name: 特徴量名
        feature_names: 最終的な特徴量カラム名リスト
    """

    name: str
    feature_names: list[str] = field(default_factory=list)


class FeatureBuilder:
    """PrecomputeEngine出力からML特徴量行列を構築

    設計原則:
    - 全特徴量は t 時点で利用可能な情報のみで構成
    - 派生特徴量（変化率、クロスTF比較等）は明示的に shift(1) 以上で構築
    - NaN含有行はフラグで管理（モデル側で処理）
    """

    # 基本特徴量（PrecomputeEngine出力をそのまま使用）
    BASE_COLUMNS: list[str] = (
        _TREND_FEATURES
        + _MOMENTUM_FEATURES
        + _VOLATILITY_FEATURES
        + _STRUCTURE_FEATURES
        + _PRICE_FEATURES
    )

    def __init__(
        self,
        lookback_bars: int = 5,
        include_derived: bool = True,
    ) -> None:
        """初期化

        Args:
            lookback_bars: 派生特徴量のルックバック期間
            include_derived: 派生特徴量を含めるか
        """
        self._lookback = lookback_bars
        self._include_derived = include_derived
        self._feature_names: list[str] | None = None

    @property
    def feature_names(self) -> list[str]:
        """構築される特徴量名のリスト"""
        if self._feature_names is None:
            raise RuntimeError(
                "build() を実行してから feature_names を参照してください"
            )
        return self._feature_names

    @property
    def n_features(self) -> int:
        """特徴量数"""
        return len(self.feature_names)

    def build(self, df: pd.DataFrame) -> pd.DataFrame:
        """PrecomputeEngine出力から特徴量DataFrameを構築

        Args:
            df: PrecomputeEngine出力（テクニカル指標+特徴量付き）

        Returns:
            pd.DataFrame: ML用特徴量（indexはdfと同一）
        """
        features = pd.DataFrame(index=df.index)

        # 1. 基本特徴量（そのまま）
        for col in self.BASE_COLUMNS:
            if col in df.columns:
                features[col] = df[col].astype(float, errors="ignore")
            else:
                features[col] = np.nan

        # 2. カテゴリカル特徴量のエンコーディング
        features = self._encode_categoricals(features, df)

        # 3. 派生特徴量
        if self._include_derived:
            features = self._add_derived_features(features, df)

        # 4. クロスTF特徴量（同一TF内での時間的特徴）
        features = self._add_temporal_features(features, df)

        self._feature_names = list(features.columns)
        logger.info(
            f"特徴量構築完了: {len(self._feature_names)}個, "
            f"行数={len(features)}"
        )
        return features

    def build_single(self, row: pd.Series) -> np.ndarray:
        """単一行から特徴量ベクトルを構築（ライブ推論用）

        Args:
            row: PrecomputeEngine出力の1行

        Returns:
            np.ndarray: 特徴量ベクトル（1D）
        """
        if self._feature_names is None:
            raise RuntimeError(
                "build() でバッチ処理を先に実行し、"
                "feature_namesを確定してください"
            )
        values = []
        for col in self._feature_names:
            if col in row.index:
                val = row[col]
                values.append(float(val) if pd.notna(val) else np.nan)
            else:
                values.append(np.nan)
        return np.array(values, dtype=np.float64)

    def _encode_categoricals(
        self,
        features: pd.DataFrame,
        df: pd.DataFrame,
    ) -> pd.DataFrame:
        """カテゴリカル特徴量を数値に変換

        trend_direction: STRONG_UP=2, UP=1, NEUTRAL=0, DOWN=-1, STRONG_DOWN=-2
        volatility_regime: VERY_HIGH=2, HIGH=1, NORMAL=0, LOW=-1, VERY_LOW=-2
        trend_state_smc: BULLISH=1, BEARISH=-1, others=0
        """
        # trend_direction
        if "trend_direction" in df.columns:
            td_map = {
                "STRONG_UP": 2.0,
                "UP": 1.0,
                "NEUTRAL": 0.0,
                "DOWN": -1.0,
                "STRONG_DOWN": -2.0,
            }
            features["trend_direction_enc"] = (
                df["trend_direction"].map(td_map).fillna(0.0)
            )

        # volatility_regime
        if "volatility_regime" in df.columns:
            vr_map = {
                "VERY_HIGH": 2.0,
                "HIGH": 1.0,
                "NORMAL": 0.0,
                "LOW": -1.0,
                "VERY_LOW": -2.0,
            }
            features["vol_regime_enc"] = (
                df["volatility_regime"].map(vr_map).fillna(0.0)
            )

        # trend_state_smc
        if "trend_state_smc" in df.columns:
            ts_map = {
                "BULLISH": 1.0,
                "BEARISH": -1.0,
                "CONSOLIDATION": 0.0,
                "REVERSAL_BULLISH": 0.5,
                "REVERSAL_BEARISH": -0.5,
            }
            features["smc_trend_enc"] = (
                df["trend_state_smc"].map(ts_map).fillna(0.0)
            )

        return features

    def _add_derived_features(
        self,
        features: pd.DataFrame,
        df: pd.DataFrame,
    ) -> pd.DataFrame:
        """派生特徴量を追加

        全て shift(1) 以上で構築（先読みバイアス回避）。
        """
        close = df["close"] if "close" in df.columns else None
        lb = self._lookback

        # --- MACD slope（ヒストグラムの変化率）---
        if "macd_histogram" in df.columns:
            mh = df["macd_histogram"]
            features["macd_slope"] = mh - mh.shift(1)
            features["macd_slope_3"] = mh - mh.shift(3)

        # --- ADX slope ---
        if "adx_14" in df.columns:
            adx = df["adx_14"]
            features["adx_slope"] = adx - adx.shift(1)
            features["adx_slope_3"] = adx - adx.shift(3)

        # --- RSI 変化率 ---
        if "rsi_14" in df.columns:
            rsi = df["rsi_14"]
            features["rsi_change"] = rsi - rsi.shift(1)
            features["rsi_change_3"] = rsi - rsi.shift(3)

        # --- ATR 変化率（ボラティリティ拡大/縮小）---
        if "atr_14" in df.columns:
            atr = df["atr_14"]
            atr_ma = atr.rolling(lb).mean()
            features["atr_ratio"] = atr / atr_ma.replace(0, np.nan)
            features["atr_change_rate"] = atr.pct_change(lb)

        # --- Price/SMA比率 ---
        if close is not None:
            for period in [20, 50]:
                sma_col = f"sma_{period}"
                if sma_col in df.columns:
                    sma = df[sma_col]
                    features[f"price_sma{period}_ratio"] = (
                        close / sma.replace(0, np.nan)
                    )

        # --- EMA配列スコア ---
        if close is not None:
            ema_cols = ["ema_10", "ema_20", "ema_50"]
            available = [c for c in ema_cols if c in df.columns]
            if len(available) >= 2:
                # EMAの順序一致度（bullish: ema10 > ema20 > ema50）
                align_score = pd.Series(0.0, index=df.index)
                for i in range(len(available) - 1):
                    align_score += (
                        df[available[i]] > df[available[i + 1]]
                    ).astype(float)
                features["ema_alignment_score"] = (
                    align_score / max(len(available) - 1, 1)
                )

        # --- DI差分（方向性強度）---
        if "plus_di_14" in df.columns and "minus_di_14" in df.columns:
            features["di_diff"] = (
                df["plus_di_14"] - df["minus_di_14"]
            )

        # --- BB位置（正規化）---
        if "bb_percent_b" in df.columns:
            bb_b = df["bb_percent_b"]
            features["bb_b_change"] = bb_b - bb_b.shift(1)

        # --- Stochastic %K-%D乖離 ---
        if "stoch_k" in df.columns and "stoch_d" in df.columns:
            features["stoch_kd_diff"] = (
                df["stoch_k"] - df["stoch_d"]
            )

        return features

    def _add_temporal_features(
        self,
        features: pd.DataFrame,
        df: pd.DataFrame,
    ) -> pd.DataFrame:
        """時間的特徴量を追加

        過去N足の統計量（ローリング特徴量）。
        """
        lb = self._lookback

        # --- 過去N足のclose変化率 ---
        if "close" in df.columns:
            close = df["close"]
            features["returns_1"] = close.pct_change(1)
            features["returns_3"] = close.pct_change(3)
            features["returns_vol"] = close.pct_change(1).rolling(lb).std()

        # --- ハイ・ロー比率 ---
        if "high" in df.columns and "low" in df.columns:
            hl_range = df["high"] - df["low"]
            features["hl_range_ma_ratio"] = (
                hl_range / hl_range.rolling(lb).mean().replace(0, np.nan)
            )

        return features
