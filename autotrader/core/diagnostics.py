"""診断用データ構造モジュール

シグナル生成パイプラインの各段階を記録する
データクラスを提供する。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SignalStepRecord:
    """シグナル生成1ステップの記録

    Attributes:
        timestamp: タイムスタンプ
        regime: 相場レジーム
        volatility: ボラティリティ
        mode: トレーディングモード
        primary_tf: 主要時間足
        risk_passed: リスク管理通過
        risk_reason: リスク管理拒否理由
        tf_details: TF別評価詳細
        consensus_direction: コンセンサス方向
        consensus_score: コンセンサススコア
        consensus_threshold: コンセンサス閾値
        consensus_passed: コンセンサス通過
        htf_passed: HTFフィルター通過
        htf_direction: HTF方向
        sl_pips: SL距離
        tp_pips: TP距離
        final_direction: 最終シグナル方向
        hold_reason: HOLD理由
    """

    timestamp: str
    # レジーム
    regime: str = ""
    volatility: float = 0.0
    # モード
    mode: str = ""
    primary_tf: str = ""
    # リスク管理
    risk_passed: bool = True
    risk_reason: str = ""
    # TF別評価
    tf_details: dict[str, dict] = field(default_factory=dict)
    # コンセンサス
    consensus_direction: str = "HOLD"
    consensus_score: float = 0.0
    consensus_threshold: float = 0.0
    consensus_passed: bool = False
    # HTFフィルター
    htf_passed: bool = True
    htf_direction: str = ""
    # SL/TP
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    # 最終結果
    final_direction: str = "HOLD"
    hold_reason: str = ""
    # エッジ検定
    edge_alert_level: str = ""
    edge_rolling_winrate: float = 0.0
    edge_rolling_pf: float = 0.0
    # マクロレジーム
    macro_regime_level: str = ""
    macro_vix: float | None = None
