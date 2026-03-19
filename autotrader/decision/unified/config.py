"""統合トレードボット設定"""

from __future__ import annotations

from dataclasses import dataclass, field


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
        pip_unit: 1pipの価格単位（JPY系=0.01、非JPY系=0.0001）
    """

    timeframe: str = "M15"
    strength_config: StrengthConfig = field(default_factory=StrengthConfig)
    min_score: float = 5.0
    atr_sl_multiplier: float = 1.5
    atr_tp_multiplier: float = 2.0
    pip_unit: float = 0.01
    # SL最小値（pips）: UnifiedBotConfig.sl_min_pipsから伝搬
    sl_min_pips: float = 20.0
    # SL最大値（pips）: UnifiedBotConfig.sl_max_pips_defaultから伝搬
    sl_max_pips: float = 50.0
    ema_cross_penalty: float | None = (
        None  # EMAクロス矛盾ペナルティ（None=-2.5）
    )
    # --- MACDスロープスコアリング ---
    # ATR比デッドゾーン（0.0=無効、0.01=ATR1%未満の変化を無視）
    macd_slope_deadzone_atr_ratio: float = 0.0
    # MACDスロープ順方向ボーナス
    macd_slope_bonus: float = 2.5
    # MACDスロープ逆方向ペナルティ（正の値、内部で減算）
    macd_slope_penalty: float = 2.0
    # --- HTF整合スコアリング ---
    # HTF強整合ボーナス（2TF以上一致）
    htf_align_bonus_strong: float = 4.0
    # HTF弱整合ボーナス（1TF一致）
    htf_align_bonus_weak: float = 2.0


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
    # Layer 1: 動的クールダウン
    dynamic_cooldown_enabled: bool = True
    dynamic_cooldown_base_minutes: int = 5
    dynamic_cooldown_per_position_minutes: int = 5
    dynamic_cooldown_max_minutes: int = 30
    # Layer 2: 同方向プログレッシブ閾値
    progressive_threshold_enabled: bool = True
    progressive_threshold_per_position: float = 0.5
    # Layer 3: ボラティリティ連動ポジション上限
    volatility_position_limit_enabled: bool = True
    high_vol_max_positions_ratio: float = 0.5
    # Layer 4: ポートフォリオ・サーキットブレーカー
    circuit_breaker_enabled: bool = True
    circuit_breaker_loss_pct: float = 0.05
    circuit_breaker_pause_minutes: int = 60
    # Layer 5: 急速DD検知パウズ
    rapid_dd_pause_enabled: bool = True
    rapid_dd_window_minutes: int = 30
    rapid_dd_threshold_pct: float = 0.02
    rapid_dd_pause_duration_minutes: int = 30
    # Layer 6: 連続敗戦サーキットブレーカー
    consecutive_loss_breaker_enabled: bool = True
    consecutive_loss_breaker_threshold: int = 8
    consecutive_loss_breaker_pause_minutes: int = 60


# ===================================================================
# 論理サブConfig（UnifiedBotConfig の責務分割）
# ===================================================================


@dataclass(frozen=True)
class SignalConfig:
    """シグナル生成・コンセンサス関連設定

    Attributes:
        consensus_threshold: コンセンサス閾値
        consensus_primary_weight: プライマリTF重み
        consensus_entry_weight: エントリーTF重み
        consensus_confirm_weight: 確認TF重み
        consensus_manage_weight: 管理TF重み
        consensus_other_weight: その他TF重み
        bca_enabled: BCA有効化フラグ
        bca_min_edge: BCA最小方向性エッジ
        bca_penalty_scale: BCAペナルティ係数
        htf_score_filter_enabled: HTFスコアフィルター有効化
        htf_score_filter_min_alignment: HTF整合度閾値
        htf_score_filter_threshold_add: HTF不一致時追加閾値
        regime_detection_tf: レジーム検出TF
        htf_alignment_tfs: HTF整合性チェックTF
        macd_slope_filter_threshold: MACDスロープ閾値
        trend_strength_max: トレンド強度上限
    """

    consensus_threshold: float = 18.0
    consensus_primary_weight: float = 2.0
    consensus_entry_weight: float = 1.5
    consensus_confirm_weight: float = 3.0
    consensus_manage_weight: float = 0.5
    consensus_other_weight: float = 1.0
    bca_enabled: bool = True
    bca_min_edge: float = 0.60
    bca_penalty_scale: float = 1.0
    htf_score_filter_enabled: bool = True
    htf_score_filter_min_alignment: float = 0.1
    htf_score_filter_threshold_add: float = 1.0
    regime_detection_tf: str = "H1"
    htf_alignment_tfs: list[str] = field(default_factory=lambda: ["H4", "D1"])
    macd_slope_filter_threshold: float = -2.0
    trend_strength_max: float = 0.7


@dataclass(frozen=True)
class RiskManagementConfig:
    """SL/TP・資金管理・ポジションサイジング設定

    Attributes:
        sl_min_pips: SL最小値（pips）
        sl_max_pips_default: SLデフォルト最大値
        trend_sl_min_pips: TREND時SL最小値上書き
        trend_sl_max_pips: TREND時SL上限キャップ
        penalty_cap: ペナルティ上限
        default_tp_sl_ratio_range: TP/SLレンジ
        max_positions: 最大同時ポジション数
        bonus_max_positions: 品質ベース追加枠
        bonus_score_threshold: 追加枠スコア閾値
        max_lot_per_trade: 1トレード最大ロット
        max_total_exposure_lot: 総エクスポージャー上限
        base_risk_pct: 基本リスク率
        max_risk_pct_absolute: リスク率絶対上限
        equity_floor_pct: 資金下限率
        equity_caution_pct: 資金警告率
        slippage_buffer_pips: スリッページバッファ
        use_dynamic_lot: 動的ロット使用
        enable_position_sizing: ポジションサイジング有効
        use_position_manager: ポジション管理有効
    """

    sl_min_pips: float = 20.0
    sl_max_pips_default: float = 50.0
    trend_sl_min_pips: float | None = None
    trend_sl_max_pips: float | None = None
    penalty_cap: float = 0.3
    default_tp_sl_ratio_range: tuple[float, float] = (1.1, 1.4)
    max_positions: int = 3
    bonus_max_positions: int = 0
    bonus_score_threshold: float = 7.0
    max_lot_per_trade: float = 5.0
    max_total_exposure_lot: float = 10.0
    base_risk_pct: float = 0.05
    max_risk_pct_absolute: float = 0.07
    equity_floor_pct: float = 0.30
    equity_caution_pct: float = 0.50
    slippage_buffer_pips: float = 2.0
    use_dynamic_lot: bool = True
    enable_position_sizing: bool = True
    use_position_manager: bool = True


@dataclass(frozen=True)
class FilterConfig:
    """レジームフィルター・時間帯フィルター・SoftGuard設定

    Attributes:
        range_day_bbw_threshold: BBW閾値
        range_day_score_premium: RANGEプレミアム
        weak_hours_enabled: Weak Hours有効化
        weak_hours_score_premium: Weak Hoursプレミアム
        sg_spread_penalty_rate: スプレッドペナルティ率
        sg_off_hours_penalty: オフアワーペナルティ
        sg_volatility_penalty: ボラティリティペナルティ
        sg_recent_loss_penalty: 直近損失ペナルティ
        regime_threshold_enabled: レジーム閾値調整有効
        regime_trend_threshold_add: TREND時追加閾値
        fundamental_assessor_enabled: ファンダメンタルアセッサー
        off_hours_trend_block: オフアワーTRENDブロック
        off_hours_high_align_block: オフアワー高alignment複合ブロック
        off_hours_high_align_threshold: 複合ブロック閾値
        high_align_penalty_threshold: 高alignmentペナルティ閾値
        high_align_penalty_score: ペナルティスコア
    """

    range_day_bbw_threshold: float = 0.25
    range_day_score_premium: float = 0.3
    weak_hours_enabled: bool = True
    weak_hours_score_premium: float = 0.5
    sg_spread_penalty_rate: float = 0.2
    sg_off_hours_penalty: float = 0.5
    sg_volatility_penalty: float = 0.05
    sg_recent_loss_penalty: float = 0.1
    regime_threshold_enabled: bool = True
    regime_trend_threshold_add: float = 1.5
    fundamental_caution_block_level: int = 2
    fundamental_holiday_liquidity_block: float = 0.3
    fundamental_decay_coefficient: float = 2.0
    fundamental_assessor_enabled: bool = False
    fundamental_softguard_enabled: bool = False
    fundamental_pm_enabled: bool = False
    fundamental_post_event_lag_seconds: int = 30
    range_filter_consolidated: bool = False
    range_filter_block_threshold: float = 0.6
    session_transition_wait_enabled: bool = False
    session_transition_wait_minutes: int = 30
    liquidity_based_tp_enabled: bool = False
    liquidity_tp_margin_pct: float = 0.01
    liquidity_tp_default_rr: float = 1.5
    use_actual_spread_data: bool = False
    off_hours_trend_block: bool = False
    off_hours_high_align_block: bool = False
    off_hours_high_align_threshold: float = 0.55
    high_align_penalty_threshold: float | None = None
    high_align_penalty_score: float = 1.0
    # ボリュームフィルタ
    volume_filter_enabled: bool = True
    volume_filter_threshold: float = 1.5
    volume_filter_penalty: float = 0.8
    # ペア別スプレッド閾値
    sg_spread_threshold_pips: float | None = None
    # マクロレジームフィルタ
    macro_regime_enabled: bool = False
    macro_regime_vix_elevated: float = 20.0
    macro_regime_vix_high_fear: float = 30.0
    macro_regime_vix_extreme_fear: float = 40.0
    macro_regime_elevated_penalty: float = 0.1
    macro_regime_high_fear_penalty: float = 0.3


@dataclass(frozen=True)
class UnifiedBotConfig:
    """統合ボット設定

    Attributes:
        consolidator: シグナル統合器設定（レガシー、後方互換用）
        risk: リスク管理設定
        timeframes: 評価対象時間足リスト
        evaluator_configs: 時間足別評価器設定
        tp_sl_ratio: TP/SL比率（レガシー、TradingPlanへ移行）
        enable_position_sizing: ポジションサイジングを有効にするか
        enable_position_manager: ポジション管理を有効にするか
    """

    consolidator: ConsolidatorConfig = field(
        default_factory=ConsolidatorConfig
    )
    risk: RiskConfig = field(default_factory=RiskConfig)
    timeframes: list[str] = field(
        default_factory=lambda: [
            "M1",
            "M5",
            "M15",
            "M30",
            "H1",
            "H4",
            "H8",
            "D1",
        ]
    )
    evaluator_configs: dict[str, EvaluatorConfig] = field(default_factory=dict)
    tp_sl_ratio: float = 1.2
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
    base_risk_pct: float = 0.05
    max_risk_pct_absolute: float = 0.07
    equity_floor_pct: float = 0.30
    equity_caution_pct: float = 0.50
    # SLスリッページバッファ（pips）
    slippage_buffer_pips: float = 2.0
    # デモモード（閾値を下げて活発にシグナルを発火させる）
    demo_mode: bool = False
    # コンセンサス閾値（BT・ライブ共通）
    consensus_threshold: float = 18.0
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
    htf_alignment_tfs: list[str] = field(default_factory=lambda: ["H4", "D1"])
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
    trend_strength_max: float = 0.7
    # --- コンセンサス重み ---
    consensus_primary_weight: float = 2.0
    consensus_entry_weight: float = 1.5
    consensus_confirm_weight: float = 3.0
    consensus_manage_weight: float = 0.5
    consensus_other_weight: float = 1.0
    # --- SoftGuardペナルティ ---
    sg_spread_penalty_rate: float = 0.2
    sg_off_hours_penalty: float = 0.5
    sg_volatility_penalty: float = 0.05
    sg_recent_loss_penalty: float = 0.1
    # レジーム別閾値調整（TRENDレジームでスコア要求引き上げ）
    regime_threshold_enabled: bool = True
    regime_trend_threshold_add: float = 1.5
    # --- BREAKOUT検出設定 ---
    # ブレイクアウト検出有効化
    regime_breakout_enabled: bool = True
    # ブレイクアウト判定のルックバック期間（足数）
    regime_breakout_lookback: int = 20
    # BREAKOUT時のコンセンサス閾値調整（0.0=追加なし）
    regime_breakout_threshold_add: float = 0.0
    # BREAKOUT時のTP倍率（利益を伸ばす）
    regime_breakout_tp_multiplier: float = 1.5
    # BREAKOUT時のSL下限（None=sl_min_pipsを使用）
    regime_breakout_sl_min_pips: float | None = None
    # BREAKOUT時のSL上限（None=sl_max_pipsを使用）
    regime_breakout_sl_max_pips: float | None = None
    # --- ボラティリティ方向検出設定 ---
    # ボラ方向検出有効化
    vol_direction_enabled: bool = True
    # EXPANDING時のATR変化率閾値
    vol_expanding_threshold: float = 0.3
    # COMPRESSING時のATR変化率閾値
    vol_compressing_threshold: float = -0.2
    # EXPANDING時のSoftGuardペナルティ
    vol_expanding_penalty: float = 0.1
    # EXPANDING時のSL拡大倍率（1.0=変更なし）
    vol_expanding_sl_multiplier: float = 1.0
    # --- CHOPPY検出設定 ---
    # CHOPPY検出有効化
    choppy_enabled: bool = True
    # Choppiness Index閾値（61.8=フィボナッチ）
    choppy_ci_threshold: float = 61.8
    # CHOPPY時のコンセンサス閾値上乗せ（実質トレード抑制）
    choppy_threshold_add: float = 3.0
    # HTFスコア最低要件（score_htf=0はWR低下）
    htf_score_filter_enabled: bool = True
    # HTF整合度がこの値以下のとき閾値を追加
    htf_score_filter_min_alignment: float = 0.1
    # HTF不一致時の追加閾値
    htf_score_filter_threshold_add: float = 1.0
    # --- Phase 2a: ファンダメンタル統合設定 ---
    # ハードガード: 注意度がこの値以上でエントリーブロック
    fundamental_caution_block_level: int = 2
    # ハードガード: 休日流動性がこの値未満でブロック
    fundamental_holiday_liquidity_block: float = 0.3
    # 時間減衰係数（大きいほど急速に減衰）
    fundamental_decay_coefficient: float = 2.0
    # --- Phase 2b: ファンダメンタルロジック統合 ---
    # リスクアセッサー有効化
    fundamental_assessor_enabled: bool = False
    # SoftGuardファンダメンタルペナルティ有効化
    fundamental_softguard_enabled: bool = False
    # PositionManagerファンダメンタル管理有効化
    fundamental_pm_enabled: bool = False
    # LLM処理ラグ（秒）: 発表後bias/surpriseが利用可能になるまでの遅延
    fundamental_post_event_lag_seconds: int = 30
    # --- RANGEフィルタ統合 ---
    # 統合RANGEフィルタ有効化（Falseで従来の個別フィルタを使用）
    range_filter_consolidated: bool = False
    # 統合RANGEフィルタのブロック閾値（合計スコアがこれ以上でHOLD）
    range_filter_block_threshold: float = 0.6
    # --- BCA: Bidirectional Conviction Assessment ---
    # BCA有効化フラグ
    bca_enabled: bool = True
    # 最小方向性エッジ閾値（これ未満でブロック）
    bca_min_edge: float = 0.60
    # ペナルティスケール係数
    bca_penalty_scale: float = 1.0
    # --- 構造的改善設定 ---
    # セッション切替待機フィルター有効化
    session_transition_wait_enabled: bool = False
    # セッション切替後の待機時間（分）
    session_transition_wait_minutes: int = 30
    # 流動性ゾーン連動TP有効化
    liquidity_based_tp_enabled: bool = False
    # 流動性ゾーン手前のマージン（%）
    liquidity_tp_margin_pct: float = 0.01
    # デフォルトリスクリワード比（流動性TPフォールバック用）
    liquidity_tp_default_rr: float = 1.5
    # 実スプレッドデータ使用（CSVの<SPREAD>列）
    use_actual_spread_data: bool = False
    # --- 改善検証パラメータ ---
    # off_hours時間帯でTRENDエントリーを完全ブロック
    off_hours_trend_block: bool = False
    # off_hours + htf_alignment 複合ブロック
    off_hours_high_align_block: bool = False
    off_hours_high_align_threshold: float = 0.55
    # TREND時のsl_min_pips上書き（None=sl_min_pipsを使用）
    trend_sl_min_pips: float | None = None
    # TREND時のSL上限キャップ（None=無制限）
    trend_sl_max_pips: float | None = None
    # 高alignment時スコアペナルティ閾値（None=無効）
    high_align_penalty_threshold: float | None = None
    # 高alignment時のスコアペナルティ量
    high_align_penalty_score: float = 1.0
    # ADXエントリー上限（None=無効, 例: 35.0で追いかけ防止）
    adx_upper_limit: float | None = None
    # TREND時整合TF上限（None=無効, 例: 6でTF過多ブロック）
    trend_max_aligned_tfs: int | None = None
    # EMAクロス矛盾ペナルティ上書き（None=デフォルト-2.5）
    ema_cross_penalty: float | None = None
    # --- M1マイクロ反転フィルタ ---
    # M1マイクロ反転フィルタ有効化
    m1_micro_reversal_enabled: bool = False
    # BB %B極値閾値（BUY: >この値, SELL: <1-この値）
    m1_micro_reversal_bb_extreme: float = 0.90
    # Stochastic K極値閾値（BUY: >この値, SELL: <100-この値）
    m1_micro_reversal_stoch_extreme: float = 80.0
    # ROC/ATR比の極値閾値
    m1_micro_reversal_roc_atr_extreme: float = 1.5
    # ROC計算に使うM1足本数
    m1_micro_reversal_roc_lookback: int = 5
    # 発動に必要な最小シグナル数（2=2/3合議）
    m1_micro_reversal_min_signals: int = 2
    # --- M1構造的SL ---
    # M1スイングレベルベースSL有効化
    m1_structure_sl_enabled: bool = False
    # SLバッファ（pips）
    m1_structure_sl_buffer_pips: float = 3.0
    # 構造的SL最小値（pips）
    m1_structure_sl_min_pips: float = 15.0
    # 構造的SL最大値（pips）
    m1_structure_sl_max_pips: float = 60.0
    # スイングウィンドウ（ルックバック本数）
    m1_structure_sl_swing_window: int = 8
    # --- M1実行ゲート ---
    # M1実行ゲート有効化
    m1_exec_gate_enabled: bool = False
    # EMAアラインメント重み
    m1_exec_gate_ema_weight: float = 1.0
    # バーモメンタム重み
    m1_exec_gate_bar_weight: float = 0.5
    # BB健全ゾーン重み
    m1_exec_gate_bb_weight: float = 0.5
    # BB健全ゾーン下限
    m1_exec_gate_bb_low: float = 0.3
    # BB健全ゾーン上限
    m1_exec_gate_bb_high: float = 0.7
    # 通過に必要な最小スコア
    m1_exec_gate_threshold: float = 1.0
    # --- pip単位 ---
    # 1pipの価格単位（JPY系=0.01、非JPY系=0.0001）
    pip_unit: float = 0.01
    # クォート通貨→口座通貨(JPY)変換レート
    quote_ccy_rate: float = 1.0
    # 通貨ペアのスプレッド（pips、プリセットから注入）
    spread_pips: float = 1.5
    # --- レジーム別動的TP比率 ---
    # 動的TP有効化（Falseで従来の固定tp_sl_ratio）
    dynamic_tp_enabled: bool = False
    # TREND時のTP倍率（利益を伸ばす）
    dynamic_tp_trend: float = 1.5
    # RANGE時のTP倍率（早めの利確）
    dynamic_tp_range: float = 0.85
    # HIGH_VOL時のTP倍率
    dynamic_tp_high_vol: float = 1.1
    # LOW_VOL/デフォルト時のTP倍率
    dynamic_tp_low_vol: float = 1.0
    # --- M1リトレースエントリー ---
    # リトレースエントリー有効化
    m1_retrace_entry_enabled: bool = False
    # リトレースATR係数（M1 ATR × factor分の押し/戻りを待つ）
    m1_retrace_atr_factor: float = 0.5
    # リトレース待機最大M1足数
    m1_retrace_max_wait_bars: int = 5
    # タイムアウト時にフォールバックエントリーするか
    m1_retrace_fallback_entry: bool = True
    # --- ボリュームフィルタ ---
    # ボリュームフィルタ有効化
    volume_filter_enabled: bool = True
    # ボリュームMA比率の閾値（これ未満でペナルティ）
    volume_filter_threshold: float = 1.5
    # ボリュームフィルタ最大ペナルティ
    volume_filter_penalty: float = 0.8
    # --- ペア別スプレッド閾値 ---
    # SoftGuardスプレッド閾値（ペア別、None=SoftGuardConfigデフォルト2.0）
    sg_spread_threshold_pips: float | None = None
    # --- マクロレジームフィルタ ---
    # マクロレジームフィルタ有効化
    macro_regime_enabled: bool = False
    # VIX ELEVATED閾値
    macro_regime_vix_elevated: float = 20.0
    # VIX HIGH_FEAR閾値
    macro_regime_vix_high_fear: float = 30.0
    # VIX EXTREME_FEAR閾値（HardGuardブロック）
    macro_regime_vix_extreme_fear: float = 40.0
    # ELEVATED時ペナルティ
    macro_regime_elevated_penalty: float = 0.1
    # HIGH_FEAR時ペナルティ
    macro_regime_high_fear_penalty: float = 0.3
    # --- エッジ検定 ---
    # エッジ検定有効化（モニタリング専用、ログ出力のみ）
    edge_validator_enabled: bool = True
    # CRITICAL時にサーキットブレーカーを自動発動するか（デフォルトOFF）
    edge_validator_auto_cb: bool = False
    # ローリングウィンドウサイズ
    edge_validator_window: int = 100
    # 期待勝率（単体ペア基準: 65-78%の範囲）
    edge_validator_expected_wr: float = 0.65
    # --- スプレッドコスト比フィルタ ---
    # スプレッドコスト比フィルタ有効化
    spread_cost_ratio_enabled: bool = False
    # コスト比率ペナルティ閾値（超過でペナルティ加算）
    spread_cost_ratio_max: float = 0.15
    # コスト比率ブロック閾値（超過で完全ブロック）
    spread_cost_ratio_block: float = 0.25
    # --- SL約定時スプレッド考慮 ---
    # SL決済時のスプレッド不利約定有効化
    sl_exit_spread_enabled: bool = True
    # SL決済時のスプレッド適用係数（0.5=半額適用）
    sl_exit_spread_factor: float = 0.5
    # --- スプレッド分布モデル ---
    # スプレッド分布モデル有効化（バックテスト用）
    spread_model_enabled: bool = False
    # --- M1モメンタム確認ゲート ---
    # M1モメンタム確認ゲート有効化
    m1_momentum_gate_enabled: bool = False
    # 最大待機M1足数
    m1_momentum_max_wait: int = 5
    # 必要連続モメンタム足数
    m1_momentum_required: int = 2
    # --- スコア比例サイジング ---
    # スコア比例サイジング有効化（デフォルトOFF）
    score_proportional_sizing: bool = False
    # 閾値ちょうどでのロット倍率下限
    score_sizing_floor: float = 0.6
    # 閾値からこのpt超過で1.0x到達
    score_sizing_full_range: float = 3.0
    # --- ATR連続サイジング ---
    # ATR連続サイジング有効化（デフォルトOFF）
    atr_sizing_enabled: bool = False
    # ATR/ATR_MA比率がこの倍率以上で縮小開始
    atr_sizing_threshold: float = 1.5
    # 最大縮小率（0.5=最大50%縮小）
    atr_sizing_max_reduction: float = 0.5
    # --- MACDスロープスコアリング ---
    # ATR比デッドゾーン（0.0=無効、0.01=ATR1%未満の変化を無視）
    macd_slope_deadzone_atr_ratio: float = 0.0
    # MACDスロープ順方向ボーナス
    macd_slope_bonus: float = 2.5
    # MACDスロープ逆方向ペナルティ（正の値、内部で減算）
    macd_slope_penalty: float = 2.0
    # --- HTF整合スコアリング ---
    # HTF強整合ボーナス（2TF以上一致）
    htf_align_bonus_strong: float = 4.0
    # HTF弱整合ボーナス（1TF一致）
    htf_align_bonus_weak: float = 2.0

    def get_evaluator_config(self, timeframe: str) -> EvaluatorConfig:
        """時間足別評価器設定を取得

        Args:
            timeframe: 時間足

        Returns:
            EvaluatorConfig: 評価器設定
        """
        # ボットレベルの共通パラメータ
        common = dict(
            pip_unit=self.pip_unit,
            sl_min_pips=self.sl_min_pips,
            sl_max_pips=self.sl_max_pips_default,
            macd_slope_deadzone_atr_ratio=(
                self.macd_slope_deadzone_atr_ratio
            ),
            macd_slope_bonus=self.macd_slope_bonus,
            macd_slope_penalty=self.macd_slope_penalty,
            htf_align_bonus_strong=self.htf_align_bonus_strong,
            htf_align_bonus_weak=self.htf_align_bonus_weak,
        )
        if timeframe in self.evaluator_configs:
            cfg = self.evaluator_configs[timeframe]
            return EvaluatorConfig(
                timeframe=cfg.timeframe,
                strength_config=cfg.strength_config,
                min_score=cfg.min_score,
                atr_sl_multiplier=cfg.atr_sl_multiplier,
                atr_tp_multiplier=cfg.atr_tp_multiplier,
                ema_cross_penalty=cfg.ema_cross_penalty,
                **common,
            )
        return EvaluatorConfig(
            timeframe=timeframe,
            **common,
        )

    def to_signal_config(self) -> SignalConfig:
        """シグナル関連設定を抽出

        Returns:
            SignalConfig: シグナル設定
        """
        return SignalConfig(
            consensus_threshold=self.consensus_threshold,
            consensus_primary_weight=self.consensus_primary_weight,
            consensus_entry_weight=self.consensus_entry_weight,
            consensus_confirm_weight=self.consensus_confirm_weight,
            consensus_manage_weight=self.consensus_manage_weight,
            consensus_other_weight=self.consensus_other_weight,
            bca_enabled=self.bca_enabled,
            bca_min_edge=self.bca_min_edge,
            bca_penalty_scale=self.bca_penalty_scale,
            htf_score_filter_enabled=self.htf_score_filter_enabled,
            htf_score_filter_min_alignment=(
                self.htf_score_filter_min_alignment
            ),
            htf_score_filter_threshold_add=(
                self.htf_score_filter_threshold_add
            ),
            regime_detection_tf=self.regime_detection_tf,
            htf_alignment_tfs=list(self.htf_alignment_tfs),
            macd_slope_filter_threshold=(self.macd_slope_filter_threshold),
            trend_strength_max=self.trend_strength_max,
        )

    def to_risk_management_config(self) -> RiskManagementConfig:
        """リスク管理設定を抽出

        Returns:
            RiskManagementConfig: リスク管理設定
        """
        return RiskManagementConfig(
            sl_min_pips=self.sl_min_pips,
            sl_max_pips_default=self.sl_max_pips_default,
            trend_sl_min_pips=self.trend_sl_min_pips,
            trend_sl_max_pips=self.trend_sl_max_pips,
            penalty_cap=self.penalty_cap,
            default_tp_sl_ratio_range=(self.default_tp_sl_ratio_range),
            max_positions=self.max_positions,
            bonus_max_positions=self.bonus_max_positions,
            bonus_score_threshold=self.bonus_score_threshold,
            max_lot_per_trade=self.max_lot_per_trade,
            max_total_exposure_lot=self.max_total_exposure_lot,
            base_risk_pct=self.base_risk_pct,
            max_risk_pct_absolute=self.max_risk_pct_absolute,
            equity_floor_pct=self.equity_floor_pct,
            equity_caution_pct=self.equity_caution_pct,
            slippage_buffer_pips=self.slippage_buffer_pips,
            use_dynamic_lot=self.use_dynamic_lot,
            enable_position_sizing=self.enable_position_sizing,
            use_position_manager=self.use_position_manager,
        )

    def to_filter_config(self) -> FilterConfig:
        """フィルター設定を抽出

        Returns:
            FilterConfig: フィルター設定
        """
        return FilterConfig(
            range_day_bbw_threshold=self.range_day_bbw_threshold,
            range_day_score_premium=self.range_day_score_premium,
            weak_hours_enabled=self.weak_hours_enabled,
            weak_hours_score_premium=self.weak_hours_score_premium,
            sg_spread_penalty_rate=self.sg_spread_penalty_rate,
            sg_off_hours_penalty=self.sg_off_hours_penalty,
            sg_volatility_penalty=self.sg_volatility_penalty,
            sg_recent_loss_penalty=self.sg_recent_loss_penalty,
            regime_threshold_enabled=self.regime_threshold_enabled,
            regime_trend_threshold_add=(self.regime_trend_threshold_add),
            fundamental_caution_block_level=(
                self.fundamental_caution_block_level
            ),
            fundamental_holiday_liquidity_block=(
                self.fundamental_holiday_liquidity_block
            ),
            fundamental_decay_coefficient=(self.fundamental_decay_coefficient),
            fundamental_assessor_enabled=(self.fundamental_assessor_enabled),
            fundamental_softguard_enabled=(self.fundamental_softguard_enabled),
            fundamental_pm_enabled=self.fundamental_pm_enabled,
            fundamental_post_event_lag_seconds=(
                self.fundamental_post_event_lag_seconds
            ),
            range_filter_consolidated=(self.range_filter_consolidated),
            range_filter_block_threshold=(self.range_filter_block_threshold),
            session_transition_wait_enabled=(
                self.session_transition_wait_enabled
            ),
            session_transition_wait_minutes=(
                self.session_transition_wait_minutes
            ),
            liquidity_based_tp_enabled=(self.liquidity_based_tp_enabled),
            liquidity_tp_margin_pct=self.liquidity_tp_margin_pct,
            liquidity_tp_default_rr=self.liquidity_tp_default_rr,
            use_actual_spread_data=self.use_actual_spread_data,
            off_hours_trend_block=self.off_hours_trend_block,
            off_hours_high_align_block=(self.off_hours_high_align_block),
            off_hours_high_align_threshold=(
                self.off_hours_high_align_threshold
            ),
            high_align_penalty_threshold=(self.high_align_penalty_threshold),
            high_align_penalty_score=self.high_align_penalty_score,
            volume_filter_enabled=self.volume_filter_enabled,
            volume_filter_threshold=self.volume_filter_threshold,
            volume_filter_penalty=self.volume_filter_penalty,
            sg_spread_threshold_pips=self.sg_spread_threshold_pips,
            macro_regime_enabled=self.macro_regime_enabled,
            macro_regime_vix_elevated=self.macro_regime_vix_elevated,
            macro_regime_vix_high_fear=self.macro_regime_vix_high_fear,
            macro_regime_vix_extreme_fear=self.macro_regime_vix_extreme_fear,
            macro_regime_elevated_penalty=self.macro_regime_elevated_penalty,
            macro_regime_high_fear_penalty=self.macro_regime_high_fear_penalty,
        )

    @classmethod
    def from_sub_configs(
        cls,
        signal: SignalConfig | None = None,
        risk_mgmt: RiskManagementConfig | None = None,
        filter_cfg: FilterConfig | None = None,
        **kwargs: object,
    ) -> UnifiedBotConfig:
        """サブConfigからUnifiedBotConfigを構築

        Args:
            signal: シグナル設定
            risk_mgmt: リスク管理設定
            filter_cfg: フィルター設定
            **kwargs: その他フィールド上書き

        Returns:
            UnifiedBotConfig: 統合設定
        """
        merged: dict[str, object] = {}
        if signal is not None:
            for f in signal.__dataclass_fields__:
                merged[f] = getattr(signal, f)
        if risk_mgmt is not None:
            for f in risk_mgmt.__dataclass_fields__:
                merged[f] = getattr(risk_mgmt, f)
        if filter_cfg is not None:
            for f in filter_cfg.__dataclass_fields__:
                merged[f] = getattr(filter_cfg, f)
        merged.update(kwargs)
        return cls(**merged)  # type: ignore[arg-type]
