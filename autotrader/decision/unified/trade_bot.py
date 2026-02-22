"""統合トレードボット

新アーキテクチャ対応版:
- MarketRegimeDetector: レジーム検出
- TradingModeSelector: モード選択
- ModeAwareScoreConsensus: コンセンサス統合
- PositionSizer: ロット計算
- PositionManager: ポジション管理
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from autotrader.constraint.soft_guard import (
    SoftGuard,
    SoftGuardConfig,
    SoftGuardResult,
)
from autotrader.core.enums import MarketRegime, SignalType
from autotrader.core.interfaces.position_sizing import SizingContext

from .config import RiskConfig, UnifiedBotConfig
from .mode_aware_consensus import (
    ConsensusConfig,
    ConsensusResult,
    ModeAwareScoreConsensus,
    TimeframeSignal as ConsensusTimeframeSignal,
)
from .mode_selector import ModeSelectorConfig, TradingModeSelector, TradingPlan
from .position_sizer import PositionSizer, PositionSizerConfig
from .signal_consolidator import ConsolidatedSignal
from .timeframe_evaluator import TimeframeEvaluator, TimeframeSignal
from .timeframe_router import TimeframeRouter

if TYPE_CHECKING:
    from autotrader.calculator.features.regime_detector import (
        RegimeResult,
    )
    from autotrader.core.entities import Candle


@dataclass
class BotState:
    """ボット状態

    Attributes:
        equity: 現在の有効証拠金
        consecutive_losses: 連敗数
        consecutive_wins: 連勝数
        current_dd_pct: 現在のドローダウン率
        peak_equity: 最高証拠金
        daily_pnl: 日次損益
        daily_trades: 日次トレード数
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

    def update_pnl(self, pnl: float) -> None:
        """損益更新

        Args:
            pnl: 損益
        """
        self.equity += pnl
        self.daily_pnl += pnl

        if pnl > 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        elif pnl < 0:
            self.consecutive_losses += 1
            self.consecutive_wins = 0

        self.peak_equity = max(self.peak_equity, self.equity)
        if self.peak_equity > 0:
            self.current_dd_pct = (
                self.peak_equity - self.equity
            ) / self.peak_equity

    def reset_daily(self) -> None:
        """日次リセット"""
        self.daily_pnl = 0.0
        self.daily_trades = 0


