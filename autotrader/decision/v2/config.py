"""V2 Trading Engine 設定モジュール。

市場構造状態マシンに基づくトレードエンジンの全設定を定義。
各コンポーネント（レジーム分類器、戦略、リスク管理）の
パラメータを frozen dataclass で管理する。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RegimeClassifierConfig:
    """レジーム分類器の設定。

    ヒステリシス付き状態マシンの遷移閾値と
    確認足数を制御する。
    """

    # --- ADX閾値 ---
    trending_adx_threshold: float = 25.0
    ranging_adx_threshold: float = 20.0

    # --- MA alignment閾値 ---
    trending_ma_align_threshold: float = 0.3

    # --- 正規化ATR閾値 ---
    volatile_atr_threshold: float = 1.8
    quiet_atr_upper: float = 0.7
    ranging_atr_lower: float = 0.7
    ranging_atr_upper: float = 1.3

    # --- ヒステリシス（遷移確認足数） ---
    # QUIET → 他
    quiet_to_trending_bars: int = 3
    quiet_to_ranging_bars: int = 2
    quiet_to_volatile_bars: int = 1
    # TRENDING → 他
    trending_to_ranging_bars: int = 4
    trending_to_volatile_bars: int = 2
    trending_to_quiet_bars: int = 3
    # RANGING → 他
    ranging_to_trending_bars: int = 3
    ranging_to_volatile_bars: int = 1
    ranging_to_quiet_bars: int = 2
    # VOLATILE → 他（安全復帰は慎重）
    volatile_to_trending_bars: int = 5
    volatile_to_ranging_bars: int = 4
    volatile_to_quiet_bars: int = 3


@dataclass(frozen=True)
class TrendFollowConfig:
    """トレンドフォロー戦略の設定。

    H4構造がBULLISH/BEARISHの時にH1プルバックで
    エントリーする戦略のパラメータ。
    """

    # プルバック検出
    ema_period: int = 50
    pullback_atr_distance: float = 1.5

    # BOS鮮度
    bos_max_bars: int = 10

    # SL/TP
    sl_atr_buffer: float = 0.3
    sl_max_pips: float = 50.0
    tp_min_rr: float = 1.5
    tp_default_rr: float = 2.0

    # 確信度重み
    weight_structure_align: float = 0.30
    weight_bos_freshness: float = 0.20
    weight_reversal_quality: float = 0.25
    weight_momentum: float = 0.25


@dataclass(frozen=True)
class RangeRevertConfig:
    """レンジ逆張り戦略の設定。

    RANGING レジームでBB極値+サポレジ到達時に
    逆張りエントリーする戦略のパラメータ。
    """

    # BB位置閾値
    bb_buy_threshold: float = 0.15
    bb_sell_threshold: float = 0.85

    # SL/TP
    sl_atr_buffer: float = 0.5
    sl_max_pips: float = 30.0
    tp_range_pct: float = 0.70
    tp_min_rr: float = 1.2

    # 確信度重み
    weight_liquidity_grab: float = 0.30
    weight_reversal_quality: float = 0.25
    weight_rsi_divergence: float = 0.25
    weight_bb_extreme: float = 0.20


@dataclass(frozen=True)
class BreakoutConfig:
    """ブレイクアウト戦略の設定。

    QUIET レジームからのBBスクイーズ解消+
    レンジブレイクでエントリーする戦略のパラメータ。
    """

    # スクイーズ条件
    min_quiet_bars: int = 5
    squeeze_threshold: float = 0.7
    adx_breakout_threshold: float = 20.0

    # SL/TP
    sl_atr_buffer: float = 0.3
    tp_range_multiplier: float = 1.5
    tp_min_rr: float = 1.5

    # 確信度重み
    weight_bos_confirm: float = 0.30
    weight_adx_strength: float = 0.25
    weight_bb_expansion: float = 0.25
    weight_d1_alignment: float = 0.20


@dataclass(frozen=True)
class V2RiskConfig:
    """V2リスク管理の設定。

    NoTrade条件、トレーリングストップ、
    ポジションサイジングを制御する。
    """

    # NoTrade条件
    max_consecutive_losses: int = 3
    max_spread_pips: float = 3.0
    blocked_hours_utc: tuple[int, ...] = (22, 23, 0, 1, 2, 3)

    # トレーリングストップ
    trailing_start_rr: float = 1.5
    trailing_atr_multiplier: float = 1.0
    breakeven_at_rr: float = 1.0

    # ポジションサイジング
    base_risk_pct: float = 0.02
    max_risk_pct: float = 0.04
    confidence_scale: bool = True


@dataclass(frozen=True)
class V2BotConfig:
    """V2トレードボットのメイン設定。

    エントリー/構造/コンテキスト時間足の指定と
    各サブコンポーネントの設定を集約する。

    Attributes:
        entry_timeframe: エントリー判定の時間足。
        structure_timeframe: 市場構造分析の時間足。
        context_timeframe: 上位コンテキストの時間足。
        min_confidence: エントリーに必要な最低確信度。
        pip_unit: 1pipの価格単位(USDJPY=0.01)。
        pip_value: 1pip/lotあたりの損益(JPY)。
    """

    # 使用時間足
    entry_timeframe: str = "H1"
    structure_timeframe: str = "H4"
    context_timeframe: str = "D1"

    # 最低確信度
    min_confidence: float = 0.50

    # 通貨ペア固有
    pip_unit: float = 0.01
    pip_value: float = 100.0

    # サブ設定
    regime: RegimeClassifierConfig = field(
        default_factory=RegimeClassifierConfig,
    )
    trend_follow: TrendFollowConfig = field(
        default_factory=TrendFollowConfig,
    )
    range_revert: RangeRevertConfig = field(
        default_factory=RangeRevertConfig,
    )
    breakout: BreakoutConfig = field(
        default_factory=BreakoutConfig,
    )
    risk: V2RiskConfig = field(
        default_factory=V2RiskConfig,
    )
