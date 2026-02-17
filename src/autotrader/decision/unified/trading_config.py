"""統合トレーディング設定

全ての戦略設定を一元管理し、設定変更の一貫性を確保する。
TP/SL比率、エントリー閾値、HTFフィルター設定などを集約。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TradingMode(Enum):
    """トレーディングモード"""

    SCALP = "scalp"
    SHORT_MID = "short_mid"
    SWING = "swing"


@dataclass(frozen=True)
class ModeConfig:
    """モード別設定

    Attributes:
        mode: トレーディングモード
        timeframes: 使用する時間足リスト
        tp_sl_ratio_min: TP/SL比率下限
        tp_sl_ratio_max: TP/SL比率上限
        min_score: エントリー最小スコア
        min_confidence: エントリー最小確度
        htf_alignment_required: HTF整合性が必須か
    """

    mode: TradingMode
    timeframes: tuple[str, ...]
    tp_sl_ratio_min: float
    tp_sl_ratio_max: float
    min_score: float
    min_confidence: float
    htf_alignment_required: bool = True


@dataclass
class TradingConfig:
    """統合トレーディング設定

    全ての戦略パラメータを一元管理。
    """

    # モード別設定
    MODE_CONFIGS: dict[TradingMode, ModeConfig] = field(default_factory=lambda: {
        TradingMode.SCALP: ModeConfig(
            mode=TradingMode.SCALP,
            timeframes=("M1", "M5", "M15"),
            tp_sl_ratio_min=1.5,
            tp_sl_ratio_max=2.0,
            min_score=2.0,
            min_confidence=0.5,
            htf_alignment_required=True,
        ),
        TradingMode.SHORT_MID: ModeConfig(
            mode=TradingMode.SHORT_MID,
            timeframes=("M15", "H1", "H4"),
            tp_sl_ratio_min=1.6,
            tp_sl_ratio_max=2.2,
            min_score=2.5,
            min_confidence=0.55,
            htf_alignment_required=True,
        ),
        TradingMode.SWING: ModeConfig(
            mode=TradingMode.SWING,
            timeframes=("H1", "H4", "D1"),
            tp_sl_ratio_min=1.8,
            tp_sl_ratio_max=2.5,
            min_score=3.0,
            min_confidence=0.6,
            htf_alignment_required=True,
        ),
    })

    # 時間足別設定
    TIMEFRAME_CONFIGS: dict[str, dict] = field(default_factory=lambda: {
        "M1": {
            "sl_atr_mult": 1.2,
            "min_adx": 10.0,
            "weight": 0.5,
        },
        "M5": {
            "sl_atr_mult": 1.3,
            "min_adx": 8.0,
            "weight": 0.7,
        },
        "M15": {
            "sl_atr_mult": 1.5,
            "min_adx": 5.0,
            "weight": 1.0,
        },
        "H1": {
            "sl_atr_mult": 1.8,
            "min_adx": 5.0,
            "weight": 1.2,
        },
        "H4": {
            "sl_atr_mult": 2.0,
            "min_adx": 5.0,
            "weight": 1.5,
        },
        "D1": {
            "sl_atr_mult": 2.5,
            "min_adx": 5.0,
            "weight": 1.8,
        },
    })

    # RSI設定
    rsi_overbought: float = 75.0
    rsi_oversold: float = 25.0
    rsi_extreme_overbought: float = 85.0
    rsi_extreme_oversold: float = 15.0

    # ADX設定
    adx_strong_trend: float = 25.0
    adx_very_strong_trend: float = 40.0

    # HTFフィルター設定
    htf_counter_trend_penalty: float = 0.5  # 逆トレンド時のスコア乗数
    htf_alignment_bonus: float = 2.0  # 整合時のボーナス
    htf_strong_alignment_bonus: float = 4.0  # 強い整合時のボーナス

    # SMCスコアリング設定
    smc_bos_score: float = 3.0
    smc_choch_score: float = 2.0
    smc_liquidity_grab_score: float = 2.5
    smc_structure_alignment_score: float = 1.0
    smc_swing_level_score: float = 1.0

    # SL/TP設定
    min_sl_pips: float = 8.0
    max_sl_pips: float = 60.0
    default_rr_ratio: float = 1.5

    # 執行コスト
    spread_pips: float = 1.5
    slippage_pips: float = 0.5

    def get_mode_config(self, mode: TradingMode) -> ModeConfig:
        """モード設定を取得

        Args:
            mode: トレーディングモード

        Returns:
            ModeConfig: モード設定
        """
        return self.MODE_CONFIGS[mode]

    def get_timeframe_config(self, timeframe: str) -> dict:
        """時間足設定を取得

        Args:
            timeframe: 時間足

        Returns:
            dict: 時間足設定
        """
        return self.TIMEFRAME_CONFIGS.get(timeframe, self.TIMEFRAME_CONFIGS["M15"])

    def get_recommended_tp_sl_ratio(self, mode: TradingMode) -> float:
        """推奨TP/SL比率を取得

        Args:
            mode: トレーディングモード

        Returns:
            float: 推奨TP/SL比率（範囲の中央値）
        """
        config = self.get_mode_config(mode)
        return (config.tp_sl_ratio_min + config.tp_sl_ratio_max) / 2

    def get_sl_atr_multiplier(self, timeframe: str) -> float:
        """SL用ATR乗数を取得

        Args:
            timeframe: 時間足

        Returns:
            float: ATR乗数
        """
        return self.get_timeframe_config(timeframe).get("sl_atr_mult", 1.5)


# グローバルインスタンス
TRADING_CONFIG = TradingConfig()