class RiskManager:
    """リスク管理器

    日次損失制限、クールダウン管理を行う。
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

    def can_trade(self, timestamp: datetime) -> tuple[bool, str]:
        """取引可否チェック

        Args:
            timestamp: 現在時刻

        Returns:
            tuple[bool, str]: (取引可否, 理由)
        """
        # 日次損失制限チェック
        if self._daily_pnl < -self.config.max_daily_loss_pct:
            return False, f"日次損失制限超過({self._daily_pnl:.2%})"

        # 日次トレード件数制限チェック（0=無制限）
        limit = self.config.max_daily_trades
        if limit > 0 and self._daily_trades >= limit:
            return False, f"日次トレード件数上限({self._daily_trades}/{limit}件)"

        # クールダウンチェック
        if self._last_trade_time is not None:
            cooldown = timedelta(minutes=self.config.cooldown_minutes)
            if timestamp - self._last_trade_time < cooldown:
                remaining = int(
                    (self._last_trade_time + cooldown - timestamp)
                    .total_seconds() // 60
                )
                return False, f"クールダウン中(残{remaining}分)"

        return True, ""


class UnifiedTradeBot:
    """統合トレードボット

    全時間足を毎分評価し、最適なエントリーを選択する。
    新アーキテクチャではレジーム検出、モード選択、
    ポジションサイジングを統合。
    """

    DEFAULT_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]

    def __init__(self, config: UnifiedBotConfig | None = None):
        """初期化

        Args:
            config: ボット設定
        """
        self.config = config or UnifiedBotConfig()
        self.timeframes = self.config.timeframes or self.DEFAULT_TIMEFRAMES

        # 時間足別評価器
        self.evaluators: dict[str, TimeframeEvaluator] = {
            tf: TimeframeEvaluator(
                tf, self.config.get_evaluator_config(tf)
            )
            for tf in self.timeframes
        }

        # 新アーキテクチャコンポーネント（risk_manager を含む）
        self._init_new_components()

        # ボット状態
        self.state = BotState()

        # 市場データ
        self._market_data: dict[str, pd.DataFrame] = {}
        self._current_indices: dict[str, int] = {}

        # フロー分析（オプション）
        self._flow_analyzer: Any = None

    def _init_new_components(self) -> None:
        """新アーキテクチャコンポーネントを初期化"""
        # レジーム検出器
        from autotrader.calculator.features.regime_detector import (
            MarketRegimeDetector,
            RegimeDetectorConfig,
        )
        self.regime_detector = MarketRegimeDetector(RegimeDetectorConfig())

        # モード選択器（UNIVERSAL固定）
        self.mode_selector = TradingModeSelector(ModeSelectorConfig())

        # タイムフレームルーター
        self.tf_router = TimeframeRouter()

        # コンセンサス統合器（デモモード時は閾値を大幅に下げる）
        if self.config.demo_mode:
            self.consensus = ModeAwareScoreConsensus(
                ConsensusConfig(
                    threshold=self.config.demo_consensus_threshold,
                )
            )
        else:
            self.consensus = ModeAwareScoreConsensus(
                ConsensusConfig(
                    threshold=self.config.consensus_threshold,
                )
            )

        # ポジションサイザー（資金管理パラメータを設定から注入）
        self.position_sizer = PositionSizer(PositionSizerConfig(
            base_risk_pct=self.config.base_risk_pct,
            max_risk_pct_absolute=self.config.max_risk_pct_absolute,
            max_lot_per_trade=self.config.max_lot_per_trade,
            max_total_exposure_lot=self.config.max_total_exposure_lot,
            equity_floor_pct=self.config.equity_floor_pct,
            equity_caution_pct=self.config.equity_caution_pct,
            slippage_buffer_pips=self.config.slippage_buffer_pips,
        ))

        # ソフトガード
        self.soft_guard = SoftGuard(SoftGuardConfig())

        # 動的TF選択器（UNIVERSALモード用）
        from autotrader.decision.unified.dynamic_tf_selector import (
            DynamicTFSelector,
        )
        self._dynamic_tf_selector = DynamicTFSelector()

        # リスク管理器（デモモード時はクールダウンを排除）
        risk_config = dataclasses.replace(
            self.config.risk,
            cooldown_minutes=(
                0 if self.config.demo_mode
                else self.config.risk.cooldown_minutes
            ),
            max_daily_trades=(
                self.config.demo_max_daily_trades
                if self.config.demo_mode
                else self.config.risk.max_daily_trades
            ),
        )
        self.risk_manager = RiskManager(risk_config)


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
                self._time_arrays[tf] = (
                    df["time"].values
                )
            elif "timestamp" in df.columns:
                self._time_arrays[tf] = (
                    df["timestamp"].values
                )

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
            return self.timeframes[idx + 1:]
        except ValueError:
            return []

    def generate_signal(
        self,
        current_time: pd.Timestamp,
        candle: Candle | None = None,
    ) -> ConsolidatedSignal:
        """毎分呼び出し：全時間足評価→統合シグナル生成

        Args:
            current_time: 現在時刻
            candle: 現在のローソク足

        Returns:
            ConsolidatedSignal: 統合シグナル
        """
        return self._generate_signal_new(current_time, candle)

    def _generate_signal_new(
        self,
        current_time: pd.Timestamp,
        candle: Candle | None = None,
    ) -> ConsolidatedSignal:
        """新アーキテクチャでのシグナル生成

        Args:
            current_time: 現在時刻
            candle: 現在のローソク足

        Returns:
            ConsolidatedSignal: 統合シグナル
        """
        # 日次リセット
        py_time = current_time.to_pydatetime()
        self.risk_manager.reset_daily(py_time)

        # レジーム検出（H1データを使用）
        regime_result = self._detect_regime(current_time)

        # モード・プラン選択（時間帯考慮）
        htf_alignment = self._get_htf_alignment(current_time)
        hour_utc = current_time.hour if hasattr(current_time, 'hour') else None
        plan = self.mode_selector.select(
            regime=regime_result.regime,
            volatility_level=regime_result.volatility_level,
            htf_alignment=htf_alignment,
            hour_utc=hour_utc,
        )

        # UNIVERSALモード固定: 動的TF選択（tf_signals取得後に実行）

        # 分析用に最後のモード/レジームを保持
        self._last_mode = plan.mode.value
        self._last_regime = regime_result.regime.value

        # TFセット取得
        tf_set = self.tf_router.route(plan)

        # リスク管理チェック
        can_trade, reason = self.risk_manager.can_trade(py_time)
        if not can_trade:
            if self._flow_analyzer:
                from autotrader.core.diagnostics import (
                    SignalStepRecord,
                )
                self._flow_analyzer.collect(SignalStepRecord(
                    timestamp=str(current_time),
                    regime=regime_result.regime.value,
                    volatility=regime_result.volatility_level,
                    mode=plan.mode.value,
                    primary_tf=plan.primary_tf,
                    risk_passed=False,
                    risk_reason=reason,
                    consensus_score=0.0,
                    consensus_threshold=0.0,
                    final_direction="HOLD",
                    hold_reason=f"リスク管理: {reason}",
                ))
            return self._hold_signal(reason)

        # 全TFを評価（アナリティクス表示用）
        # config.timeframes 全TFのスコアを計算してWebUIに表示する。
        # コンセンサス計算はモード別TFセットのみ使用（役割ベース重み付け維持）。
        tf_signals: dict[str, TimeframeSignal] = {}
        consensus_signals: dict[str, ConsensusTimeframeSignal] = {}

        for tf in self.timeframes:
            if tf not in self.evaluators:
                continue
            row = self._get_current_row(tf, current_time)
            if row is None:
                continue

            signal = self.evaluators[tf].evaluate(
                row, candle, plan, current_time,
            )
            tf_signals[tf] = signal

        # UNIVERSALモード: 全TFシグナル取得後に動的TF選択でplanを更新
        if tf_signals:
            # コンセンサス方向を支配方向として渡す（先のコンセンサス前のため
            # None で全TF評価）
            _dynamic_result = self._dynamic_tf_selector.select(tf_signals)
            plan = dataclasses.replace(
                plan,
                dynamic_entry_tf=_dynamic_result.selected_entry_tf,
                max_holding_bars=_dynamic_result.max_holding_bars,
                tp_sl_ratio_range=_dynamic_result.tp_sl_ratio_range,
            )

        # コンセンサスはモード別TFセットのみ対象（役割重みを保持）
        for tf in tf_set.all_tfs:
            if tf not in tf_signals:
                continue
            signal = tf_signals[tf]
            # 強度を0-1に正規化（net_strengthは-1から1の範囲）
            strength = (
                abs(signal.net_strength)
                if signal.direction != SignalType.HOLD
                else 0.0
            )
            consensus_signals[tf] = ConsensusTimeframeSignal(
                direction=signal.direction,
                strength=strength,
                sl_pips=signal.sl_pips,
                tp_pips=signal.tp_pips,
            )

        # コンセンサス統合
        consensus = self.consensus.consolidate(consensus_signals, plan)

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
                self._flow_analyzer.collect(SignalStepRecord(
                    timestamp=str(current_time),
                    regime=regime_result.regime.value,
                    volatility=regime_result.volatility_level,
                    mode=plan.mode.value,
                    primary_tf=plan.primary_tf,
                    risk_passed=True,
                    tf_details=tf_detail,
                    consensus_direction="HOLD",
                    consensus_score=consensus.score,
                    consensus_threshold=consensus.threshold,
                    consensus_passed=False,
                    final_direction="HOLD",
                    hold_reason=consensus.reasoning,
                ))
            return self._hold_with_analysis(
                consensus.reasoning,
                plan, tf_signals, consensus,
                regime_result, htf_alignment,
            )

        # SoftGuardチェック（デモモードでも情報取得: 出力データに使用）
        # ATR比率・絶対ATRを計算してボラティリティ状態をSoftGuardに渡す
        _primary_atr_row = self._get_current_row(
            plan.primary_tf, current_time,
        )
        _atr_ratio = 1.0
        _primary_atr_abs = None  # 絶対ATR値（primary_tf基準）
        if _primary_atr_row is not None:
            _atr = _primary_atr_row.get("atr_14")
            _atr_ma = _primary_atr_row.get("atr_ma_20")
            if (
                _atr is not None and _atr_ma is not None
                and not pd.isna(_atr) and not pd.isna(_atr_ma)
                and _atr_ma > 0
            ):
                _atr_ratio = float(_atr) / float(_atr_ma)
            if _atr is not None and not pd.isna(_atr):
                _primary_atr_abs = float(_atr)
        # SWING低ボラフィルター用: entry_tf(H1)のATRを取得
        # entry_atr(CSV列)はH1由来のため、スケールを合わせる
        _entry_tf_atr_abs = None
        _entry_tf_row = self._get_current_row(
            plan.entry_tf, current_time,
        )
        if _entry_tf_row is not None:
            _e_atr = _entry_tf_row.get("atr_14")
            if _e_atr is not None and not pd.isna(_e_atr):
                _entry_tf_atr_abs = float(_e_atr)
        sg_context = {
            "spread_pips": self._get_spread_pips(current_time),
            "current_time": current_time.to_pydatetime(),
            "atr_ratio": _atr_ratio,
            "recent_losses": self.state.consecutive_losses,
            "trend_strength": regime_result.trend_strength,
            "mtf_alignment": (
                "aligned" if htf_alignment >= 0.3 else "mixed"
            ),
        }
        sg_result = self.soft_guard.check(sg_context, is_entry=True)

        # セッションフィルター
        hour_utc = current_time.hour if hasattr(
            current_time, 'hour'
        ) else current_time.to_pydatetime().hour


        def _filt_hold(reason: str) -> ConsolidatedSignal:
            """フィルターHOLD用ローカルヘルパー"""
            return self._hold_with_analysis(
                reason, plan, tf_signals, consensus,
                regime_result, htf_alignment, sg_result,
            )

        # デモモード: コンセンサス閾値のみ。追加フィルタースキップ
        if not self.config.demo_mode:
            # 上位足トレンドフィルター（必須条件）
            if not self._check_htf_trend_alignment(
                current_time, consensus.direction,
            ):
                if self._flow_analyzer:
                    from autotrader.core.diagnostics import (
                        SignalStepRecord,
                    )
                    self._flow_analyzer.collect(SignalStepRecord(
                        timestamp=str(current_time),
                        regime=regime_result.regime.value,
                        volatility=regime_result.volatility_level,
                        mode=plan.mode.value,
                        primary_tf=plan.primary_tf,
                        risk_passed=True,
                        consensus_direction=consensus.direction.value,
                        consensus_score=consensus.score,
                        consensus_threshold=consensus.threshold,
                        consensus_passed=True,
                        htf_passed=False,
                        htf_direction=consensus.direction.value,
                        final_direction="HOLD",
                        hold_reason=(
                            f"HTFトレンド不一致"
                            f"({consensus.direction.value})"
                        ),
                    ))
                return _filt_hold(
                    f"HTFトレンド不一致({consensus.direction.value})"
                )

            # SoftGuardペナルティによるブロック（常時有効）
            if sg_result.total_penalty >= 0.8:
                return _filt_hold(
                    f"SoftGuardブロック: penalty="
                    f"{sg_result.total_penalty:.2f}"
                )

            # LONDONオフ時間ブロック（hour=7はLONDON境界）
            if hour_utc == 7 and sg_result.total_penalty > 0:
                return _filt_hold(
                    f"LONDONオフ時間ブロック: hour={hour_utc}, "
                    f"penalty={sg_result.total_penalty:.2f}"
                )

            # TOKYOオフ時間フィルター（閾値6.6）
            if (
                4 <= hour_utc <= 6
                and sg_result.total_penalty > 0
                and consensus.score < 6.6
            ):
                return _filt_hold(
                    f"TOKYOオフ時間フィルター: hour={hour_utc}, "
                    f"score={consensus.score:.1f}<6.6"
                )

            # LOW_VOL制限: スコア品質要件を高める
            # 低ボラティリティ環境はスプレッド影響が大きく不利
            if (
                regime_result.regime == MarketRegime.LOW_VOL
                and consensus.score < consensus.threshold + 1.5
            ):
                return _filt_hold(
                    f"LOW_VOL制限: score={consensus.score:.2f}"
                    f" < threshold+1.5={consensus.threshold + 1.5:.2f}"
                )

            # RANGE + トレンド弱制限
            if (
                regime_result.regime == MarketRegime.RANGE
                and regime_result.trend_strength < 0.3
            ):
                return _filt_hold(
                    f"RANGE制限: trend_strength="
                    f"{regime_result.trend_strength:.2f}"
                )

            # RANGE ペナルティ+低ボラ制限
            if (
                regime_result.regime == MarketRegime.RANGE
                and sg_result.total_penalty > 0
            ):
                _primary_row = self._get_current_row(
                    plan.primary_tf, current_time,
                )
                if _primary_row is not None:
                    _bb_w = _primary_row.get("bb_width")
                    if (
                        _bb_w is not None
                        and not pd.isna(_bb_w)
                        and float(_bb_w)
                        < self.config.range_day_bbw_threshold
                    ):
                        return _filt_hold(
                            f"RANGE低ボラ制限: "
                            f"penalty="
                            f"{sg_result.total_penalty:.2f}"
                            f", bb_width="
                            f"{float(_bb_w):.4f}"
                            f"<{self.config.range_day_bbw_threshold}"
                        )

            # Weak Hours RANGEフィルター（JST 18-21 = UTC 9-12）
            if (
                self.config.weak_hours_enabled
                and 9 <= hour_utc <= 12
                and regime_result.regime == MarketRegime.RANGE
                and consensus.score < consensus.threshold
                    + self.config.weak_hours_score_premium
            ):
                _wh_threshold = (
                    consensus.threshold
                    + self.config.weak_hours_score_premium
                )
                return _filt_hold(
                    f"WeakHours RANGE: hour={hour_utc}, "
                    f"score={consensus.score:.1f}"
                    f"<{_wh_threshold:.1f}"
                )

            # 東京深夜フィルター（JST 02-06 = UTC 17-21）
            # 東京深夜は流動性低下でトレンド追従が困難
            if (
                17 <= hour_utc <= 21
                and regime_result.regime == MarketRegime.TREND
                and consensus.score < consensus.threshold + 0.3
            ):
                _tn_threshold = consensus.threshold + 0.3
                return _filt_hold(
                    f"東京深夜TREND: hour={hour_utc}, "
                    f"score={consensus.score:.1f}"
                    f"<{_tn_threshold:.1f}"
                )

            # RANGEスコアプレミアム（低スコア帯を除外）
            _score_premium = self.config.range_day_score_premium
            if (
                _score_premium > 0
                and regime_result.regime == MarketRegime.RANGE
                and consensus.score
                < consensus.threshold + _score_premium
            ):
                return _filt_hold(
                    f"RANGEスコアプレミアム: "
                    f"score={consensus.score:.1f}"
                    f"<{consensus.threshold + _score_premium:.1f}"
                )

            # TOKYO低ペナルティ帯: 閾値+0.2
            if (
                4 <= hour_utc <= 6
                and 0 < sg_result.total_penalty <= 0.2
                and consensus.score < consensus.threshold + 0.2
            ):
                return _filt_hold(
                    f"TOKYO低penalty閾値: penalty="
                    f"{sg_result.total_penalty:.2f}, "
                    f"score={consensus.score:.1f}"
                    f"<{consensus.threshold + 0.2:.1f}"
                )

            # MACDスロープ逆方向フィルター
            _primary_sig = tf_signals.get(plan.primary_tf)
            if (
                _primary_sig
                and _primary_sig.score_breakdown
            ):
                _macd_slope = (
                    _primary_sig.score_breakdown.macd_slope
                )
                if _macd_slope <= -2.0:
                    return _filt_hold(
                        f"MACDスロープ逆方向: "
                        f"{_macd_slope:.1f}"
                    )

        # SL/TP計算（primary_tf由来）
        primary_signal = tf_signals.get(plan.primary_tf)
        if primary_signal is None:
            if self._flow_analyzer:
                from autotrader.core.diagnostics import (
                    SignalStepRecord,
                )
                self._flow_analyzer.collect(SignalStepRecord(
                    timestamp=str(current_time),
                    regime=regime_result.regime.value,
                    volatility=regime_result.volatility_level,
                    mode=plan.mode.value,
                    primary_tf=plan.primary_tf,
                    risk_passed=True,
                    consensus_direction=consensus.direction.value,
                    consensus_score=consensus.score,
                    consensus_threshold=consensus.threshold,
                    consensus_passed=True,
                    htf_passed=True,
                    final_direction="HOLD",
                    hold_reason="primary_tfデータなし",
                ))
            return _filt_hold("primary_tfデータなし")

        sl_pips = primary_signal.sl_pips
        tp_sl_ratio = (
            plan.get_recommended_tp_sl_ratio()
            * self.config.tp_sl_ratio
        )
        tp_pips = sl_pips * tp_sl_ratio

        # ポジションサイジング
        lot = 0.01
        if self.config.enable_position_sizing:
            confidence = (
                consensus.score / consensus.threshold
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
                open_same_direction_lot=(
                    self.state.open_same_direction_lot
                ),
            )
            sizing_result = self.position_sizer.calculate(sizing_context)
            if sizing_result.blocked:
                return _filt_hold(
                    f"資金管理: {sizing_result.reasoning}"
                )
            lot = sizing_result.lot

        rationale = (
            f"{consensus.reasoning}, "
            f"mode={plan.mode.value}, "
            f"lot={lot:.2f}"
        )

        # フロー分析: シグナル発生記録
        if self._flow_analyzer:
            from autotrader.core.diagnostics import (
                SignalStepRecord,
            )
            self._flow_analyzer.collect(SignalStepRecord(
                timestamp=str(current_time),
                regime=regime_result.regime.value,
                volatility=regime_result.volatility_level,
                mode=plan.mode.value,
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
            ))

        # TF別スコア内訳を集約
        tf_breakdowns: dict[str, dict[str, float]] = {}
        for tf_name, sig in tf_signals.items():
            if sig.score_breakdown is not None:
                tf_breakdowns[tf_name] = sig.score_breakdown.to_dict()

        # TF別方向を集約（UI表示用）
        tf_directions: dict[str, str] = {
            tf: sig.direction.value
            for tf, sig in tf_signals.items()
        }

        # 返却用confidence計算
        ret_confidence = min(
            consensus.score / consensus.threshold,
            1.0,
        )

        # strategy_id構築（モード_選択理由）
        _strategy_id = (
            f"{plan.mode.value}_{plan.selection_reason}"
            if plan.selection_reason
            else plan.mode.value
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
            mode=plan.mode.value,
            consensus_score=consensus.score,
            tf_score_breakdowns=tf_breakdowns,
            tf_directions=tf_directions,
            strategy_id=_strategy_id,
            entry_threshold=consensus.threshold,
            htf_alignment=htf_alignment,
            penalty_total=sg_result.total_penalty,
            penalty_breakdown={
                r.value: v
                for r, v in sg_result.penalties.items()
            },
            trend_strength=regime_result.trend_strength,
            lot=lot,
        )

    def _get_spread_pips(self, current_time: pd.Timestamp) -> float:
        """スプレッドを取得（簡易版）

        Args:
            current_time: 現在時刻

        Returns:
            float: スプレッド（pips）
        """
        # TODO: 実際のスプレッドデータがあれば使用
        # ここでは固定値を返す
        return 1.5

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
                    result[tf] = df.iloc[:idx + 1]
            else:
                result[tf] = df

        return result

    def _detect_regime(self, current_time: pd.Timestamp) -> "RegimeResult":
        """レジームを検出

        Args:
            current_time: 現在時刻

        Returns:
            RegimeResult: レジーム判定結果
        """
        from autotrader.calculator.features.regime_detector import RegimeResult

        # H1データを使用
        row = self._get_current_row("H1", current_time)
        if row is None:
            return RegimeResult(
                regime=MarketRegime.RANGE,
                trend_strength=0.0,
                volatility_level=1.0,
                adx=0.0,
                confidence=0.0,
                reasoning="H1データなし",
            )

        return self.regime_detector.detect_from_row(row)

    def _get_htf_alignment(self, current_time: pd.Timestamp) -> float:
        """HTF整合度を取得

        Args:
            current_time: 現在時刻

        Returns:
            float: HTF整合度（-1から1）
        """
        alignment_scores = []

        for tf in ["H4", "D1"]:
            row = self._get_current_row(tf, current_time)
            if row is None:
                continue

            ma_alignment = row.get("ma_alignment")
            if ma_alignment is not None and not pd.isna(ma_alignment):
                alignment_scores.append(float(ma_alignment))

        if not alignment_scores:
            return 0.0
        return sum(alignment_scores) / len(alignment_scores)

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
                tf_breakdowns[tf_name] = (
                    sig.score_breakdown.to_dict()
                )

        # TF別方向を集約（UI表示用）
        tf_directions: dict[str, str] = {
            tf: sig.direction.value
            for tf, sig in tf_signals.items()
        }

        return ConsolidatedSignal(
            direction=SignalType.HOLD,
            confidence=0.0,
            primary_tf=plan.primary_tf,
            aligned_tfs=consensus.aligned_tfs,
            sl_pips=0.0,
            tp_pips=0.0,
            rationale=reason,
            scores={
                tf: sig.confidence
                for tf, sig in tf_signals.items()
            },
            regime=regime_result.regime.value,
            mode=plan.mode.value,
            consensus_score=consensus.score,
            tf_score_breakdowns=tf_breakdowns,
            tf_directions=tf_directions,
            entry_threshold=consensus.threshold,
            htf_alignment=htf_alignment,
            penalty_total=(
                sg_result.total_penalty
                if sg_result else 0.0
            ),
            penalty_breakdown=(
                {
                    r.value: v
                    for r, v in sg_result.penalties.items()
                }
                if sg_result else {}
            ),
            trend_strength=regime_result.trend_strength,
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
    ) -> bool:
        """上位足トレンド一致チェック

        Args:
            current_time: 現在時刻
            direction: シグナル方向

        Returns:
            bool: トレンドが一致しているか
        """
        aligned_score = 0.0
        check_tfs = ["H4", "D1"]

        for tf in check_tfs:
            row = self._get_current_row(tf, current_time)
            if row is None:
                continue

            sma_20 = row.get("sma_20")
            sma_50 = row.get("sma_50")
            close = row.get("close")
            macd = row.get("macd")
            macd_signal = row.get("macd_signal")

            if any(pd.isna(v) for v in [sma_20, sma_50, close]
                   if v is not None):
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

  # データなしは通過

        rsi = row.get("rsi_14")
        if rsi is None or pd.isna(rsi):
            return True

        # 買いシグナルで過買（RSI > 70）は回避
        if direction == SignalType.BUY and rsi > 70:
            return False

        # 売りシグナルで過売（RSI < 30）は回避
        if direction == SignalType.SELL and rsi < 30:
            return False

        return True

    def on_trade_executed(
        self,
        timestamp: datetime,
        pnl: float | None = None,
    ) -> None:
        """取引実行時コールバック

        Args:
            timestamp: 取引時刻
            pnl: 損益（決済時のみ）
        """
        self.risk_manager.record_trade(timestamp)
        if pnl is not None:
            self.risk_manager.update_pnl(pnl)
            self.state.update_pnl(pnl)

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
