"""ティックエントリー最適化設定

M1シグナル発生後のティック監視によるエントリー最適化の
パラメータを定義する。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TickEntryConfig:
    """ティックエントリー最適化設定

    Attributes:
        enabled: ティック最適化の有効/無効
        enabled_modes: 有効な取引モード名タプル
        poll_interval_sec: ポーリング間隔（秒）
        max_monitoring_sec: 監視タイムアウト（秒）
        spread_threshold_pips: スプレッド閾値（pips）
        spread_weight: スプレッド条件の重み
        momentum_window_ticks: モメンタム評価tick数
        momentum_min_ticks: 最低観測tick数
        momentum_threshold: 方向一致率閾値
        momentum_weight: モメンタム条件の重み
        retracement_enabled: リトレース評価の有効/無効
        retracement_weight: リトレース条件の重み
        composite_threshold: 総合スコア閾値
        execute_on_timeout: タイムアウト時に成行実行
        conflict_policy: 新シグナル衝突時ポリシー
    """

    enabled: bool = False
    enabled_modes: tuple[str, ...] = ("SCALPING",)
    poll_interval_sec: float = 0.1
    max_monitoring_sec: float = 30.0
    spread_threshold_pips: float = 1.5
    spread_weight: float = 0.4
    momentum_window_ticks: int = 10
    momentum_min_ticks: int = 5
    momentum_threshold: float = 0.6
    momentum_weight: float = 0.35
    retracement_enabled: bool = False
    retracement_weight: float = 0.25
    composite_threshold: float = 0.6
    execute_on_timeout: bool = True
    conflict_policy: str = "replace"
