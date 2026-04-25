"""予測モード設定"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PredictionConfig:
    """予測ベーストレードモード設定

    Attributes:
        direction_tf: 方向予測に使用する時間足
        direction_horizon_bars: 予測ホライズン（direction_tfの足数）
        direction_threshold: UP/DOWNとして採用するための最小確率
        direction_atr_label_mult: ラベル構築時のATR倍率
            （±ATR*mult超の変化をUP/DOWNとして分類）
        timing_tf: タイミング確認の時間足
        timing_macd_confirm: MACD方向一致を確認するか
        timing_ema_confirm: EMA配列を確認するか
        entry_tf: エントリー時間足
        sl_atr_mult: SL幅のATR倍率
        tp_atr_mult: TP幅のATR倍率
        divergence_enabled: 乖離モニタリング有効化
        divergence_check_interval_bars: 再予測間隔（entry_tfの足数）
        divergence_exit_threshold: この確率以下で即撤退
        divergence_rapid_decay: 2バー以内にこの量だけ確率低下で撤退
        divergence_decay_bars: 最大保持バー数（超過+下降傾向で撤退）
        model_dir: モデル保存ディレクトリ
        walk_forward_is_months: Walk-forward IS期間（月）
        walk_forward_oos_months: Walk-forward OOS期間（月）
        min_training_samples: 最小学習サンプル数
        n_estimators: LightGBMの決定木数
        learning_rate: 学習率
        max_depth: 最大深さ（-1=制限なし）
        num_leaves: 最大葉数
        min_child_samples: 葉の最小サンプル数
        feature_fraction: 特徴量サブサンプリング率
        class_weight: クラス重み（"balanced"=自動バランス）
    """

    # --- 方向予測 ---
    direction_tf: str = "H4"
    direction_horizon_bars: int = 6
    direction_threshold: float = 0.6
    direction_atr_label_mult: float = 1.0

    # --- タイミング確認 ---
    timing_tf: str = "H1"
    timing_macd_confirm: bool = True
    timing_ema_confirm: bool = True

    # --- エントリー ---
    entry_tf: str = "M15"
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.5

    # --- 乖離モニタリング ---
    divergence_enabled: bool = True
    divergence_check_interval_bars: int = 1
    divergence_exit_threshold: float = 0.3
    divergence_rapid_decay: float = 0.25
    divergence_decay_bars: int = 12

    # --- モデル管理 ---
    model_dir: str = "data/models/prediction"
    walk_forward_is_months: int = 12
    walk_forward_oos_months: int = 3
    min_training_samples: int = 2000

    # --- LightGBMハイパーパラメータ ---
    n_estimators: int = 500
    learning_rate: float = 0.05
    max_depth: int = 6
    num_leaves: int = 31
    min_child_samples: int = 50
    feature_fraction: float = 0.8
    class_weight: str = "balanced"
