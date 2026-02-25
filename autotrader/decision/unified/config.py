"""統合トレードボット設定"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TimeframeConfig:
    """時間足ごとの設定

    Attributes:
        timeframe: 時間足
        max_positions: 最大ポジション数
        enabled: 有効フラグ
        volume_multiplier: ボリューム倍率
    """

    timeframe: str = "M15"
    max_positions: int = 1
    enabled: bool = True
    volume_multiplier: float = 1.0


@dataclass(frozen=True)
class StrengthConfig:
    """指標強度計算設定

    Attributes:
        rsi_oversold: RSI売られすぎ閾値
        rsi_overbought: RSI買われすぎ閾値
        macd_norm_factor: MACDヒストグラム正規化係数
        adx_threshold: ADXトレンド判定閾値
        stoch_oversold: ストキャスティクス売られすぎ閾値
        stoch_overbought: ストキャスティクス買われすぎ閾値
        bb_lower_threshold: BB下限閾値
        bb_upper_threshold: BB上限閾値
    """

    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    macd_norm_factor: float = 0.5
    adx_threshold: float = 25.0
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0
    bb_lower_threshold: float = 0.2
    bb_upper_threshold: float = 0.8


@dataclass(frozen=True)
class EvaluatorConfig:
    """時間足評価器設定

    Attributes:
        timeframe: 対象時間足
        strength_config: 指標強度計算設定
        min_score: 最小スコア閾値
        atr_sl_multiplier: ATRベースSL係数
        atr_tp_multiplier: ATRベースTP係数
    """

    timeframe: str = "M15"
    strength_config: StrengthConfig = field(
        default_factory=StrengthConfig
    )
    min_score: float = 5.0
    atr_sl_multiplier: float = 1.5
    atr_tp_multiplier: float = 2.0


@dataclass(frozen=True)
class ConsolidatorConfig:
    """シグナル統合器設定

    Attributes:
        min_alignment: 最小一致時間足数
        confidence_threshold: 最小確度閾値
    """

    min_alignment: int = 3
    confidence_threshold: float = 0.5


@dataclass(frozen=True)
class RiskConfig:
    """リスク管理設定

    Attributes:
        max_daily_loss_pct: 日次最大損失率
        max_position_size: 最大ポジションサイズ
        default_sl_pips: デフォルトSL(pips)
        default_tp_pips: デフォルトTP(pips)
        cooldown_minutes: クールダウン時間（分）
        max_daily_trades: 日次最大トレード件数（0=無制限）
    """

    max_daily_loss_pct: float = 0.05
    max_position_size: float = 0.1
    default_sl_pips: float = 15.0
    default_tp_pips: float = 25.0
    cooldown_minutes: int = 5
    max_daily_trades: int = 0


@dataclass(frozen=True)
class UnifiedBotConfig:
    """統合ボット設定

    Attributes:
        consolidator: シグナル統合器設定（レガシー、後方互換用）
        risk: リスク管理設定
        timeframes: 評価対象時間足リスト
        evaluator_configs: 時間足別評価器設定
        min_adx: 最小ADX閾値（レガシー、RegimeDetectorへ移行）
        require_htf_trend: 上位足トレンド一致を要求（レガシー）
        tp_sl_ratio: TP/SL比率（レガシー、TradingPlanへ移行）
        enable_position_sizing: ポジションサイジングを有効にするか
        enable_position_manager: ポジション管理を有効にするか
    """

    consolidator: ConsolidatorConfig = field(
        default_factory=ConsolidatorConfig
    )
    risk: RiskConfig = field(default_factory=RiskConfig)
    timeframes: list[str] = field(
        default_factory=lambda: ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    )
    evaluator_configs: dict[str, EvaluatorConfig] = field(
        default_factory=dict
    )
    min_adx: float = 20.0
    require_htf_trend: bool = True
    tp_sl_ratio: float = 1.2
    timeframe_configs: dict[str, TimeframeConfig] = field(
        default_factory=dict
    )
    enable_position_sizing: bool = True
    enable_position_manager: bool = True
    use_position_manager: bool = True
    range_day_bbw_threshold: float = 0.25
    range_day_score_premium: float = 0.3
    # Weak Hours RANGEフィルター（JST 18-21 = UTC 9-12）
    weak_hours_enabled: bool = True
    weak_hours_score_premium: float = 0.5
    # 動的ロットサイズ（PositionSizer結果をSimulatorに渡す）
    use_dynamic_lot: bool = True
    # 資金管理パラメータ（PositionSizerConfigに渡す）
    max_lot_per_trade: float = 5.0
    max_total_exposure_lot: float = 10.0
    base_risk_pct: float = 0.04
    max_risk_pct_absolute: float = 0.07
    equity_floor_pct: float = 0.30
    equity_caution_pct: float = 0.50
    # SLスリッページバッファ（pips）
    slippage_buffer_pips: float = 2.0
    # デモモード（閾値を下げて活発にシグナルを発火させる）
    demo_mode: bool = False
    # コンセンサス閾値
    consensus_threshold: float = 8.0
    # 最大同時ポジション数（ライブ用）
    max_positions: int = 3
    # デモモード時の最大同時ポジション数
    demo_max_positions: int = 1
    # デモモード時のクールダウン時間（分）: 0=無効
    demo_cooldown_minutes: int = 0
    # デモモード時の日次最大トレード件数（0=無制限）
    demo_max_daily_trades: int = 5
    # デモモード時のコンセンサス閾値（大幅低下で活発にシグナル発火）
    demo_consensus_threshold: float = 1.5
    # 品質ベース動的ポジション枠（バックテストと同一ロジック）
    bonus_max_positions: int = 0
    bonus_score_threshold: float = 7.0
    # レジーム検出に使用するTF
    regime_detection_tf: str = "H1"
    # HTF整合性チェックに使用するTFリスト
    htf_alignment_tfs: list[str] = field(
        default_factory=lambda: ["H4", "D1"]
    )
    # --- TradingPlan デフォルト設定 ---
    default_primary_tf: str = "M15"
    default_entry_tf: str = "M5"
    default_manage_tf: str = "M15"
    default_max_holding_bars: int = 32
    default_tp_sl_ratio_range: tuple[float, float] = (1.1, 1.4)
    # --- フィルター閾値 ---
    macd_slope_filter_threshold: float = -2.0
    sl_min_pips: float = 20.0
    sl_max_pips_default: float = 50.0
    # ペナルティ上限（これ以上でエントリーブロック）
    penalty_cap: float = 0.3
    # トレンド強度上限（過大なトレンド強度でブロック）
    trend_strength_max: float = 0.8
    # --- コンセンサス重み ---
    consensus_primary_weight: float = 2.0
    consensus_entry_weight: float = 1.5
    consensus_confirm_weight: float = 3.0
    consensus_manage_weight: float = 0.5
    consensus_other_weight: float = 1.0
    # --- SoftGuardペナルティ ---
    sg_spread_penalty_rate: float = 0.2
    sg_off_hours_penalty: float = 0.25
    sg_volatility_penalty: float = 0.05
    sg_recent_loss_penalty: float = 0.1
    # レジーム別閾値調整（TRENDレジームでスコア要求引き上げ）
    regime_threshold_enabled: bool = False
    regime_trend_threshold_add: float = 1.5

    def get_evaluator_config(self, timeframe: str) -> EvaluatorConfig:
        """時間足別評価器設定を取得

        Args:
            timeframe: 時間足

        Returns:
            EvaluatorConfig: 評価器設定
        """
        if timeframe in self.evaluator_configs:
            return self.evaluator_configs[timeframe]
        return EvaluatorConfig(timeframe=timeframe)

    def get_timeframe_config(self, timeframe: str) -> TimeframeConfig:
        """時間足別設定を取得

        Args:
            timeframe: 時間足

        Returns:
            TimeframeConfig: 時間足設定
        """
        if timeframe in self.timeframe_configs:
            return self.timeframe_configs[timeframe]
        return TimeframeConfig(timeframe=timeframe)
