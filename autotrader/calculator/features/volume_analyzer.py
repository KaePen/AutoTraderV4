"""ボリューム分析フィーチャーモジュール

tick_volume のMA比率を計算し、
ブレイクアウト確認やフェイクシグナル検出に活用する。
"""

from __future__ import annotations

import pandas as pd


def compute_volume_features(
    df: pd.DataFrame,
    ma_period: int = 20,
) -> pd.DataFrame:
    """ボリュームフィーチャーを計算

    Args:
        df: OHLCVデータ（volume列必須）
        ma_period: 移動平均期間

    Returns:
        pd.DataFrame: volume_ma_{period}, volume_ratio 列が
            追加されたDF
    """
    if "volume" not in df.columns:
        return df

    col_ma = f"volume_ma_{ma_period}"
    _vol_ma = df["volume"].rolling(ma_period).mean()
    df[col_ma] = _vol_ma
    df["volume_ratio"] = df["volume"] / _vol_ma.replace(
        0, float("nan")
    )
    return df
