"""アダプティブ調整によるパラメータオーバーライド"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdaptiveOverrides:
    """パラメータの差分オーバーライド

    frozen Config を置き換えず、差分として適用する。
    delta = 加算値、multiplier = 乗数。

    Attributes:
        consensus_threshold_delta: 閾値加算（正=厳格化）
        sl_multiplier: SL乗数（1.0=変更なし）
        stagnation_multiplier: 停滞exit時間の乗数
        penalty_cap_delta: ペナルティ上限の加算値
    """

    # コンセンサス閾値の加算値（正=厳しく、負=緩く）
    consensus_threshold_delta: float = 0.0
    # SL乗数（1.0=変更なし、1.2=20%拡大）
    sl_multiplier: float = 1.0
    # stagnation_exit_minutes の乗数
    stagnation_multiplier: float = 1.0
    # ペナルティ上限の調整（正=厳格化=上限下げ）
    penalty_cap_delta: float = 0.0

    # デフォルトインスタンス（調整なし）
    NEUTRAL: AdaptiveOverrides = None  # type: ignore[assignment]

    @property
    def is_active(self) -> bool:
        """いずれかが非デフォルトか"""
        return (
            self.consensus_threshold_delta != 0.0
            or self.sl_multiplier != 1.0
            or self.stagnation_multiplier != 1.0
            or self.penalty_cap_delta != 0.0
        )


# クラス定義後にデフォルトインスタンスを設定
AdaptiveOverrides.NEUTRAL = AdaptiveOverrides()  # type: ignore[attr-defined]
