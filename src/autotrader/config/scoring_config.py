"""スコアリング設定の単一ソース."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoringConfig:
    """スコアリング閾値の一元管理.

    Attributes:
        rsi_oversold: RSI売られすぎ閾値
        rsi_overbought: RSI買われすぎ閾値
        adx_threshold: ADXトレンド判定閾値
        stoch_oversold: ストキャスティクス売られすぎ
        stoch_overbought: ストキャスティクス買われすぎ
        macd_norm_factor: MACD正規化係数
        bb_lower_threshold: ボリンジャーバンド下限閾値
        bb_upper_threshold: ボリンジャーバンド上限閾値
    """

    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    adx_threshold: float = 25.0
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0
    macd_norm_factor: float = 0.5
    bb_lower_threshold: float = 0.2
    bb_upper_threshold: float = 0.8


@dataclass(frozen=True)
class TimeframeScoring:
    """時間足別スコアリング設定.

    Attributes:
        min_scores: 時間足別の最小スコア閾値
        weights: 時間足別の重み
    """

    min_scores: dict[str, float] = field(default_factory=lambda: {
        "M1": 2.0,
        "M5": 2.25,
        "M15": 2.7,
        "H1": 3.0,
        "H4": 3.3,
        "D1": 3.75,
    })

    weights: dict[str, float] = field(default_factory=lambda: {
        "M1": 0.5,
        "M5": 0.8,
        "M15": 1.0,
        "H1": 1.5,
        "H4": 2.0,
        "D1": 2.5,
    })


# デフォルトインスタンス
DEFAULT_SCORING = ScoringConfig()
DEFAULT_TF_SCORING = TimeframeScoring()
