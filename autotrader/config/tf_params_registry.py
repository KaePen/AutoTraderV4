"""TFパラメータレジストリ

ハードコードされた全TFパラメータ辞書を一元管理し、
未定義TFへの補間付き取得を提供する。
"""

from __future__ import annotations

from autotrader.core.tf_interpolation import (
    interpolate_tf_param,
    interpolate_tf_param_tuple,
)

# ===================================================================
# ATR乗数 (SL, TP) - 元: timeframe_evaluator.py L93
# ===================================================================
_ATR_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "M1": (1.0, 1.5),
    "M5": (1.2, 1.8),
    "M15": (1.5, 2.0),
    "H1": (2.0, 3.0),
    "H4": (2.5, 4.0),
    "D1": (3.0, 5.0),
}

# ===================================================================
# 最小スコア比率 - 元: timeframe_evaluator.py L107
# ===================================================================
_NORMALIZED_MIN_SCORES: dict[str, float] = {
    "M1": 0.10,
    "M5": 0.12,
    "M15": 0.14,
    "H1": 0.16,
    "H4": 0.18,
    "D1": 0.20,
}

# ===================================================================
# TF重み - 元: scoring_config.py L51
# ===================================================================
_TF_WEIGHTS: dict[str, float] = {
    "M1": 0.5,
    "M5": 0.8,
    "M15": 1.0,
    "H1": 1.5,
    "H4": 2.0,
    "D1": 2.5,
}

# ===================================================================
# TFスコア閾値 - 元: scoring_config.py L42
# ===================================================================
_TF_MIN_SCORES: dict[str, float] = {
    "M1": 2.0,
    "M5": 2.25,
    "M15": 2.7,
    "H1": 3.0,
    "H4": 3.3,
    "D1": 3.75,
}

# ===================================================================
# 保持時間（分） - 元: dynamic_tf_selector.py L44
# ===================================================================
_HOLDING_MINUTES: dict[str, int] = {
    "M1": 90,
    "M5": 480,
    "M15": 480,
    "H1": 2880,
    "H4": 2880,
    "H8": 5760,
    "D1": 10080,
}

# ===================================================================
# TP/SL比率 - 元: dynamic_tf_selector.py L55
# ===================================================================
_TP_SL_RATIOS: dict[str, tuple[float, float]] = {
    "M1": (1.0, 1.3),
    "M5": (1.1, 1.4),
    "M15": (1.1, 1.4),
    "H1": (1.2, 1.6),
    "H4": (1.2, 1.6),
    "H8": (1.3, 1.8),
    "D1": (1.3, 1.8),
}

# ===================================================================
# SL base_mult - 元: parallel.py L235
# ===================================================================
_SL_BASE_MULT: dict[str, float] = {
    "M1": 0.8,
    "M5": 1.0,
    "M15": 1.2,
    "H1": 1.5,
    "H4": 2.0,
    "D1": 2.5,
}

# ===================================================================
# MTF重み - 元: mtf_features.py L72
# ===================================================================
_MTF_WEIGHTS: dict[str, float] = {
    "M1": 0.1,
    "M5": 0.15,
    "M15": 0.2,
    "M30": 0.25,
    "H1": 0.4,
    "H4": 0.6,
    "D1": 0.8,
    "W1": 1.0,
}

# ===================================================================
# SLマルチプライヤー - 元: timeframe_evaluator.py _calculate_sl_tp
# ===================================================================
_SL_MULTIPLIERS: dict[str, float] = {
    "M1": 1.2,
    "M5": 1.3,
    "M15": 1.4,
    "H1": 1.5,
    "H4": 1.6,
    "D1": 1.8,
}

# ===================================================================
# デフォルトTP/SL比率 - 元: timeframe_evaluator.py _calculate_sl_tp
# ===================================================================
_DEFAULT_TP_RATIOS: dict[str, float] = {
    "M1": 1.2,
    "M5": 1.3,
    "M15": 1.4,
    "H1": 1.5,
    "H4": 1.6,
    "D1": 1.8,
}

# ===================================================================
# TF別SL最大値（pips） - TFスケールに応じたSLキャップ
# ===================================================================
_SL_MAX_PIPS: dict[str, float] = {
    "M1": 20.0,
    "M5": 30.0,
    "M15": 50.0,
    "M30": 70.0,
    "H1": 100.0,
    "H4": 150.0,
    "H8": 200.0,
    "D1": 300.0,
}

# ===================================================================
# 構造ベースSL ATR倍率 - 元: timeframe_evaluator.py
# ===================================================================
_STRUCTURE_SL_MULT: dict[str, float] = {
    "M1": 1.2,
    "M5": 1.3,
    "M15": 1.5,
    "H1": 1.8,
    "H4": 2.0,
    "D1": 2.5,
}


# ===================================================================
# 公開API
# ===================================================================

def get_atr_multipliers(tf: str) -> tuple[float, float]:
    """ATR乗数を取得（補間付き）"""
    return interpolate_tf_param_tuple(_ATR_MULTIPLIERS, tf)


def get_normalized_min_score(tf: str) -> float:
    """正規化最小スコアを取得（補間付き）"""
    return interpolate_tf_param(_NORMALIZED_MIN_SCORES, tf)


def get_tf_weight(tf: str) -> float:
    """TF重みを取得（補間付き）"""
    return interpolate_tf_param(_TF_WEIGHTS, tf)


def get_tf_min_score(tf: str) -> float:
    """TFスコア閾値を取得（補間付き）"""
    return interpolate_tf_param(_TF_MIN_SCORES, tf)


def get_holding_minutes(tf: str) -> int:
    """保持時間（分）を取得（補間付き）"""
    return int(
        interpolate_tf_param(
            {k: float(v) for k, v in _HOLDING_MINUTES.items()}, tf
        )
    )


def get_tp_sl_ratio(tf: str) -> tuple[float, float]:
    """TP/SL比率を取得（補間付き）"""
    return interpolate_tf_param_tuple(_TP_SL_RATIOS, tf)


def get_sl_base_mult(tf: str) -> float:
    """SL base_multを取得（補間付き）"""
    return interpolate_tf_param(_SL_BASE_MULT, tf)


def get_mtf_weight(tf: str) -> float:
    """MTF重みを取得（補間付き）"""
    return interpolate_tf_param(_MTF_WEIGHTS, tf)


def get_sl_multiplier(tf: str) -> float:
    """SLマルチプライヤーを取得（補間付き）"""
    return interpolate_tf_param(_SL_MULTIPLIERS, tf)


def get_default_tp_ratio(tf: str) -> float:
    """デフォルトTP/SL比率を取得（補間付き）"""
    return interpolate_tf_param(_DEFAULT_TP_RATIOS, tf)


def get_structure_sl_mult(tf: str) -> float:
    """構造ベースSL ATR倍率を取得（補間付き）"""
    return interpolate_tf_param(_STRUCTURE_SL_MULT, tf)


def get_sl_max_pips(tf: str) -> float:
    """TF別SL最大値を取得（補間付き）"""
    return interpolate_tf_param(_SL_MAX_PIPS, tf)
