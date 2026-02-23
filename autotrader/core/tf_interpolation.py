"""TFパラメータ対数補間ユーティリティ

未定義TFのパラメータを既知TFから対数線形補間で推定する。
既知TFはそのまま返す（既存動作を完全保証）。
"""

from __future__ import annotations

import math

from autotrader.core.enums import Timeframe

# TF名→分数マッピング（Timeframe.minutes()と同等だが文字列キーで高速参照）
_TF_MINUTES: dict[str, int] = {tf.value: tf.minutes() for tf in Timeframe}


def interpolate_tf_param(
    known_values: dict[str, float],
    target_tf: str,
) -> float:
    """既知TFの値から対数線形補間で推定

    既知TFに含まれる場合はそのまま返す。

    Args:
        known_values: 既知TFの値 (例: {"M1": 1.0, "H1": 2.0})
        target_tf: 推定対象のTF名

    Returns:
        float: 補間された値

    Raises:
        ValueError: 既知TFが不足している場合
    """
    if target_tf in known_values:
        return known_values[target_tf]

    if len(known_values) < 2:
        raise ValueError(
            f"補間には2つ以上の既知TFが必要: {len(known_values)}個"
        )

    target_min = _TF_MINUTES.get(target_tf)
    if target_min is None:
        raise ValueError(f"不明なTF: {target_tf}")

    target_log = math.log(target_min)

    # 既知TFを分数でソート
    sorted_known = sorted(
        [
            (tf, val, math.log(_TF_MINUTES[tf]))
            for tf, val in known_values.items()
            if tf in _TF_MINUTES
        ],
        key=lambda x: x[2],
    )

    if not sorted_known:
        raise ValueError("既知TFが分数マッピングに存在しない")

    # target_logが範囲外の場合は最近接値で外挿
    if target_log <= sorted_known[0][2]:
        # 最小より小さい → 最小2つで外挿
        if len(sorted_known) >= 2:
            return _lerp_log(
                sorted_known[0], sorted_known[1], target_log
            )
        return sorted_known[0][1]

    if target_log >= sorted_known[-1][2]:
        # 最大より大きい → 最大2つで外挿
        if len(sorted_known) >= 2:
            return _lerp_log(
                sorted_known[-2], sorted_known[-1], target_log
            )
        return sorted_known[-1][1]

    # 範囲内 → 隣接2点で補間
    for i in range(len(sorted_known) - 1):
        lo = sorted_known[i]
        hi = sorted_known[i + 1]
        if lo[2] <= target_log <= hi[2]:
            return _lerp_log(lo, hi, target_log)

    # フォールバック（到達しないはず）
    return sorted_known[-1][1]


def interpolate_tf_param_tuple(
    known_values: dict[str, tuple[float, float]],
    target_tf: str,
) -> tuple[float, float]:
    """タプル版の対数線形補間

    Args:
        known_values: 既知TFのタプル値
        target_tf: 推定対象のTF名

    Returns:
        tuple[float, float]: 補間されたタプル
    """
    if target_tf in known_values:
        return known_values[target_tf]

    dict_0 = {tf: v[0] for tf, v in known_values.items()}
    dict_1 = {tf: v[1] for tf, v in known_values.items()}

    return (
        interpolate_tf_param(dict_0, target_tf),
        interpolate_tf_param(dict_1, target_tf),
    )


def _lerp_log(
    lo: tuple[str, float, float],
    hi: tuple[str, float, float],
    target_log: float,
) -> float:
    """対数スケールでの線形補間

    Args:
        lo: (tf名, 値, log分数) の下限点
        hi: (tf名, 値, log分数) の上限点
        target_log: ターゲットのlog分数

    Returns:
        float: 補間値
    """
    log_range = hi[2] - lo[2]
    if log_range == 0:
        return lo[1]
    t = (target_log - lo[2]) / log_range
    return lo[1] + t * (hi[1] - lo[1])
