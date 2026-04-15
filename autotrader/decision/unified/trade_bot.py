"""統合トレードボット

新アーキテクチャ対応版:
- MarketRegimeDetector: レジーム検出
- TradingPlan: UNIVERSALモードプラン
- ModeAwareScoreConsensus: コンセンサス統合
- PositionSizer: ロット計算
- PositionManager: ポジション管理
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

import pandas as pd

from autotrader.constraint.filters.m1_execution_gate import (
    M1ExecutionGate,
    M1ExecutionGateConfig,
)
from autotrader.constraint.filters.session_transition_filter import (
    SessionTransitionFilter,
)
from autotrader.constraint.soft_guard import (
    SoftGuard,
    SoftGuardConfig,
    SoftGuardResult,
)
from autotrader.core.enums import MarketRegime, SignalType
from autotrader.core.interfaces.position_sizing import SizingContext

from .adaptive import (
    AdaptiveOverrides,
    AdaptiveParameterTuner,
    TradeRecord,
    TunerConfig,
)
from .config import RiskConfig, UnifiedBotConfig
from .mode_selector import TradingPlan
from .pipeline_pkg.directional_edge import DirectionalEdgeAssessor
from .risk.position_sizer import PositionSizer, PositionSizerConfig
from .scoring.consensus import (
    ConsensusConfig,
    ConsensusResult,
    ModeAwareScoreConsensus,
)
from .scoring.consolidator import ConsolidatedSignal
from .scoring.timeframe_evaluator import TimeframeEvaluator, TimeframeSignal
from .timeframe_router import TimeframeRouter

if TYPE_CHECKING:
    from autotrader.adapters.fundamental.schemas import (
        FundamentalContext,
        FundamentalMemorySnapshot,
    )
    from autotrader.calculator.features.regime_detector import (
        RegimeResult,
    )
    from autotrader.core.entities import Candle
    from autotrader.decision.unified.fundamental_assessor import (
        FundamentalAssessment,
    )


@dataclass
class PendingEntry:
    """リトレースエントリー保留データ."""

    direction: SignalType
    target_price: float
    original_close: float
    bars_waited: int = 0
    max_wait_bars: int = 5
    fallback_entry: bool = True
    # 保留シグナル構築に必要なコンテキスト
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    confidence: float = 0.0
    primary_tf: str = ""
    rationale: str = ""
    consensus: object | None = None
    regime_result: object | None = None


@dataclass
class PendingMomentumEntry:
    """M1モメンタム確認待機エントリー.

    コンセンサス通過後、M1の直近足が
    トレード方向にモメンタムを持つことを確認する。

    Attributes:
        direction: シグナル方向
        bars_waited: 待機済みM1足数
        max_wait_bars: 最大待機足数
        momentum_required: 必要連続モメンタム足数
        momentum_count: 現在の連続カウント
        sl_pips: SL(pips)
        tp_pips: TP(pips)
        confidence: 確度
        consensus_score: コンセンサススコア
        lot: ロットサイズ
        primary_tf: 主要時間足
        rationale: 判断理由
        regime: レジーム文字列
        mode: モード文字列
        entry_threshold: エントリー閾値
        htf_alignment: HTF整合スコア
        penalty_total: ペナルティ合計
        penalty_breakdown: ペナルティ内訳
        trend_strength: トレンド強度
        strategy_id: 戦略ID
        tf_score_breakdowns: TF別スコア内訳
        buy_score: BUYスコア
        sell_score: SELLスコア
        aligned_tfs: 整合TFリスト
        scores: TF別スコア
        tf_directions: TF別方向
    """

    direction: SignalType
    bars_waited: int = 0
    max_wait_bars: int = 5
    momentum_required: int = 2
    momentum_count: int = 0
    # シグナル情報の保存
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    confidence: float = 0.0
    consensus_score: float = 0.0
    lot: float = 0.0
    primary_tf: str = ""
    rationale: str = ""
    regime: str = ""
    mode: str = ""
    consensus: object | None = None
    regime_result: object | None = None
    entry_threshold: float = 0.0
    htf_alignment: float = 0.0
    penalty_total: float = 0.0
    penalty_breakdown: dict = field(default_factory=dict)
    trend_strength: float = 0.0
    strategy_id: str = ""
    tf_score_breakdowns: dict = field(
        default_factory=dict,
    )
    buy_score: float = 0.0
    sell_score: float = 0.0
    aligned_tfs: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    tf_directions: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BotState:
    """ボット状態（イミュータブル）

    frozen=True により全フィールドが読み取り専用。
    状態変更は with_xxx メソッドで新インスタンスを返す。

    Attributes:
        equity: 現在の有効証拠金
        initial_equity: 初期資金
        consecutive_losses: 連敗数
        consecutive_wins: 連勝数
        current_dd_pct: 現在のドローダウン率
        peak_equity: 最高証拠金
        daily_pnl: 日次損益
        daily_trades: 日次トレード数
        open_exposure_lot: 未決済エクスポージャー
        open_same_direction_lot: 同方向最大ロット
    """

    equity: float = 1_000_000.0
    initial_equity: float = 1_000_000.0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    current_dd_pct: float = 0.0
    peak_equity: float = 1_000_000.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    open_exposure_lot: float = 0.0
    open_same_direction_lot: float = 0.0
    open_buy_count: int = 0
    open_sell_count: int = 0

    def with_pnl(self, pnl: float) -> BotState:
        """PnL適用後の新しいBotStateを返す

        Args:
            pnl: 損益

        Returns:
            BotState: 更新後の新インスタンス
        """
        new_equity = self.equity + pnl
        new_daily_pnl = self.daily_pnl + pnl

        if pnl > 0:
            new_wins = self.consecutive_wins + 1
            new_losses = 0
        elif pnl < 0:
            new_losses = self.consecutive_losses + 1
            new_wins = 0
        else:
            new_wins = self.consecutive_wins
            new_losses = self.consecutive_losses

        new_peak = max(self.peak_equity, new_equity)
        new_dd = (new_peak - new_equity) / new_peak if new_peak > 0 else 0.0

        return replace(
            self,
            equity=new_equity,
            daily_pnl=new_daily_pnl,
            consecutive_wins=new_wins,
            consecutive_losses=new_losses,
            peak_equity=new_peak,
            current_dd_pct=new_dd,
        )

    def with_daily_reset(self) -> BotState:
        """日次リセット後の新しいBotStateを返す

        Returns:
            BotState: リセット後の新インスタンス
        """
        return replace(self, daily_pnl=0.0, daily_trades=0)

    def with_exposure(
        self,
        open_exposure_lot: float,
        open_same_direction_lot: float,
        open_buy_count: int = 0,
        open_sell_count: int = 0,
    ) -> BotState:
        """エクスポージャー更新後の新しいBotStateを返す

        Args:
            open_exposure_lot: 未決済エクスポージャー
            open_same_direction_lot: 同方向最大ロット
            open_buy_count: BUYポジション数
            open_sell_count: SELLポジション数

        Returns:
            BotState: 更新後の新インスタンス
        """
        return replace(
            self,
            open_exposure_lot=open_exposure_lot,
            open_same_direction_lot=open_same_direction_lot,
            open_buy_count=open_buy_count,
            open_sell_count=open_sell_count,
        )

    def with_initial_equity(
        self,
        initial_balance: float,
    ) -> BotState:
        """初期資金設定後の新しいBotStateを返す

        Args:
            initial_balance: 初期資金

        Returns:
            BotState: 更新後の新インスタンス
        """
        return replace(
            self,
            equity=initial_balance,
            initial_equity=initial_balance,
            peak_equity=initial_balance,
        )


class RiskManager:
    """リスク管理器

    日次損失制限、クールダウン管理、多層防御を行う。
    """

    def __init__(self, config: RiskConfig | None = None):
        """初期化

        Args:
            config: リスク管理設定
        """
        self.config = config or RiskConfig()
        self._daily_pnl: float = 0.0
        self._last_trade_time: datetime | None = None
        self._daily_trades: int = 0
        self._current_date: date | None = None
        # Layer 4: サーキットブレーカー
        self._circuit_breaker_until: datetime | None = None
        # Layer 5: 急速DD検知用エクイティ履歴
        self._equity_history: list[tuple[datetime, float]] = []
        self._rapid_dd_pause_until: datetime | None = None

    def reset_daily(self, date: datetime) -> None:
        """日次リセット

        Args:
            date: リセット日
        """
        if self._current_date != date.date():
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._current_date = date.date()

    def update_pnl(self, pnl: float) -> None:
        """損益更新

        Args:
            pnl: 損益
        """
        self._daily_pnl += pnl

    def record_trade(self, timestamp: datetime) -> None:
        """取引記録

        Args:
            timestamp: 取引時刻
        """
        self._last_trade_time = timestamp
        self._daily_trades += 1

    def record_equity(
        self,
        timestamp: datetime,
        equity: float,
    ) -> None:
        """エクイティ記録（Layer 5用）

        Args:
            timestamp: 記録時刻
            equity: エクイティ
        """
        self._equity_history.append((timestamp, equity))
        # ウィンドウ外のデータを削除
        cutoff = timestamp - timedelta(
            minutes=self.config.rapid_dd_window_minutes,
        )
        self._equity_history = [
            (t, e) for t, e in self._equity_history if t >= cutoff
        ]

    def trigger_circuit_breaker(
        self,
        timestamp: datetime,
    ) -> None:
        """サーキットブレーカー発動（Layer 4）

        Args:
            timestamp: 発動時刻
        """
        self._circuit_breaker_until = timestamp + timedelta(
            minutes=(self.config.circuit_breaker_pause_minutes),
        )

    def check_rapid_dd(
        self,
        timestamp: datetime,
        current_equity: float,
    ) -> bool:
        """急速DD検知（Layer 5）

        Args:
            timestamp: 現在時刻
            current_equity: 現在のエクイティ

        Returns:
            bool: 急速DD検知でパウズすべきならTrue
        """
        if not self.config.rapid_dd_pause_enabled:
            return False
        if not self._equity_history:
            return False
        # ウィンドウ内の最高エクイティを取得
        window_peak = max(e for _, e in self._equity_history)
        if window_peak <= 0:
            return False
        dd_pct = (window_peak - current_equity) / window_peak
        if dd_pct >= self.config.rapid_dd_threshold_pct:
            self._rapid_dd_pause_until = timestamp + timedelta(
                minutes=(self.config.rapid_dd_pause_duration_minutes),
            )
            return True
        return False

    def can_trade(
        self,
        timestamp: datetime,
        open_position_count: int = 0,
    ) -> tuple[bool, str]:
        """取引可否チェック

        Args:
            timestamp: 現在時刻
            open_position_count: 現在の保有ポジション数

        Returns:
            tuple[bool, str]: (取引可否, 理由)
        """
        # Layer 4: サーキットブレーカーチェック
        if (
            self.config.circuit_breaker_enabled
            and self._circuit_breaker_until is not None
            and timestamp < self._circuit_breaker_until
        ):
            remaining = int(
                (self._circuit_breaker_until - timestamp).total_seconds() // 60
            )
            return (
                False,
                f"サーキットブレーカー発動中(残{remaining}分)",
            )

        # Layer 5: 急速DD検知パウズ
        if (
            self.config.rapid_dd_pause_enabled
            and self._rapid_dd_pause_until is not None
            and timestamp < self._rapid_dd_pause_until
        ):
            remaining = int(
                (self._rapid_dd_pause_until - timestamp).total_seconds() // 60
            )
            return (
                False,
                f"急速DD検知パウズ中(残{remaining}分)",
            )

        # 日次損失制限チェック
        if self._daily_pnl < -self.config.max_daily_loss_pct:
            return (
                False,
                f"日次損失制限超過({self._daily_pnl:.2%})",
            )

        # 日次トレード件数制限チェック（0=無制限）
        limit = self.config.max_daily_trades
        if limit > 0 and self._daily_trades >= limit:
            return (
                False,
                f"日次トレード件数上限({self._daily_trades}/{limit}件)",
            )

        # Layer 1: 動的クールダウン
        if self._last_trade_time is not None:
            if self.config.dynamic_cooldown_enabled:
                cooldown_min = min(
                    (
                        self.config.dynamic_cooldown_base_minutes
                        + (
                            open_position_count
                            * self.config.dynamic_cooldown_per_position_minutes
                        )
                    ),
                    self.config.dynamic_cooldown_max_minutes,
                )
            else:
                cooldown_min = self.config.cooldown_minutes
            cooldown = timedelta(minutes=cooldown_min)
            if timestamp - self._last_trade_time < cooldown:
                remaining = int(
                    (
                        self._last_trade_time + cooldown - timestamp
                    ).total_seconds()
                    // 60
                )
                return (
                    False,
                    f"クールダウン中(残{remaining}分,"
                    f" pos={open_position_count})",
                )

        return True, ""


class UnifiedTradeBot:
    """統合トレードボット

    全時間足を毎分評価し、最適なエントリーを選択する。
    新アーキテクチャではレジーム検出、モード選択、
    ポジションサイジングを統合。
    """

    # RANGEフィルタ用定数
    _LOW_VOL_SCORE_MARGIN: float = 1.5
    _WEAK_TREND_THRESHOLD: float = 0.3

    def __init__(
        self,
        config: UnifiedBotConfig | None = None,
        adaptive_config: TunerConfig | None = None,
    ):
        """初期化

        Args:
            config: ボット設定
            adaptive_config: アダプティブ調整設定（Noneで無効）
        """
        self.config = config or UnifiedBotConfig()
        self.timeframes = self.config.effective_timeframes

        # 時間足別評価器
        self.evaluators: dict[str, TimeframeEvaluator] = {}
        for tf in self.timeframes:
            _eval_cfg = self.config.get_evaluator_config(tf)
            if self.config.ema_cross_penalty is not None:
                _eval_cfg = dataclasses.replace(
                    _eval_cfg,
                    ema_cross_penalty=(self.config.ema_cross_penalty),
                )
            self.evaluators[tf] = TimeframeEvaluator(tf, _eval_cfg)

        # 新アーキテクチャコンポーネント（risk_manager を含む）
        self._init_new_components()

        # ボット状態
        self.state = BotState()

        # 市場データ
        self._market_data: dict[str, pd.DataFrame] = {}
        self._current_indices: dict[str, int] = {}

        # フロー分析（オプション）
        self._flow_analyzer: Any = None

        # アダプティブパラメータ調整（オプション）
        self._adaptive_tuner: AdaptiveParameterTuner | None = (
            AdaptiveParameterTuner(adaptive_config)
            if adaptive_config is not None
            else None
        )

        # エッジ検定器
        from autotrader.decision.unified.adaptive.edge_validator import (
            EdgeValidator,
            EdgeValidatorConfig,
        )

        self._edge_validator: EdgeValidator | None = None
        # エッジ劣化時のロット縮小係数（1.0=通常）
        self._edge_lot_multiplier: float = 1.0
        if self.config.edge_validator_enabled:
            self._edge_validator = EdgeValidator(
                EdgeValidatorConfig(
                    window_size=self.config.edge_validator_window,
                    short_window_size=(
                        self.config.edge_validator_short_window
                    ),
                    expected_winrate=(
                        self.config.edge_validator_expected_wr
                    ),
                ),
            )

        # レジーム遷移追跡
        self._prev_regime: str = ""
        self._regime_transition_bars: int = 0

        # クロスペア方向データ（マルチペアBTから注入）
        self._cross_pair_directions: dict[str, str] = {}

        # リアルタイムスプレッド（BT: CSV実データ、ライブ: MT5取得値）
        self._current_spread_pips: float | None = None

        # Phase 2b: 直近のファンダメンタル評価結果
        self._last_fundamental_assessment: Any = None

        # シグナル生成パイプライン
        from .pipeline import build_default_pipeline

        self._pipeline = build_default_pipeline()

    def _init_new_components(self) -> None:
        """新アーキテクチャコンポーネントを初期化"""
        # レジーム検出器
        from autotrader.calculator.features.regime_detector import (
            MarketRegimeDetector,
            RegimeDetectorConfig,
        )

        self.regime_detector = MarketRegimeDetector(
            RegimeDetectorConfig(
                breakout_enabled=(
                    self.config.regime_breakout_enabled
                ),
                breakout_lookback=(
                    self.config.regime_breakout_lookback
                ),
                vol_expanding_threshold=(
                    self.config.vol_expanding_threshold
                ),
                vol_compressing_threshold=(
                    self.config.vol_compressing_threshold
                ),
                choppy_enabled=(
                    self.config.choppy_enabled
                ),
                choppy_ci_threshold=(
                    self.config.choppy_ci_threshold
                ),
            )
        )

        # モード選択（UNIVERSAL固定、TradingPlan直接生成）

        # タイムフレームルーター
        self.tf_router = TimeframeRouter()

        # コンセンサス統合器（config→ConsensusConfig伝搬）
        _consensus_cfg = ConsensusConfig(
            primary_weight=self.config.consensus_primary_weight,
            entry_weight=self.config.consensus_entry_weight,
            confirm_weight=self.config.consensus_confirm_weight,
            manage_weight=self.config.consensus_manage_weight,
            other_weight=self.config.consensus_other_weight,
            threshold=(
                self.config.demo_consensus_threshold
                if self.config.demo_mode
                else self.config.consensus_threshold
            ),
        )
        self.consensus = ModeAwareScoreConsensus(_consensus_cfg)

        # ポジションサイザー（資金管理パラメータを設定から注入）
        # pip_value = 100,000通貨 × pip_unit × quote_ccy_rate
        # USDJPY: 100,000 × 0.01 × 1.0 = 1,000 JPY/pip/lot
        # EURUSD: 100,000 × 0.0001 × 150.0 = 1,500 JPY/pip/lot
        _sizer_pv = (
            100_000 * self.config.pip_unit
            * self.config.quote_ccy_rate
        )
        self.position_sizer = PositionSizer(
            PositionSizerConfig(
                base_risk_pct=self.config.base_risk_pct,
                max_risk_pct_absolute=self.config.max_risk_pct_absolute,
                max_lot_per_trade=self.config.max_lot_per_trade,
                max_total_exposure_lot=self.config.max_total_exposure_lot,
                equity_floor_pct=self.config.equity_floor_pct,
                equity_caution_pct=self.config.equity_caution_pct,
                slippage_buffer_pips=self.config.slippage_buffer_pips,
                pip_value=_sizer_pv,
                score_proportional_sizing=(
                    self.config.score_proportional_sizing
                ),
                score_sizing_floor=(
                    self.config.score_sizing_floor
                ),
                score_sizing_full_range=(
                    self.config.score_sizing_full_range
                ),
                atr_sizing_enabled=(
                    self.config.atr_sizing_enabled
                ),
                atr_sizing_threshold=(
                    self.config.atr_sizing_threshold
                ),
                atr_sizing_max_reduction=(
                    self.config.atr_sizing_max_reduction
                ),
            )
        )

        # ソフトガード（UnifiedBotConfigからペナルティ値を伝搬）
        self.soft_guard = SoftGuard(
            SoftGuardConfig(
                spread_penalty_rate=self.config.sg_spread_penalty_rate,
                off_hours_penalty=self.config.sg_off_hours_penalty,
                volatility_penalty=self.config.sg_volatility_penalty,
                recent_loss_penalty=self.config.sg_recent_loss_penalty,
                penalty_hours=self.config.sg_penalty_hours,
                penalty_hours_value=self.config.sg_penalty_hours_value,
            )
        )

        # 動的TF選択器（UNIVERSALモード用）
        from autotrader.decision.unified.dynamic_tf_selector import (
            DynamicTFSelector,
        )

        self._dynamic_tf_selector = DynamicTFSelector(
            bot_config=self.config,
        )

        # BCA: 方向性エッジ評価器（オプション）
        self._edge_assessor = None
        if self.config.bca_enabled:
            self._edge_assessor = DirectionalEdgeAssessor(
                min_edge=self.config.bca_min_edge,
                penalty_scale=self.config.bca_penalty_scale,
            )

        # リスク管理器（デモモード時はクールダウンを排除）
        risk_config = dataclasses.replace(
            self.config.risk,
            cooldown_minutes=(
                0
                if self.config.demo_mode
                else self.config.risk.cooldown_minutes
            ),
            max_daily_trades=(
                self.config.demo_max_daily_trades
                if self.config.demo_mode
                else self.config.risk.max_daily_trades
            ),
        )
        self.risk_manager = RiskManager(risk_config)

        # セッション切替待機フィルタ
        self._session_transition_filter = SessionTransitionFilter(
            wait_minutes=self.config.session_transition_wait_minutes,
            enabled=self.config.session_transition_wait_enabled,
        )

        # M1マイクロ反転フィルタ
        from autotrader.constraint.filters.micro_reversal_filter import (
            MicroReversalConfig,
            MicroReversalFilter,
        )

        self._micro_reversal_filter = MicroReversalFilter(
            MicroReversalConfig(
                enabled=self.config.m1_micro_reversal_enabled,
                bb_extreme=(self.config.m1_micro_reversal_bb_extreme),
                stoch_extreme=(self.config.m1_micro_reversal_stoch_extreme),
                roc_atr_extreme=(
                    self.config.m1_micro_reversal_roc_atr_extreme
                ),
                roc_lookback=(self.config.m1_micro_reversal_roc_lookback),
                min_signals=(self.config.m1_micro_reversal_min_signals),
            )
        )

        # M1実行ゲート
        self._m1_exec_gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=self.config.m1_exec_gate_enabled,
                ema_weight=self.config.m1_exec_gate_ema_weight,
                bar_weight=self.config.m1_exec_gate_bar_weight,
                bb_weight=self.config.m1_exec_gate_bb_weight,
                bb_low=self.config.m1_exec_gate_bb_low,
                bb_high=self.config.m1_exec_gate_bb_high,
                threshold=self.config.m1_exec_gate_threshold,
            )
        )

        # M1リトレースエントリー保留
        self._pending_entry: PendingEntry | None = None

        # M1モメンタム確認待機
        self._pending_momentum: (
            PendingMomentumEntry | None
        ) = None

        # マクロレジームフィルタ（VIXベース）
        from autotrader.calculator.features.macro_regime import (
            MacroRegimeConfig,
            MacroRegimeFilter,
        )

        self._macro_regime_filter = MacroRegimeFilter(
            MacroRegimeConfig(
                enabled=self.config.macro_regime_enabled,
                vix_elevated_threshold=(
                    self.config.macro_regime_vix_elevated
                ),
                vix_high_fear_threshold=(
                    self.config.macro_regime_vix_high_fear
                ),
                vix_extreme_fear_threshold=(
                    self.config.macro_regime_vix_extreme_fear
                ),
                elevated_penalty=(
                    self.config.macro_regime_elevated_penalty
                ),
                high_fear_penalty=(
                    self.config.macro_regime_high_fear_penalty
                ),
            )
        )

    def update_macro_regime(self, vix: float) -> None:
        """VIX値を更新してマクロレジームを判定

        Args:
            vix: VIX Close値
        """
        level = self._macro_regime_filter.update_vix(vix)
        if self.config.macro_regime_enabled:
            logger.debug(
                "VIX更新: %.1f → %s",
                vix,
                level.value,
            )

    @property
    def market_data(self) -> dict[str, pd.DataFrame]:
        """現在の市場データを返す（読み取り専用コピー）

        Returns:
            dict[str, pd.DataFrame]: 時間足別市場データ
        """
        return dict(self._market_data)

    def set_market_data(
        self,
        data: dict[str, pd.DataFrame],
    ) -> None:
        """各時間足データを設定

        Args:
            data: 時間足別データフレーム
        """
        self._market_data = data
        self._current_indices = {tf: 0 for tf in data}

        # 時刻配列を事前キャッシュ（O(1)検索用）
        import numpy as np

        self._time_arrays: dict[str, np.ndarray] = {}
        for tf, df in data.items():
            if df is None or df.empty:
                continue
            if isinstance(df.index, pd.DatetimeIndex):
                self._time_arrays[tf] = df.index.values
            elif "time" in df.columns:
                self._time_arrays[tf] = df["time"].values
            elif "timestamp" in df.columns:
                self._time_arrays[tf] = df["timestamp"].values

        # 各評価器に上位時間足データを設定
        for tf in self.timeframes:
            higher_tfs = self._get_higher_timeframes(tf)
            htf_data = {
                htf: self._market_data.get(htf, pd.DataFrame())
                for htf in higher_tfs
            }
            self.evaluators[tf].set_higher_tf_data(htf_data)

    def set_flow_analyzer(self, analyzer: Any) -> None:
        """フロー分析器を設定

        Args:
            analyzer: TradeFlowAnalyzer インスタンス
        """
        self._flow_analyzer = analyzer

    def _get_higher_timeframes(self, tf: str) -> list[str]:
        """指定時間足より長い時間足リストを取得

        Args:
            tf: 基準時間足

        Returns:
            list[str]: 上位時間足リスト
        """
        try:
            idx = self.timeframes.index(tf)
            return self.timeframes[idx + 1 :]
        except ValueError:
            return []

    def generate_signal(
        self,
        current_time: pd.Timestamp,
        candle: Candle | None = None,
        fundamental_ctx: FundamentalContext | None = None,
        fundamental_memory: FundamentalMemorySnapshot | None = None,
    ) -> ConsolidatedSignal:
        """毎分呼び出し：全時間足評価→統合シグナル生成

        Args:
            current_time: 現在時刻
            candle: 現在のローソク足
            fundamental_ctx: ファンダメンタルコンテキスト
            fundamental_memory: ファンダメンタルメモリ

        Returns:
            ConsolidatedSignal: 統合シグナル
        """
        return self._generate_signal_new(
            current_time,
            candle,
            fundamental_ctx,
            fundamental_memory,
        )

    def _assess_fundamental(
        self,
        fundamental_ctx: FundamentalContext | None,
        fundamental_memory: FundamentalMemorySnapshot | None,
    ) -> FundamentalAssessment | None:
        """ファンダメンタル評価を実行

        Args:
            fundamental_ctx: ファンダメンタルコンテキスト
            fundamental_memory: メモリスナップショット

        Returns:
            FundamentalAssessment | None: 評価結果
        """
        if (
            not self.config.fundamental_assessor_enabled
            or fundamental_ctx is None
            or fundamental_memory is None
        ):
            return None

        from autotrader.adapters.fundamental.schemas import (
            FundamentalMemory,
        )
        from autotrader.decision.unified.fundamental_assessor import (
            FundamentalRiskAssessor,
        )

        if not hasattr(self, "_fund_assessor"):
            self._fund_assessor = FundamentalRiskAssessor()

        return self._fund_assessor.assess(
            fundamental_ctx,
            fundamental_memory,
        )

    def _generate_signal_new(
        self,
        current_time: pd.Timestamp,
        candle: Candle | None = None,
        fundamental_ctx: FundamentalContext | None = None,
        fundamental_memory: FundamentalMemorySnapshot | None = None,
    ) -> ConsolidatedSignal:
        """新アーキテクチャでのシグナル生成

        Args:
            current_time: 現在時刻
            candle: 現在のローソク足
            fundamental_ctx: ファンダメンタルコンテキスト
            fundamental_memory: メモリスナップショット

        Returns:
            ConsolidatedSignal: 統合シグナル
        """
        # リトレースエントリー保留チェック
        if self._pending_entry is not None:
            _pe = self._pending_entry
            _m1_row_pe = self._get_current_row(
                "M1",
                current_time,
            )
            if _m1_row_pe is not None:
                _pe.bars_waited += 1
                _low = _m1_row_pe.get("low")
                _high = _m1_row_pe.get("high")

                # リトレース到達判定
                _reached = False
                if _pe.direction == SignalType.BUY:
                    if (
                        _low is not None
                        and not pd.isna(_low)
                        and float(_low) <= _pe.target_price
                    ):
                        _reached = True
                elif _pe.direction == SignalType.SELL:
                    if (
                        _high is not None
                        and not pd.isna(_high)
                        and float(_high) >= _pe.target_price
                    ):
                        _reached = True

                if _reached:
                    # リトレース到達 → エントリー実行
                    _entry_price = _pe.target_price
                    self._pending_entry = None
                    return self._build_retrace_signal(
                        _pe,
                        _entry_price,
                        current_time,
                        candle,
                    )

                # タイムアウト判定
                if _pe.bars_waited >= _pe.max_wait_bars:
                    if _pe.fallback_entry:
                        # フォールバック: 現在価格でエントリー
                        self._pending_entry = None
                        return self._build_retrace_signal(
                            _pe,
                            None,
                            current_time,
                            candle,
                        )
                    else:
                        # キャンセル
                        self._pending_entry = None
                        return self._hold_signal(
                            "リトレース待機タイムアウト（キャンセル）"
                        )

                # 待機継続 → HOLD
                return self._hold_signal(
                    f"リトレース待機中({_pe.bars_waited}/{_pe.max_wait_bars})"
                )

        # M1モメンタム確認待機チェック
        if self._pending_momentum is not None:
            return self._check_pending_momentum(
                current_time,
                candle,
            )

        # 1. 日次リセット
        py_time = current_time.to_pydatetime()
        self.risk_manager.reset_daily(py_time)

        # 2. リスク管理チェック（早期リターンで不要な計算を回避）
        _open_pos_count = (
            self.state.open_buy_count + self.state.open_sell_count
        )
        can_trade, reason = self.risk_manager.can_trade(
            py_time,
            open_position_count=_open_pos_count,
        )
        if not can_trade:
            return self._hold_signal(reason)

        # 2b. セッション切替待機チェック
        session_result = self._session_transition_filter.check(py_time)
        if session_result.should_filter:
            return self._hold_signal(session_result.reason)

        # 3. 初期プラン（デフォルトTF値）
        plan = TradingPlan.create_universal(self.config)

        # 4. 全TF評価 → tf_signals
        tf_signals: dict[str, TimeframeSignal] = {}

        _PARALLEL_TF_THRESHOLD = 12
        _eval_tfs = [tf for tf in self.timeframes if tf in self.evaluators]

        if len(_eval_tfs) > _PARALLEL_TF_THRESHOLD:
            tf_signals = self._evaluate_tfs_parallel(
                _eval_tfs,
                current_time,
                candle,
                plan,
            )
        else:
            for tf in _eval_tfs:
                row = self._get_current_row(tf, current_time)
                if row is None:
                    continue
                signal = self.evaluators[tf].evaluate(
                    row,
                    candle,
                    plan,
                    current_time,
                )
                tf_signals[tf] = signal

        # 5. 動的TF選択 → 全TFロール決定
        if tf_signals:
            _dynamic_result = self._dynamic_tf_selector.select(
                tf_signals,
            )
            plan = dataclasses.replace(
                plan,
                primary_tf=_dynamic_result.selected_primary_tf,
                manage_tf=_dynamic_result.selected_manage_tf,
                dynamic_entry_tf=_dynamic_result.selected_entry_tf,
                max_holding_bars=_dynamic_result.max_holding_bars,
                tp_sl_ratio_range=_dynamic_result.tp_sl_ratio_range,
            )
            _regime_tf = _dynamic_result.selected_regime_tf
            _htf_tfs = _dynamic_result.selected_htf_alignment_tfs
        else:
            _regime_tf = self.config.regime_detection_tf
            _htf_tfs = list(self.config.effective_htf_alignment_tfs)

        # 6. レジーム検出（動的regime_tfを使用）
        regime_result = self._detect_regime(
            current_time,
            regime_tf=_regime_tf,
        )

        # 7. HTF整合度（動的htf_tfsを使用）
        htf_alignment = self._get_htf_alignment(
            current_time,
            htf_tfs=_htf_tfs,
        )

        # Layer 3: ボラティリティ連動ポジション上限
        if (
            self.config.risk.volatility_position_limit_enabled
            and regime_result.regime == MarketRegime.HIGH_VOL
        ):
            _vol_max = int(
                self.config.max_positions
                * self.config.risk.high_vol_max_positions_ratio
            )
            if _open_pos_count >= max(_vol_max, 1):
                return self._hold_signal(
                    f"HIGH_VOLポジション上限({_open_pos_count}/{_vol_max})"
                )

        # レジーム遷移検出
        _current_regime = regime_result.regime.value
        _regime_transitioned = False
        if self._prev_regime and self._prev_regime != _current_regime:
            self._regime_transition_bars = 0
            _regime_transitioned = True
        else:
            self._regime_transition_bars += 1

        # 分析用に最後のモード/レジームを保持
        self._last_mode = plan.mode
        self._last_regime = _current_regime
        self._prev_regime = _current_regime

        # 8. TFルーティング（動的plan使用）
        tf_set = self.tf_router.route(plan)

        # 9. コンセンサスはモード別TFセットのみ対象（役割重みを保持）
        consensus_signals: dict[str, TimeframeSignal] = {
            tf: tf_signals[tf] for tf in tf_set.all_tfs if tf in tf_signals
        }

        # Phase 2b: ファンダメンタル評価（consolidate前に実行）
        # conviction boost適用のため方向判定前に評価する
        _fund_assessment = self._assess_fundamental(
            fundamental_ctx,
            fundamental_memory,
        )
        self._last_fundamental_assessment = _fund_assessment
        _fund_boosted = False

        # アダプティブオーバーライド取得
        _overrides = (
            self._adaptive_tuner.get_overrides()
            if self._adaptive_tuner
            else AdaptiveOverrides()
        )

        # コンセンサス統合（閾値オーバーライド適用）
        _threshold_override = None
        _base_threshold = self.consensus.threshold
        if _overrides.consensus_threshold_delta != 0.0:
            _base_threshold = (
                _base_threshold + _overrides.consensus_threshold_delta
            )
        # Layer 2: 同方向プログレッシブ閾値
        if self.config.risk.progressive_threshold_enabled:
            _max_same_dir_count = max(
                self.state.open_buy_count,
                self.state.open_sell_count,
            )
            if _max_same_dir_count > 0:
                _base_threshold += (
                    _max_same_dir_count
                    * self.config.risk.progressive_threshold_per_position
                )
        # レジーム別閾値調整（TREND勝率41%→閾値引き上げ）
        if (
            self.config.regime_threshold_enabled
            and regime_result.regime == MarketRegime.TREND
        ):
            _base_threshold = (
                _base_threshold + self.config.regime_trend_threshold_add
            )
        # HTFスコア不一致フィルター（HTF整合度低→閾値引き上げ）
        if (
            self.config.htf_score_filter_enabled
            and htf_alignment <= self.config.htf_score_filter_min_alignment
        ):
            _base_threshold = (
                _base_threshold + self.config.htf_score_filter_threshold_add
            )
        # レジーム遷移ボーナス（RANGE→BREAKOUTで閾値引下げ）
        if (
            self.config.regime_transition_enabled
            and self._prev_regime == "RANGE"
            and _current_regime == "BREAKOUT"
            and self._regime_transition_bars
            <= self.config.regime_transition_window
        ):
            _base_threshold = (
                _base_threshold
                + self.config.regime_transition_breakout_bonus
            )
        # クロスペア合意ボーナス（複数ペアが同方向で閾値引下げ）
        if (
            self.config.cross_pair_agreement_enabled
            and self._cross_pair_directions
            and consensus_signals
        ):
            # 暫定方向を推定（buy_score vs sell_score）
            _prelim_buy = sum(
                s.buy_strength for s in consensus_signals.values()
            )
            _prelim_sell = sum(
                s.sell_strength for s in consensus_signals.values()
            )
            _prelim_dir = (
                "BUY" if _prelim_buy > _prelim_sell else "SELL"
            )
            _same_dir_count = sum(
                1 for d in self._cross_pair_directions.values()
                if d == _prelim_dir
            )
            if _same_dir_count >= self.config.cross_pair_min_agreement:
                _base_threshold = (
                    _base_threshold
                    + self.config.cross_pair_agreement_bonus
                )
        if _base_threshold != self.consensus.threshold:
            _threshold_override = _base_threshold
        consensus = self.consensus.consolidate(
            consensus_signals,
            plan,
            threshold_override=_threshold_override,
        )

        # Phase 2b: コンビクションブースト救済
        # 閾値不足でHOLDでも、ファンダメンタルが方向を
        # 支持する場合は閾値引き下げで救済
        if (
            consensus.direction == SignalType.HOLD
            and _fund_assessment is not None
            and consensus.score > 0
        ):
            _prelim_dir = (
                SignalType.BUY
                if consensus.buy_score > consensus.sell_score
                else (
                    SignalType.SELL
                    if consensus.sell_score > consensus.buy_score
                    else SignalType.HOLD
                )
            )
            if _prelim_dir != SignalType.HOLD:
                _boost_sign = 1.0 if _prelim_dir == SignalType.BUY else -1.0
                _boost_adj = _fund_assessment.get_threshold_adjustment(
                    signal_direction=_boost_sign,
                )
                # ブーストのみ適用（adj < 0 = 閾値引下げ）
                if _boost_adj < 0:
                    _boosted_th = consensus.threshold + _boost_adj
                    if consensus.score >= _boosted_th:
                        _fund_boosted = True
                        consensus = ConsensusResult(
                            direction=_prelim_dir,
                            score=consensus.score,
                            threshold=_boosted_th,
                            aligned_tfs=(consensus.aligned_tfs),
                            reasoning=(
                                "ファンダブースト: "
                                f"score="
                                f"{consensus.score:.2f}"
                                f"≥{_boosted_th:.2f}"
                                f"(adj="
                                f"{_boost_adj:+.2f})"
                            ),
                            buy_score=(consensus.buy_score),
                            sell_score=(consensus.sell_score),
                            dynamic_entry_tf=(consensus.dynamic_entry_tf),
                        )

        if consensus.direction == SignalType.HOLD:
            if self._flow_analyzer:
                from autotrader.core.diagnostics import (
                    SignalStepRecord,
                )

                tf_detail = {}
                for tf_name, sig in tf_signals.items():
                    tf_detail[tf_name] = {
                        "direction": sig.direction.value,
                        "buy_strength": sig.buy_strength,
                        "sell_strength": sig.sell_strength,
                        "net_strength": sig.net_strength,
                    }
                self._flow_analyzer.collect(
                    SignalStepRecord(
                        timestamp=str(current_time),
                        regime=regime_result.regime.value,
                        volatility=regime_result.volatility_level,
                        mode=plan.mode,
                        primary_tf=plan.primary_tf,
                        risk_passed=True,
                        tf_details=tf_detail,
                        consensus_direction="HOLD",
                        consensus_score=consensus.score,
                        consensus_threshold=consensus.threshold,
                        consensus_passed=False,
                        final_direction="HOLD",
                        hold_reason=consensus.reasoning,
                    )
                )
            return self._hold_with_analysis(
                "スコア不足",
                plan,
                tf_signals,
                consensus,
                regime_result,
                htf_alignment,
            )

        # BCA: 方向性エッジ評価
        _bca_penalty = 0.0
        if self._edge_assessor is not None:
            _edge_result = self._edge_assessor.assess(
                consensus,
                tf_signals,
                tf_set,
            )
            if not _edge_result.passed:
                return self._hold_with_analysis(
                    "スコア不足",
                    plan,
                    tf_signals,
                    consensus,
                    regime_result,
                    htf_alignment,
                )
            _bca_penalty = _edge_result.penalty

        # M1マイクロ反転フィルタ
        if self._micro_reversal_filter.config.enabled:
            _m1_row = self._get_current_row(
                "M1",
                current_time,
            )
            _m1_df = self._market_data.get("M1")
            _m1_idx = self._current_indices.get("M1")
            _mr_result = self._micro_reversal_filter.check(
                direction=consensus.direction,
                m1_row=_m1_row,
                m1_df=_m1_df,
                m1_index=_m1_idx,
            )
            if _mr_result.should_filter:
                return self._hold_with_analysis(
                    "スコア不足",
                    plan,
                    tf_signals,
                    consensus,
                    regime_result,
                    htf_alignment,
                )

        # M1実行ゲート
        if self._m1_exec_gate.config.enabled:
            _m1_row_gate = self._get_current_row(
                "M1",
                current_time,
            )
            _gate_result = self._m1_exec_gate.check(
                direction=consensus.direction,
                m1_row=_m1_row_gate,
            )
            if not _gate_result.passed:
                return self._hold_with_analysis(
                    "スコア不足",
                    plan,
                    tf_signals,
                    consensus,
                    regime_result,
                    htf_alignment,
                )

        # Phase 2b: ファンダメンタル方向フィルター（ペナルティ）
        # ブーストで救済されたトレードはスキップ（方向一致確認済み）
        if _fund_assessment is not None and not _fund_boosted:
            _dir_sign = 1.0 if consensus.direction == SignalType.BUY else -1.0
            _fund_adj = _fund_assessment.get_threshold_adjustment(
                signal_direction=_dir_sign,
            )
            _effective_threshold = consensus.threshold + _fund_adj
            if consensus.score < _effective_threshold:
                return self._hold_with_analysis(
                    "ファンダフィルター",
                    plan,
                    tf_signals,
                    consensus,
                    regime_result,
                    htf_alignment,
                )

        # SoftGuardチェック（デモモードでも情報取得: 出力データに使用）
        # ATR比率・絶対ATRを計算してボラティリティ状態をSoftGuardに渡す
        _primary_atr_row = self._get_current_row(
            plan.primary_tf,
            current_time,
        )
        _atr_ratio = 1.0
        _primary_atr_abs = None  # 絶対ATR値（primary_tf基準）
        if _primary_atr_row is not None:
            _atr = _primary_atr_row.get("atr_14")
            _atr_ma = _primary_atr_row.get("atr_ma_20")
            if (
                _atr is not None
                and _atr_ma is not None
                and not pd.isna(_atr)
                and not pd.isna(_atr_ma)
                and _atr_ma > 0
            ):
                _atr_ratio = float(_atr) / float(_atr_ma)
            if _atr is not None and not pd.isna(_atr):
                _primary_atr_abs = float(_atr)
        # SWING低ボラフィルター用: entry_tf(H1)のATRを取得
        # entry_atr(CSV列)はH1由来のため、スケールを合わせる
        _entry_tf_atr_abs = None
        _entry_tf_row = self._get_current_row(
            plan.entry_tf,
            current_time,
        )
        if _entry_tf_row is not None:
            _e_atr = _entry_tf_row.get("atr_14")
            if _e_atr is not None and not pd.isna(_e_atr):
                _entry_tf_atr_abs = float(_e_atr)

        # ボリューム比率（エントリーTFから取得）
        _vol_ratio: float | None = None
        if (
            self.config.volume_filter_enabled
            and _entry_tf_row is not None
        ):
            _vr = _entry_tf_row.get("volume_ratio")
            if _vr is not None and not pd.isna(_vr):
                _vol_ratio = float(_vr)

        # マクロレジームフィルタ: ペナルティ取得
        # HardGuardチェックは_filt_hold定義後に実行
        _macro_penalty, _macro_penalty_reason = (
            self._macro_regime_filter.get_penalty()
        )

        sg_context: dict[str, object] = {
            "spread_pips": self._get_spread_pips(current_time),
            "current_time": current_time.to_pydatetime(),
            "atr_ratio": _atr_ratio,
            "recent_losses": self.state.consecutive_losses,
            "trend_strength": regime_result.trend_strength,
            "mtf_alignment": (
                "aligned" if htf_alignment >= 0.3
                else "mixed"
            ),
            "volume_ratio": _vol_ratio,
            "volume_filter_enabled": (
                self.config.volume_filter_enabled
            ),
            "volume_filter_threshold": (
                self.config.volume_filter_threshold
            ),
            "volume_filter_penalty": (
                self.config.volume_filter_penalty
            ),
        }
        # ペア別スプレッド閾値を渡す
        if self.config.sg_spread_threshold_pips is not None:
            sg_context["sg_spread_threshold_pips"] = (
                self.config.sg_spread_threshold_pips
            )
        sg_result = self.soft_guard.check(
            sg_context,
            is_entry=True,
            fundamental_assessment=(
                _fund_assessment
                if self.config.fundamental_softguard_enabled
                else None
            ),
        )

        # BCAペナルティをSoftGuard結果に加算
        if _bca_penalty > 0:
            sg_result = dataclasses.replace(
                sg_result,
                total_penalty=(sg_result.total_penalty + _bca_penalty),
            )

        # マクロレジームペナルティをSoftGuard結果に加算
        if _macro_penalty > 0:
            sg_result = dataclasses.replace(
                sg_result,
                total_penalty=(
                    sg_result.total_penalty + _macro_penalty
                ),
            )

        # セッションフィルター
        hour_utc = (
            current_time.hour
            if hasattr(current_time, "hour")
            else current_time.to_pydatetime().hour
        )

        def _filt_hold(reason: str) -> ConsolidatedSignal:
            """フィルターHOLD用ローカルヘルパー"""
            return self._hold_with_analysis(
                reason,
                plan,
                tf_signals,
                consensus,
                regime_result,
                htf_alignment,
                sg_result,
            )

        # マクロレジームフィルタ: HardGuardチェック（EXTREME_FEAR→全停止）
        _macro_blocked, _macro_reason = (
            self._macro_regime_filter.should_block_trade()
        )
        if _macro_blocked:
            return _filt_hold(_macro_reason or "VIXブロック")

        # デモモード: コンセンサス閾値のみ。追加フィルタースキップ
        if not self.config.demo_mode:
            # 上位足トレンドフィルター（必須条件、動的htf_tfs使用）
            if not self._check_htf_trend_alignment(
                current_time,
                consensus.direction,
                htf_tfs=_htf_tfs,
            ):
                if self._flow_analyzer:
                    from autotrader.core.diagnostics import (
                        SignalStepRecord,
                    )

                    self._flow_analyzer.collect(
                        SignalStepRecord(
                            timestamp=str(current_time),
                            regime=regime_result.regime.value,
                            volatility=regime_result.volatility_level,
                            mode=plan.mode,
                            primary_tf=plan.primary_tf,
                            risk_passed=True,
                            consensus_direction=consensus.direction.value,
                            consensus_score=consensus.score,
                            consensus_threshold=consensus.threshold,
                            consensus_passed=True,
                            htf_passed=False,
                            htf_direction=consensus.direction.value,
                            final_direction="HOLD",
                            hold_reason="HTFトレンド不一致",
                        )
                    )
                return _filt_hold("HTFトレンド不一致")

            # SoftGuardペナルティによるブロック（常時有効）
            if sg_result.total_penalty >= 0.8:
                return _filt_hold("SoftGuardブロック")

            # ペナルティ上限フィルター（アダプティブ調整対応）
            _eff_penalty_cap = (
                self.config.penalty_cap - _overrides.penalty_cap_delta
            )
            if (
                _eff_penalty_cap < 0.8
                and sg_result.total_penalty >= _eff_penalty_cap
            ):
                return _filt_hold("SoftGuardブロック")

            # トレンド強度上限フィルター
            if (
                self.config.trend_strength_max < 999.0
                and regime_result.trend_strength
                >= self.config.trend_strength_max
            ):
                return _filt_hold("パラメータブロック")

            # ADX上限フィルタ（追いかけ防止）
            if (
                self.config.adx_upper_limit is not None
                and regime_result.adx > self.config.adx_upper_limit
            ):
                return _filt_hold("パラメータブロック")

            # TREND整合TF上限フィルタ
            if (
                self.config.trend_max_aligned_tfs is not None
                and regime_result.regime == MarketRegime.TREND
                and len(consensus.aligned_tfs)
                > self.config.trend_max_aligned_tfs
            ):
                return _filt_hold("パラメータブロック")

            # LONDONオフ時間ブロック（hour=7はLONDON境界）
            if hour_utc == 7 and sg_result.total_penalty > 0:
                return _filt_hold("時間帯ブロック")

            # TOKYOオフ時間フィルター（閾値6.6）
            if (
                4 <= hour_utc <= 6
                and sg_result.total_penalty > 0
                and consensus.score < 6.6
            ):
                return _filt_hold("時間帯ブロック")

            # 東京深夜フィルター（JST 02-06 = UTC 17-21）
            # 東京深夜は流動性低下でトレンド追従が困難
            if (
                17 <= hour_utc <= 21
                and regime_result.regime == MarketRegime.TREND
                and consensus.score < consensus.threshold + 0.3
            ):
                return _filt_hold("時間帯ブロック")

            # off_hours TREND完全ブロック
            # optimal_hours(UTC 8-17)外のTRENDを全てブロック
            if (
                self.config.off_hours_trend_block
                and hour_utc not in range(8, 18)
                and regime_result.regime == MarketRegime.TREND
            ):
                return _filt_hold("時間帯ブロック")

            # off_hours + 高htf_alignment 複合ブロック
            if (
                self.config.off_hours_high_align_block
                and hour_utc not in range(8, 18)
                and abs(htf_alignment)
                >= self.config.off_hours_high_align_threshold
            ):
                return _filt_hold("時間帯ブロック")

            # RANGE/LOW_VOLフィルタ群
            _range_hold = self._check_range_regime_filter(
                regime_result=regime_result,
                consensus=consensus,
                sg_result=sg_result,
                hour_utc=hour_utc,
                plan=plan,
                current_time=current_time,
            )
            if _range_hold is not None:
                return _filt_hold(_range_hold)

            # TOKYO低ペナルティ帯: 閾値+0.2
            if (
                4 <= hour_utc <= 6
                and 0 < sg_result.total_penalty <= 0.2
                and consensus.score < consensus.threshold + 0.2
            ):
                return _filt_hold("SoftGuardブロック")

            # MACDスロープ逆方向フィルター
            _primary_sig = tf_signals.get(plan.primary_tf)
            if _primary_sig and _primary_sig.score_breakdown:
                _macd_slope = _primary_sig.score_breakdown.macd_slope
                if _macd_slope <= self.config.macd_slope_filter_threshold:
                    return _filt_hold("レジームブロック")

        # 高alignment時スコアペナルティ
        if (
            self.config.high_align_penalty_threshold is not None
            and abs(htf_alignment) > self.config.high_align_penalty_threshold
        ):
            _penalty = self.config.high_align_penalty_score
            consensus = ConsensusResult(
                direction=consensus.direction,
                score=consensus.score - _penalty,
                threshold=consensus.threshold,
                aligned_tfs=consensus.aligned_tfs,
                reasoning=(
                    f"{consensus.reasoning}, "
                    f"AlignPenalty(-{_penalty:.1f},"
                    f" |align|="
                    f"{abs(htf_alignment):.2f}"
                    f">{self.config.high_align_penalty_threshold})"
                ),
                buy_score=consensus.buy_score,
                sell_score=consensus.sell_score,
                dynamic_entry_tf=(consensus.dynamic_entry_tf),
            )
            # ペナルティ後にスコアが閾値未満ならHOLD
            if consensus.score < consensus.threshold:
                return _filt_hold("SoftGuardブロック")

        # SL/TP計算（primary_tf由来）
        primary_signal = tf_signals.get(plan.primary_tf)
        if primary_signal is None:
            if self._flow_analyzer:
                from autotrader.core.diagnostics import (
                    SignalStepRecord,
                )

                self._flow_analyzer.collect(
                    SignalStepRecord(
                        timestamp=str(current_time),
                        regime=regime_result.regime.value,
                        volatility=regime_result.volatility_level,
                        mode=plan.mode,
                        primary_tf=plan.primary_tf,
                        risk_passed=True,
                        consensus_direction=consensus.direction.value,
                        consensus_score=consensus.score,
                        consensus_threshold=consensus.threshold,
                        consensus_passed=True,
                        htf_passed=True,
                        final_direction="HOLD",
                        hold_reason="primary_tfデータなし",
                    )
                )
            return _filt_hold("primary_tfデータなし")

        sl_pips, tp_pips = self._calculate_final_sl_tp(
            primary_signal=primary_signal,
            direction=consensus.direction,
            regime=regime_result.regime,
            sl_multiplier=_overrides.sl_multiplier,
            current_time=current_time,
            candle=candle,
            plan=plan,
        )

        # M1リトレースエントリー
        if self.config.m1_retrace_entry_enabled:
            _m1_row_rt = self._get_current_row(
                "M1",
                current_time,
            )
            if _m1_row_rt is not None:
                _atr_val = _m1_row_rt.get("atr_14")
                if _atr_val is not None and not pd.isna(_atr_val):
                    _pip_unit_rt = self.config.pip_unit
                    _retrace_pips = (
                        float(_atr_val)
                        / _pip_unit_rt
                        * self.config.m1_retrace_atr_factor
                    )
                    _close_rt = (
                        candle.close
                        if candle
                        else float(
                            _m1_row_rt.get(
                                "close",
                                0,
                            ),
                        )
                    )
                    if consensus.direction == SignalType.BUY:
                        _target = _close_rt - _retrace_pips * _pip_unit_rt
                    else:
                        _target = _close_rt + _retrace_pips * _pip_unit_rt

                    self._pending_entry = PendingEntry(
                        direction=consensus.direction,
                        target_price=_target,
                        original_close=_close_rt,
                        bars_waited=0,
                        max_wait_bars=(self.config.m1_retrace_max_wait_bars),
                        fallback_entry=(self.config.m1_retrace_fallback_entry),
                        sl_pips=sl_pips,
                        tp_pips=tp_pips,
                        confidence=min(
                            consensus.score / 20.0,
                            1.0,
                        ),
                        primary_tf=(primary_signal.timeframe),
                        rationale=(f"リトレース保留: target={_target:.3f}"),
                        consensus=consensus,
                        regime_result=regime_result,
                    )
                    return self._hold_signal(
                        f"リトレース待機開始: target={_target:.3f}"
                    )

        # ポジションサイジング
        lot = 0.01
        if self.config.enable_position_sizing:
            confidence = consensus.score / consensus.threshold
            # Phase 2b: ファンダメンタル値をSizingContextに反映
            _liq_factor = 1.0
            _vol_mult = 1.0
            if fundamental_ctx is not None:
                _liq_factor = fundamental_ctx.liquidity_factor
                _vol_mult = fundamental_ctx.volatility_multiplier
            # ファンダメンタルアセッサーのロット倍率も反映
            if _fund_assessment is not None:
                _liq_factor = min(
                    _liq_factor,
                    _fund_assessment.lot_multiplier,
                )
            sizing_context = SizingContext(
                equity=self.state.equity,
                sl_pips=sl_pips,
                confidence=confidence,
                regime=regime_result.regime,
                consecutive_losses=self.state.consecutive_losses,
                current_dd_pct=self.state.current_dd_pct,
                initial_equity=self.state.initial_equity,
                open_exposure_lot=self.state.open_exposure_lot,
                open_same_direction_lot=(self.state.open_same_direction_lot),
                liquidity_factor=_liq_factor,
                volatility_multiplier=_vol_mult,
                consensus_score=consensus.score,
                consensus_threshold=consensus.threshold,
                atr_ratio=_atr_ratio,
            )
            sizing_result = self.position_sizer.calculate(sizing_context)
            if sizing_result.blocked:
                return _filt_hold("資金不足")
            lot = sizing_result.lot
            # エッジ劣化WARNING時のロット縮小
            if self._edge_lot_multiplier < 1.0:
                lot = round(
                    lot * self._edge_lot_multiplier, 2,
                )
                if lot < 0.01:
                    return _filt_hold("エッジ劣化ロット縮小")

        rationale = (
            f"{consensus.reasoning}, mode={plan.mode}, lot={lot:.2f}"
        )

        # フロー分析: シグナル発生記録
        if self._flow_analyzer:
            from autotrader.core.diagnostics import (
                SignalStepRecord,
            )

            self._flow_analyzer.collect(
                SignalStepRecord(
                    timestamp=str(current_time),
                    regime=regime_result.regime.value,
                    volatility=regime_result.volatility_level,
                    mode=plan.mode,
                    primary_tf=plan.primary_tf,
                    risk_passed=True,
                    consensus_direction=consensus.direction.value,
                    consensus_score=consensus.score,
                    consensus_threshold=consensus.threshold,
                    consensus_passed=True,
                    htf_passed=True,
                    sl_pips=sl_pips,
                    tp_pips=tp_pips,
                    final_direction=consensus.direction.value,
                    hold_reason="",
                )
            )

        # TF別スコア内訳を集約
        tf_breakdowns: dict[str, dict[str, float]] = {}
        for tf_name, sig in tf_signals.items():
            if sig.score_breakdown is not None:
                tf_breakdowns[tf_name] = sig.score_breakdown.to_dict()

        # TF別方向を集約（UI表示用）
        tf_directions: dict[str, str] = {
            tf: sig.direction.value for tf, sig in tf_signals.items()
        }

        # 返却用confidence計算
        ret_confidence = min(
            consensus.score / consensus.threshold,
            1.0,
        )

        # strategy_id構築（モード_選択理由）
        _strategy_id = (
            f"{plan.mode}_{plan.selection_reason}"
            if plan.selection_reason
            else plan.mode
        )

        # M1モメンタム確認ゲート:
        # リトレース待機と排他的（リトレース優先）
        if (
            self.config.m1_momentum_gate_enabled
            and not self.config.m1_retrace_entry_enabled
            and self._pending_entry is None
        ):
            _scores = {
                tf: sig.confidence
                for tf, sig in tf_signals.items()
            }
            self._pending_momentum = PendingMomentumEntry(
                direction=consensus.direction,
                bars_waited=0,
                max_wait_bars=(
                    self.config.m1_momentum_max_wait
                ),
                momentum_required=(
                    self.config.m1_momentum_required
                ),
                momentum_count=0,
                sl_pips=sl_pips,
                tp_pips=tp_pips,
                confidence=ret_confidence,
                consensus_score=consensus.score,
                lot=lot,
                primary_tf=plan.primary_tf,
                rationale=rationale,
                regime=regime_result.regime.value,
                mode=plan.mode,
                consensus=consensus,
                regime_result=regime_result,
                entry_threshold=consensus.threshold,
                htf_alignment=htf_alignment,
                penalty_total=sg_result.total_penalty,
                penalty_breakdown={
                    r.value: v
                    for r, v in sg_result.penalties.items()
                },
                trend_strength=(
                    regime_result.trend_strength
                ),
                strategy_id=_strategy_id,
                tf_score_breakdowns=tf_breakdowns,
                buy_score=consensus.buy_score,
                sell_score=consensus.sell_score,
                aligned_tfs=consensus.aligned_tfs,
                scores=_scores,
                tf_directions=tf_directions,
            )
            return self._hold_signal(
                "M1モメンタム確認待機開始"
            )

        return ConsolidatedSignal(
            direction=consensus.direction,
            confidence=ret_confidence,
            primary_tf=plan.primary_tf,
            aligned_tfs=consensus.aligned_tfs,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            rationale=rationale,
            scores={tf: sig.confidence for tf, sig in tf_signals.items()},
            regime=regime_result.regime.value,
            mode=plan.mode,
            consensus_score=consensus.score,
            tf_score_breakdowns=tf_breakdowns,
            tf_directions=tf_directions,
            strategy_id=_strategy_id,
            entry_threshold=consensus.threshold,
            htf_alignment=htf_alignment,
            penalty_total=sg_result.total_penalty,
            penalty_breakdown={
                r.value: v for r, v in sg_result.penalties.items()
            },
            trend_strength=regime_result.trend_strength,
            buy_score=consensus.buy_score,
            sell_score=consensus.sell_score,
            lot=lot,
        )

    def _calculate_final_sl_tp(
        self,
        primary_signal: TimeframeSignal,
        direction: SignalType,
        regime: MarketRegime,
        sl_multiplier: float,
        current_time: pd.Timestamp,
        candle: Candle | None,
        plan: TradingPlan,
    ) -> tuple[float, float]:
        """SL/TPの最終計算

        primary_tfの基本SLに各種調整（M1構造的SL、TREND上下限）を適用し、
        TP/SL比率からTPを算出する。

        Args:
            primary_signal: プライマリTFシグナル
            direction: トレード方向
            regime: 市場レジーム
            sl_multiplier: SL倍率（アダプティブ調整）
            current_time: 現在時刻
            candle: ローソク足データ
            plan: トレーディングプラン

        Returns:
            tuple[float, float]: (sl_pips, tp_pips)
        """
        sl_pips = primary_signal.sl_pips * sl_multiplier
        # M1構造的SL
        if self.config.m1_structure_sl_enabled:
            _m1_row_sl = self._get_current_row(
                "M1",
                current_time,
            )
            if _m1_row_sl is not None:
                _pip_unit = self.config.pip_unit
                _current_close = (
                    candle.close
                    if candle
                    else (
                        _m1_row_sl.get("close")
                        if _m1_row_sl is not None
                        else None
                    )
                )
                if _current_close is not None:
                    if direction == SignalType.BUY:
                        _swing = _m1_row_sl.get(
                            "last_swing_low",
                        )
                        if _swing is not None and not pd.isna(_swing):
                            _struct_sl = (
                                (_current_close - float(_swing)) / _pip_unit
                                + self.config.m1_structure_sl_buffer_pips
                            )
                            _struct_sl = max(
                                self.config.m1_structure_sl_min_pips,
                                min(
                                    _struct_sl,
                                    self.config.m1_structure_sl_max_pips,
                                ),
                            )
                            sl_pips = _struct_sl
                    elif direction == SignalType.SELL:
                        _swing = _m1_row_sl.get(
                            "last_swing_high",
                        )
                        if _swing is not None and not pd.isna(_swing):
                            _struct_sl = (
                                (float(_swing) - _current_close) / _pip_unit
                                + self.config.m1_structure_sl_buffer_pips
                            )
                            _struct_sl = max(
                                self.config.m1_structure_sl_min_pips,
                                min(
                                    _struct_sl,
                                    self.config.m1_structure_sl_max_pips,
                                ),
                            )
                            sl_pips = _struct_sl
        # TREND時のSL下限上書き
        if (
            self.config.trend_sl_min_pips is not None
            and regime == MarketRegime.TREND
        ):
            sl_pips = max(
                sl_pips,
                self.config.trend_sl_min_pips,
            )
        # TREND時のSL上限キャップ
        if (
            self.config.trend_sl_max_pips is not None
            and regime == MarketRegime.TREND
        ):
            sl_pips = min(
                sl_pips,
                self.config.trend_sl_max_pips,
            )
        tp_sl_ratio = (
            plan.get_recommended_tp_sl_ratio() * self.config.tp_sl_ratio
        )
        tp_pips = sl_pips * tp_sl_ratio
        return sl_pips, tp_pips

    def set_cross_pair_directions(
        self, directions: dict[str, str],
    ) -> None:
        """クロスペアシグナル方向を設定

        マルチペアBTから他ペアの直近シグナル方向を受け取る。

        Args:
            directions: ペア名→方向("BUY"/"SELL")の辞書
        """
        self._cross_pair_directions = directions

    def set_current_spread_pips(
        self, spread_pips: float,
    ) -> None:
        """リアルタイムスプレッドを設定

        BT: CSV実スプレッドデータから毎足更新
        ライブ: MT5から取得して更新

        Args:
            spread_pips: 現在のスプレッド（pips）
        """
        self._current_spread_pips = spread_pips

    def _get_spread_pips(self, current_time: pd.Timestamp) -> float:
        """スプレッドを取得

        リアルタイム値が設定されていれば優先。
        なければプリセット値にフォールバック。

        Args:
            current_time: 現在時刻

        Returns:
            float: スプレッド（pips）
        """
        if self._current_spread_pips is not None:
            return self._current_spread_pips
        return self.config.spread_pips

    def _get_current_price(
        self,
        current_time: pd.Timestamp,
        candle: Candle | None,
    ) -> float:
        """現在価格を取得

        Args:
            current_time: 現在時刻
            candle: ローソク足

        Returns:
            float: 現在価格
        """
        if candle is not None:
            return candle.close

        row = self._get_current_row("M1", current_time)
        if row is not None:
            close = row.get("close")
            if close is not None and not pd.isna(close):
                return float(close)

        return 0.0

    def _get_all_tf_data(
        self,
        current_time: pd.Timestamp,
    ) -> dict[str, pd.DataFrame]:
        """全時間足のデータを取得（O(1)インデックス利用）

        Args:
            current_time: 現在時刻

        Returns:
            dict[str, pd.DataFrame]: 時間足別データ
        """
        result: dict[str, pd.DataFrame] = {}
        ct = current_time.to_datetime64()

        for tf, df in self._market_data.items():
            if df is None or df.empty:
                continue

            # インデックス追跡を利用してスライス
            time_arr = self._time_arrays.get(tf)
            if time_arr is not None:
                idx = self._current_indices.get(tf, 0)
                n = len(time_arr)
                # current_timeまでインデックスを進める
                while idx + 1 < n and time_arr[idx + 1] <= ct:
                    idx += 1
                self._current_indices[tf] = idx
                # current_timeまでのデータをスライス
                if time_arr[idx] <= ct:
                    result[tf] = df.iloc[: idx + 1]
            else:
                result[tf] = df

        return result

    def _evaluate_tfs_parallel(
        self,
        eval_tfs: list[str],
        current_time: pd.Timestamp,
        candle: "Candle | None",
        plan: "TradingPlan",
    ) -> dict[str, TimeframeSignal]:
        """TF評価を並列実行（12TF超の場合のみ使用）

        Args:
            eval_tfs: 評価対象TFリスト
            current_time: 現在時刻
            candle: ローソク足
            plan: トレーディングプラン

        Returns:
            dict[str, TimeframeSignal]: TF別シグナル
        """
        from concurrent.futures import ThreadPoolExecutor

        def _eval_single(tf: str) -> tuple[str, TimeframeSignal | None]:
            row = self._get_current_row(tf, current_time)
            if row is None:
                return tf, None
            signal = self.evaluators[tf].evaluate(
                row,
                candle,
                plan,
                current_time,
            )
            return tf, signal

        results: dict[str, TimeframeSignal] = {}
        _max_w = min(4, (len(eval_tfs) + 3) // 4)
        with ThreadPoolExecutor(max_workers=_max_w) as pool:
            for tf, signal in pool.map(_eval_single, eval_tfs):
                if signal is not None:
                    results[tf] = signal
        return results

    def _detect_regime(
        self,
        current_time: pd.Timestamp,
        regime_tf: str | None = None,
    ) -> "RegimeResult":
        """レジームを検出

        Args:
            current_time: 現在時刻
            regime_tf: レジーム検出TF（Noneの場合configデフォルト）

        Returns:
            RegimeResult: レジーム判定結果
        """
        from autotrader.calculator.features.regime_detector import RegimeResult

        # レジーム検出TFを使用（動的 or configデフォルト）
        _regime_tf = regime_tf or self.config.regime_detection_tf
        row = self._get_current_row(_regime_tf, current_time)
        if row is None:
            return RegimeResult(
                regime=MarketRegime.RANGE,
                trend_strength=0.0,
                volatility_level=1.0,
                adx=0.0,
                confidence=0.0,
                reasoning=f"{_regime_tf}データなし",
            )

        # ブレイクアウト/ボラ方向/CHOPPYの特徴量を計算
        if (
            self.config.regime_breakout_enabled
            or self.config.vol_direction_enabled
            or self.config.choppy_enabled
        ):
            row = self._enrich_breakout_features(
                row, _regime_tf,
            )

        return self.regime_detector.detect_from_row(row)

    def _enrich_breakout_features(
        self,
        row: pd.Series,
        timeframe: str,
    ) -> pd.Series:
        """ブレイクアウト/ボラ方向/CHOPPY特徴量をrowに追加

        直近N足の高値/安値突破、ATR変化率、
        Choppiness Indexを計算。

        Args:
            row: 現在のデータ行
            timeframe: 時間足

        Returns:
            pd.Series: 特徴量追加済みの行
        """
        import math

        df = self._market_data.get(timeframe)
        if df is None or df.empty:
            return row

        _idx = self._current_indices.get(timeframe, 0)
        _lookback = self.config.regime_breakout_lookback

        # ルックバック期間分のデータがない場合はスキップ
        if _idx < _lookback:
            return row

        # 直近N足の高値/安値（現在足を含まない）
        _slice = df.iloc[_idx - _lookback:_idx]
        _high_max = _slice["high"].max()
        _low_min = _slice["low"].min()
        _close = row.get("close", 0.0)

        # コピーして追加フィールドを設定
        row = row.copy()
        if not pd.isna(_close) and not pd.isna(_high_max):
            row["breakout_up"] = (
                1.0 if _close > _high_max else 0.0
            )
        else:
            row["breakout_up"] = 0.0

        if not pd.isna(_close) and not pd.isna(_low_min):
            row["breakout_down"] = (
                1.0 if _close < _low_min else 0.0
            )
        else:
            row["breakout_down"] = 0.0

        # ATR変化率: (現在ATR - ATR_MA) / ATR_MA
        _atr_col = None
        for _c in ["atr_14", "atr", "ATR"]:
            if _c in df.columns:
                _atr_col = _c
                break
        if _atr_col is not None and _idx >= _lookback:
            _atr_slice = df[_atr_col].iloc[
                _idx - _lookback:_idx + 1
            ]
            _atr_ma = _atr_slice.iloc[:-1].mean()
            _atr_now = _atr_slice.iloc[-1]
            if (
                not pd.isna(_atr_ma)
                and not pd.isna(_atr_now)
                and _atr_ma > 0
            ):
                row["atr_change_rate"] = (
                    (_atr_now - _atr_ma) / _atr_ma
                )
            else:
                row["atr_change_rate"] = 0.0
        else:
            row["atr_change_rate"] = 0.0

        # Choppiness Index（14期間）
        # CI = 100 * log10(sum(ATR,N)/(high_N-low_N))
        #      / log10(N)
        _ci_period = 14
        if (
            self.config.choppy_enabled
            and _atr_col is not None
            and _idx >= _ci_period
        ):
            _ci_slice = df.iloc[
                _idx - _ci_period + 1:_idx + 1
            ]
            _atr_sum = _ci_slice[_atr_col].sum()
            _h_max = _ci_slice["high"].max()
            _l_min = _ci_slice["low"].min()
            _range = _h_max - _l_min
            if (
                _range > 0
                and _atr_sum > 0
                and not pd.isna(_atr_sum)
                and not pd.isna(_range)
            ):
                row["choppiness_index"] = (
                    100.0
                    * math.log10(_atr_sum / _range)
                    / math.log10(_ci_period)
                )
            else:
                row["choppiness_index"] = 0.0
        else:
            row["choppiness_index"] = 0.0

        return row

    def _get_htf_alignment(
        self,
        current_time: pd.Timestamp,
        htf_tfs: list[str] | None = None,
    ) -> float:
        """HTF整合度を取得

        Args:
            current_time: 現在時刻
            htf_tfs: HTF整合チェック用TFリスト（Noneの場合configデフォルト）

        Returns:
            float: HTF整合度（-1から1）
        """
        alignment_scores = []
        _tfs = htf_tfs or list(self.config.effective_htf_alignment_tfs)

        for tf in _tfs:
            row = self._get_current_row(tf, current_time)
            if row is None:
                continue

            ma_alignment = row.get("ma_alignment")
            if ma_alignment is not None and not pd.isna(ma_alignment):
                alignment_scores.append(float(ma_alignment))

        if not alignment_scores:
            return 0.0
        return sum(alignment_scores) / len(alignment_scores)

    def _get_bb_width(
        self,
        plan: TradingPlan,
        current_time: pd.Timestamp,
    ) -> float | None:
        """primary_tfのbb_width取得（None/NaN時はNone）

        Args:
            plan: トレーディングプラン
            current_time: 現在時刻

        Returns:
            float | None: bb_width値。取得不可時はNone
        """
        row = self._get_current_row(
            plan.primary_tf,
            current_time,
        )
        if row is None:
            return None
        val = row.get("bb_width")
        if val is None or pd.isna(val):
            return None
        return float(val)

    def _check_range_regime_filter(
        self,
        regime_result: RegimeResult,
        consensus: ConsensusResult,
        sg_result: SoftGuardResult,
        hour_utc: int,
        plan: TradingPlan,
        current_time: pd.Timestamp,
    ) -> str | None:
        """RANGE/LOW_VOLレジーム統合フィルタ

        従来5つに分散していたRANGE系フィルタを1つに統合。
        各条件にスコア(0.0-1.0)を割り当て、合計が閾値を
        超えた場合のみブロックする。個別条件での即HOLDを廃止
        し、累積的な過剰排除を防ぐ。

        range_filter_consolidated=False で従来の個別フィルタ
        にフォールバックする（A/Bテスト用）。

        Args:
            regime_result: レジーム判定結果
            consensus: コンセンサス結果
            sg_result: SoftGuard結果
            hour_utc: UTC時間
            plan: トレーディングプラン
            current_time: 現在時刻

        Returns:
            str | None: ブロック理由。Noneなら通過
        """
        regime = regime_result.regime

        # RANGE/LOW_VOL以外は対象外
        if regime not in (
            MarketRegime.RANGE,
            MarketRegime.LOW_VOL,
        ):
            return None

        # 従来モード（個別フィルタ）
        if not self.config.range_filter_consolidated:
            return self._check_range_legacy(
                regime_result,
                consensus,
                sg_result,
                hour_utc,
                plan,
                current_time,
            )

        # --- 統合モード: 各条件のスコアを加算 ---
        score = 0.0
        reasons: list[str] = []

        # 1. LOW_VOLレジーム（スプレッド影響大）
        if regime == MarketRegime.LOW_VOL:
            _margin = (
                consensus.threshold
                + self._LOW_VOL_SCORE_MARGIN
                - consensus.score
            )
            if _margin > 0:
                # スコア余裕度に応じて0.0-1.0
                _s = min(
                    _margin / self._LOW_VOL_SCORE_MARGIN,
                    1.0,
                )
                score += _s
                reasons.append(f"LOW_VOL({_s:.2f})")

        # 2. RANGE + トレンド弱（方向感欠如）
        if (
            regime == MarketRegime.RANGE
            and regime_result.trend_strength < self._WEAK_TREND_THRESHOLD
        ):
            # trend_strength=0で1.0、閾値で0.0
            _s = (
                1.0 - regime_result.trend_strength / self._WEAK_TREND_THRESHOLD
            )
            score += _s
            reasons.append(
                f"弱トレンド({_s:.2f},ts={regime_result.trend_strength:.2f})"
            )

        # 3. RANGE + ペナルティ + 低BB幅
        if regime == MarketRegime.RANGE and sg_result.total_penalty > 0:
            _bb_val = self._get_bb_width(
                plan,
                current_time,
            )
            if _bb_val is not None:
                _thr = self.config.range_day_bbw_threshold
                if _bb_val < _thr:
                    # BB幅がthreshold比でどれだけ小さいか
                    _s = min(
                        (_thr - _bb_val) / _thr,
                        1.0,
                    )
                    score += _s
                    reasons.append(f"低BBW({_s:.2f},bbw={_bb_val:.4f})")

        # 4. Weak Hours（JST 18-21 = UTC 9-12）
        if (
            self.config.weak_hours_enabled
            and 9 <= hour_utc <= 12
            and regime == MarketRegime.RANGE
        ):
            _wh_thr = (
                consensus.threshold + self.config.weak_hours_score_premium
            )
            _margin = _wh_thr - consensus.score
            if _margin > 0:
                _s = min(
                    _margin / self.config.weak_hours_score_premium,
                    1.0,
                )
                score += _s
                reasons.append(f"WeakHours({_s:.2f},h={hour_utc})")

        # 5. RANGEスコアプレミアム（低スコア帯を除外）
        _sp = self.config.range_day_score_premium
        if _sp > 0 and regime == MarketRegime.RANGE:
            _margin = consensus.threshold + _sp - consensus.score
            if _margin > 0:
                _s = min(_margin / _sp, 1.0)
                score += _s
                reasons.append(
                    f"スコアPrem({_s:.2f},sc={consensus.score:.1f})"
                )

        _thr = self.config.range_filter_block_threshold
        if score >= _thr:
            return (
                f"RANGE統合フィルタ: "
                f"score={score:.2f}>={_thr:.2f} "
                f"[{','.join(reasons)}]"
            )
        return None

    def _check_range_legacy(
        self,
        regime_result: RegimeResult,
        consensus: ConsensusResult,
        sg_result: SoftGuardResult,
        hour_utc: int,
        plan: TradingPlan,
        current_time: pd.Timestamp,
    ) -> str | None:
        """従来の個別RANGEフィルタ（フォールバック用）

        range_filter_consolidated=False 時に使用。
        既存動作を完全に維持する。

        Args:
            regime_result: レジーム判定結果
            consensus: コンセンサス結果
            sg_result: SoftGuard結果
            hour_utc: UTC時間
            plan: トレーディングプラン
            current_time: 現在時刻

        Returns:
            str | None: ブロック理由。Noneなら通過
        """
        # LOW_VOL制限
        _lv_margin = self._LOW_VOL_SCORE_MARGIN
        if (
            regime_result.regime == MarketRegime.LOW_VOL
            and consensus.score < consensus.threshold + _lv_margin
        ):
            return "レジームブロック"

        # RANGE + トレンド弱制限
        if (
            regime_result.regime == MarketRegime.RANGE
            and regime_result.trend_strength < self._WEAK_TREND_THRESHOLD
        ):
            return "レジームブロック"

        # RANGE ペナルティ+低ボラ制限
        if (
            regime_result.regime == MarketRegime.RANGE
            and sg_result.total_penalty > 0
        ):
            _bb_val = self._get_bb_width(
                plan,
                current_time,
            )
            if (
                _bb_val is not None
                and _bb_val < self.config.range_day_bbw_threshold
            ):
                return "レジームブロック"

        # Weak Hours RANGEフィルター
        if (
            self.config.weak_hours_enabled
            and 9 <= hour_utc <= 12
            and regime_result.regime == MarketRegime.RANGE
            and consensus.score
            < consensus.threshold + self.config.weak_hours_score_premium
        ):
            return "レジームブロック"

        # RANGEスコアプレミアム
        _score_premium = self.config.range_day_score_premium
        if (
            _score_premium > 0
            and regime_result.regime == MarketRegime.RANGE
            and consensus.score < consensus.threshold + _score_premium
        ):
            return "レジームブロック"

        return None

    def _hold_signal(self, reason: str) -> ConsolidatedSignal:
        """HOLDシグナルを生成

        Args:
            reason: 理由

        Returns:
            ConsolidatedSignal: HOLDシグナル
        """
        return ConsolidatedSignal(
            direction=SignalType.HOLD,
            confidence=0.0,
            primary_tf="",
            aligned_tfs=[],
            sl_pips=0.0,
            tp_pips=0.0,
            rationale=reason,
            scores={},
        )

    def _hold_with_analysis(
        self,
        reason: str,
        plan: TradingPlan,
        tf_signals: dict[str, TimeframeSignal],
        consensus: ConsensusResult,
        regime_result: RegimeResult,
        htf_alignment: float,
        sg_result: SoftGuardResult | None = None,
    ) -> ConsolidatedSignal:
        """分析データ付きHOLDシグナル

        TF評価・コンセンサス計算後にHOLDを返す場合、
        UI表示用の分析データを保持する。

        Args:
            reason: HOLD理由
            plan: トレーディングプラン
            tf_signals: 時間足別シグナル
            consensus: コンセンサス結果
            regime_result: レジーム判定結果
            htf_alignment: HTF整合スコア
            sg_result: SoftGuard結果

        Returns:
            ConsolidatedSignal: 分析データ付きHOLDシグナル
        """
        tf_breakdowns: dict[str, dict[str, float]] = {}
        for tf_name, sig in tf_signals.items():
            if sig.score_breakdown is not None:
                tf_breakdowns[tf_name] = sig.score_breakdown.to_dict()

        # TF別方向を集約（UI表示用）
        tf_directions: dict[str, str] = {
            tf: sig.direction.value for tf, sig in tf_signals.items()
        }

        return ConsolidatedSignal(
            direction=SignalType.HOLD,
            confidence=0.0,
            primary_tf=plan.primary_tf,
            aligned_tfs=consensus.aligned_tfs,
            sl_pips=0.0,
            tp_pips=0.0,
            rationale=reason,
            scores={tf: sig.confidence for tf, sig in tf_signals.items()},
            regime=regime_result.regime.value,
            mode=plan.mode,
            consensus_score=consensus.score,
            tf_score_breakdowns=tf_breakdowns,
            tf_directions=tf_directions,
            entry_threshold=consensus.threshold,
            htf_alignment=htf_alignment,
            penalty_total=(sg_result.total_penalty if sg_result else 0.0),
            penalty_breakdown=(
                {r.value: v for r, v in sg_result.penalties.items()}
                if sg_result
                else {}
            ),
            trend_strength=regime_result.trend_strength,
            buy_score=consensus.buy_score,
            sell_score=consensus.sell_score,
        )

    def _build_retrace_signal(
        self,
        pe: PendingEntry,
        entry_price: float | None,
        current_time: pd.Timestamp,
        candle: Candle | None,
    ) -> ConsolidatedSignal:
        """リトレースエントリーシグナルを構築.

        Args:
            pe: 保留エントリーデータ
            entry_price: リトレース到達価格（Noneならフォールバック）
            current_time: 現在時刻
            candle: 現在足

        Returns:
            ConsolidatedSignal: エントリーシグナル
        """
        _pip_unit = self.config.pip_unit
        _base = (
            entry_price
            if entry_price is not None
            else (candle.close if candle else pe.original_close)
        )
        _mode = (
            "リトレース到達" if entry_price is not None else "フォールバック"
        )

        return ConsolidatedSignal(
            direction=pe.direction,
            confidence=pe.confidence,
            primary_tf=pe.primary_tf,
            aligned_tfs=[],
            sl_pips=pe.sl_pips,
            tp_pips=pe.tp_pips,
            rationale=(
                f"M1{_mode}エントリー: "
                f"target={pe.target_price:.3f}, "
                f"base={_base:.3f}, "
                f"waited={pe.bars_waited}bars"
            ),
            entry_price=_base,
            consensus_score=(
                pe.consensus.score
                if pe.consensus and hasattr(pe.consensus, "score")
                else None
            ),
            regime=(
                str(pe.regime_result.regime.value)
                if pe.regime_result and hasattr(pe.regime_result, "regime")
                else None
            ),
        )

    def _check_pending_momentum(
        self,
        current_time: pd.Timestamp,
        candle: Candle | None,
    ) -> ConsolidatedSignal:
        """M1モメンタム確認待機を処理する.

        Args:
            current_time: 現在時刻
            candle: 現在足

        Returns:
            ConsolidatedSignal: シグナル
        """
        pm = self._pending_momentum
        assert pm is not None  # noqa: S101
        pm.bars_waited += 1

        # M1足のモメンタム確認
        m1_row = self._get_current_row(
            "M1", current_time,
        )
        if m1_row is not None:
            close = m1_row.get("close")
            open_ = m1_row.get("open")
            if (
                close is not None
                and open_ is not None
                and not pd.isna(close)
                and not pd.isna(open_)
            ):
                _cl = float(close)
                _op = float(open_)
                # 方向確認: BUY=陽線, SELL=陰線
                is_momentum = (
                    pm.direction == SignalType.BUY
                    and _cl > _op
                ) or (
                    pm.direction == SignalType.SELL
                    and _cl < _op
                )

                if is_momentum:
                    pm.momentum_count += 1
                else:
                    pm.momentum_count = 0  # リセット

                # 確認完了 → エントリーシグナル発行
                if pm.momentum_count >= pm.momentum_required:
                    sig = self._build_momentum_signal(
                        pm, _cl,
                    )
                    self._pending_momentum = None
                    return sig

        # タイムアウト → 見送り
        if pm.bars_waited >= pm.max_wait_bars:
            self._pending_momentum = None
            return self._hold_signal(
                "M1モメンタム確認タイムアウト"
                f"({pm.max_wait_bars}本)"
            )

        # 待機継続 → HOLD
        return self._hold_signal(
            f"M1モメンタム確認中"
            f"({pm.momentum_count}/"
            f"{pm.momentum_required}連続, "
            f"{pm.bars_waited}/"
            f"{pm.max_wait_bars}本)"
        )

    def _build_momentum_signal(
        self,
        pm: PendingMomentumEntry,
        entry_close: float,
    ) -> ConsolidatedSignal:
        """モメンタム確認済みエントリーシグナルを構築.

        Args:
            pm: モメンタム待機データ
            entry_close: エントリー時点の終値

        Returns:
            ConsolidatedSignal: エントリーシグナル
        """
        return ConsolidatedSignal(
            direction=pm.direction,
            confidence=pm.confidence,
            primary_tf=pm.primary_tf,
            aligned_tfs=pm.aligned_tfs,
            sl_pips=pm.sl_pips,
            tp_pips=pm.tp_pips,
            rationale=(
                f"M1モメンタム確認済み: "
                f"{pm.momentum_required}本連続, "
                f"waited={pm.bars_waited}bars, "
                f"{pm.rationale}"
            ),
            scores=pm.scores,
            regime=pm.regime,
            mode=pm.mode,
            consensus_score=pm.consensus_score,
            tf_score_breakdowns=(
                pm.tf_score_breakdowns
            ),
            tf_directions=pm.tf_directions,
            strategy_id=pm.strategy_id,
            entry_threshold=pm.entry_threshold,
            htf_alignment=pm.htf_alignment,
            penalty_total=pm.penalty_total,
            penalty_breakdown=pm.penalty_breakdown,
            trend_strength=pm.trend_strength,
            buy_score=pm.buy_score,
            sell_score=pm.sell_score,
            lot=pm.lot,
            entry_price=entry_close,
        )

    def _get_current_row(
        self,
        timeframe: str,
        current_time: pd.Timestamp,
    ) -> pd.Series | None:
        """指定時間足の現在データ行を取得（O(1)償却）

        前回位置から前方スキャンでインデックスを追跡する。
        current_timeは常に前進するため、0-2ステップで到達。

        Args:
            timeframe: 時間足
            current_time: 現在時刻

        Returns:
            pd.Series | None: データ行
        """
        df = self._market_data.get(timeframe)
        if df is None or df.empty:
            return None

        time_arr = self._time_arrays.get(timeframe)
        if time_arr is None:
            return None

        # 現在のインデックスを取得
        last_idx = self._current_indices.get(timeframe, 0)
        n = len(time_arr)

        if n == 0:
            return None

        # current_timeをnumpy datetime64に変換
        ct = current_time.to_datetime64()

        # 前方スキャン: 次のバーがcurrent_time以下なら進む
        while last_idx + 1 < n and time_arr[last_idx + 1] <= ct:
            last_idx += 1

        # インデックスを保存
        self._current_indices[timeframe] = last_idx

        # 最初のバーすらcurrent_timeより後なら None
        if time_arr[last_idx] > ct:
            return None

        return df.iloc[last_idx]

    def _check_htf_trend_alignment(
        self,
        current_time: pd.Timestamp,
        direction: SignalType,
        htf_tfs: list[str] | None = None,
    ) -> bool:
        """上位足トレンド一致チェック + RSIフィルタ

        Args:
            current_time: 現在時刻
            direction: シグナル方向
            htf_tfs: HTFリスト（Noneの場合configデフォルト）

        Returns:
            bool: トレンドが一致し、RSIが極端でないか
        """
        aligned_score = 0.0
        check_tfs = htf_tfs or list(self.config.effective_htf_alignment_tfs)

        # プライマリTF（最初のHTF）でRSIチェック
        primary_row = None
        if check_tfs:
            primary_row = self._get_current_row(check_tfs[0], current_time)

        # RSIフィルタ（プライマリTFで判定）
        if primary_row is not None:
            rsi = primary_row.get("rsi_14")
            if rsi is not None and not pd.isna(rsi):
                # 買いシグナルで過買（RSI > 70）は回避
                if direction == SignalType.BUY and rsi > 70:
                    return False
                # 売りシグナルで過売（RSI < 30）は回避
                if direction == SignalType.SELL and rsi < 30:
                    return False

        for tf in check_tfs:
            row = self._get_current_row(tf, current_time)
            if row is None:
                continue

            sma_20 = row.get("sma_20")
            sma_50 = row.get("sma_50")
            close = row.get("close")
            macd = row.get("macd")
            macd_signal = row.get("macd_signal")

            if any(
                pd.isna(v) for v in [sma_20, sma_50, close] if v is not None
            ):
                continue

            if sma_20 is None or sma_50 is None or close is None:
                continue

            if direction == SignalType.BUY:
                # 完全上昇トレンド
                if close > sma_20 > sma_50:
                    aligned_score += 1.0
                # 短期上昇（SMA20上）
                elif close > sma_20:
                    aligned_score += 0.5
                # MACDモメンタム
                if macd is not None and macd_signal is not None:
                    if not pd.isna(macd) and not pd.isna(macd_signal):
                        if macd > macd_signal:
                            aligned_score += 0.3
            elif direction == SignalType.SELL:
                # 完全下降トレンド
                if close < sma_20 < sma_50:
                    aligned_score += 1.0
                # 短期下降（SMA20下）
                elif close < sma_20:
                    aligned_score += 0.5
                # MACDモメンタム
                if macd is not None and macd_signal is not None:
                    if not pd.isna(macd) and not pd.isna(macd_signal):
                        if macd < macd_signal:
                            aligned_score += 0.3

        # 閾値0.8（緩和）
        return aligned_score >= 0.8

    def on_trade_executed(
        self,
        timestamp: datetime,
        pnl: float | None = None,
        trade_record: TradeRecord | None = None,
    ) -> None:
        """取引実行時コールバック

        Args:
            timestamp: 取引時刻
            pnl: 損益（決済時のみ）
            trade_record: アダプティブ調整用トレード記録
        """
        self.risk_manager.record_trade(timestamp)
        if pnl is not None:
            self.risk_manager.update_pnl(pnl)
            self.state = self.state.with_pnl(pnl)
        if trade_record is not None and self._adaptive_tuner:
            self._adaptive_tuner.record_trade(trade_record)
        # エッジ検定
        if trade_record is not None and self._edge_validator:
            from autotrader.decision.unified.adaptive.edge_validator import (
                EdgeAlertLevel,
            )

            edge_status = self._edge_validator.record_trade(
                trade_record,
            )
            if self.config.edge_validator_auto_cb:
                # STOP → サーキットブレーカー発動
                if (
                    edge_status.alert_level
                    == EdgeAlertLevel.STOP
                ):
                    logger.warning(
                        "エッジSTOP → サーキットブレーカー発動",
                    )
                    self.risk_manager.trigger_circuit_breaker(
                        timestamp,
                    )
                # CRITICAL → サーキットブレーカー発動
                elif (
                    edge_status.alert_level
                    == EdgeAlertLevel.CRITICAL
                ):
                    logger.warning(
                        "エッジCRITICAL → "
                        "サーキットブレーカー発動",
                    )
                    self.risk_manager.trigger_circuit_breaker(
                        timestamp,
                    )
                # WARNING → ロット縮小フラグ設定
                elif (
                    edge_status.alert_level
                    == EdgeAlertLevel.WARNING
                ):
                    self._edge_lot_multiplier = (
                        self.config.edge_warning_lot_multiplier
                    )
                else:
                    # OK/INFO → ロット縮小解除
                    self._edge_lot_multiplier = 1.0
        # Layer 6: 連続敗戦サーキットブレーカー
        if (
            pnl is not None
            and pnl < 0
            and self.config.risk.consecutive_loss_breaker_enabled
        ):
            if (
                self.state.consecutive_losses
                >= self.config.risk.consecutive_loss_breaker_threshold
            ):
                logger.warning(
                    "%d連敗 → サーキットブレーカー発動",
                    self.state.consecutive_losses,
                )
                self.risk_manager.trigger_circuit_breaker(
                    timestamp,
                )

    def get_timeframe_signals(
        self,
        current_time: pd.Timestamp,
        candle: Candle | None = None,
    ) -> dict[str, TimeframeSignal]:
        """全時間足のシグナルを取得（デバッグ用）

        Args:
            current_time: 現在時刻
            candle: 現在のローソク足

        Returns:
            dict[str, TimeframeSignal]: 時間足別シグナル
        """
        tf_signals: dict[str, TimeframeSignal] = {}

        for tf in self.timeframes:
            row = self._get_current_row(tf, current_time)
            if row is None:
                continue

            signal = self.evaluators[tf].evaluate(row, candle)
            tf_signals[tf] = signal

        return tf_signals
