"""シグナル生成パイプライン

_generate_signal_new の770行モノリシックメソッドを
6ステップに分割し、データフローを PipelineContext で明示化。

ロジック変更ゼロ: コードの移動のみ。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import pandas as pd

from autotrader.constraint.soft_guard import SoftGuardResult
from autotrader.core.enums import MarketRegime, SignalType
from autotrader.core.interfaces.position_sizing import SizingContext

from ..scoring.consensus import (
    ConsensusResult,
    TimeframeSignal as ConsensusTimeframeSignal,
)
from ..mode_selector import TradingPlan
from ..scoring.consolidator import ConsolidatedSignal
from ..scoring.timeframe_evaluator import TimeframeSignal

if TYPE_CHECKING:
    from autotrader.adapters.fundamental.schemas import (
        FundamentalContext,
        FundamentalMemorySnapshot,
    )
    from autotrader.calculator.features.regime_detector import (
        RegimeResult,
    )
    from autotrader.core.entities import Candle
    from autotrader.decision.unified.adaptive import (
        AdaptiveOverrides,
    )
    from autotrader.decision.unified.fundamental_assessor import (
        FundamentalAssessment,
    )
    from autotrader.decision.unified.timeframe_router import (
        TimeframeSet,
    )


@dataclass
class PipelineContext:
    """パイプラインステップ間で共有するコンテキスト

    Attributes:
        current_time: 現在時刻
        candle: 現在のローソク足
        fundamental_ctx: ファンダメンタルコンテキスト
        fundamental_memory: メモリスナップショット
    """

    # 入力（不変）
    current_time: pd.Timestamp
    candle: Candle | None = None
    fundamental_ctx: FundamentalContext | None = None
    fundamental_memory: FundamentalMemorySnapshot | None = None

    # ステップ間で蓄積されるデータ
    plan: TradingPlan | None = None
    tf_signals: dict[str, TimeframeSignal] = field(
        default_factory=dict,
    )
    consensus_signals: dict[str, ConsensusTimeframeSignal] = field(
        default_factory=dict,
    )
    regime_result: RegimeResult | None = None
    htf_alignment: float = 0.0
    regime_tf: str = ""
    htf_tfs: list[str] = field(default_factory=list)
    tf_set: TimeframeSet | None = None
    consensus: ConsensusResult | None = None
    fund_assessment: FundamentalAssessment | None = None
    fund_boosted: bool = False
    overrides: AdaptiveOverrides | None = None
    sg_result: SoftGuardResult | None = None
    bca_penalty: float = 0.0
    hour_utc: int = 0
    atr_ratio: float = 1.0
    primary_atr_abs: float | None = None
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    lot: float = 0.01

    # 早期終了用
    should_abort: bool = False
    abort_signal: ConsolidatedSignal | None = None


class PipelineStep(Protocol):
    """パイプラインの1ステップ"""

    name: str

    def execute(
        self,
        ctx: PipelineContext,
        bot: Any,
    ) -> PipelineContext:
        """コンテキストを受け取り、処理後のコンテキストを返す。

        早期終了する場合は ctx.should_abort=True +
        ctx.abort_signal を設定。
        """
        ...


class SignalPipeline:
    """シグナル生成パイプライン

    ステップを順次実行し、早期終了が発生したら
    即座にHOLDシグナルを返す。
    """

    def __init__(
        self,
        steps: list[PipelineStep],
    ) -> None:
        self._steps = steps

    def execute(
        self,
        ctx: PipelineContext,
        bot: Any,
    ) -> ConsolidatedSignal:
        """パイプラインを実行

        Args:
            ctx: パイプラインコンテキスト
            bot: UnifiedTradeBot インスタンス

        Returns:
            ConsolidatedSignal: 統合シグナル
        """
        for step in self._steps:
            ctx = step.execute(ctx, bot)
            if ctx.should_abort:
                return ctx.abort_signal
        # 最終ステップ完了 → abort_signal に最終シグナルが入る
        return ctx.abort_signal


# ============================================================
# Step 1: RiskCheckStep
# ============================================================


class RiskCheckStep:
    """日次リセット + リスク管理チェック + セッション待機"""

    name = "risk_check"

    def execute(
        self,
        ctx: PipelineContext,
        bot: Any,
    ) -> PipelineContext:
        """リスクチェックを実行"""
        # 1. 日次リセット
        py_time = ctx.current_time.to_pydatetime()
        bot.risk_manager.reset_daily(py_time)

        # 2. リスク管理チェック
        can_trade, reason = bot.risk_manager.can_trade(py_time)
        if not can_trade:
            ctx.should_abort = True
            ctx.abort_signal = bot._hold_signal(reason)
            return ctx

        # 2b. セッション切替待機チェック
        session_result = (
            bot._session_transition_filter.check(py_time)
        )
        if session_result.should_filter:
            ctx.should_abort = True
            ctx.abort_signal = bot._hold_signal(
                session_result.reason,
            )
            return ctx

        return ctx


# ============================================================
# Step 2: TimeframeEvalStep
# ============================================================


class TimeframeEvalStep:
    """モード選択 + TF評価 + 動的TF選択 + レジーム + HTF"""

    name = "timeframe_eval"

    def execute(
        self,
        ctx: PipelineContext,
        bot: Any,
    ) -> PipelineContext:
        """時間足評価を実行"""
        # 3. 初期プラン
        plan = bot.mode_selector.select()

        # 4. 全TF評価 → tf_signals
        tf_signals: dict[str, TimeframeSignal] = {}

        _PARALLEL_TF_THRESHOLD = 12
        _eval_tfs = [
            tf for tf in bot.timeframes
            if tf in bot.evaluators
        ]

        if len(_eval_tfs) > _PARALLEL_TF_THRESHOLD:
            tf_signals = bot._evaluate_tfs_parallel(
                _eval_tfs, ctx.current_time, ctx.candle, plan,
            )
        else:
            for tf in _eval_tfs:
                row = bot._get_current_row(
                    tf, ctx.current_time,
                )
                if row is None:
                    continue
                signal = bot.evaluators[tf].evaluate(
                    row, ctx.candle, plan, ctx.current_time,
                )
                tf_signals[tf] = signal

        # 5. 動的TF選択 → 全TFロール決定
        if tf_signals:
            _dynamic_result = (
                bot._dynamic_tf_selector.select(tf_signals)
            )
            plan = dataclasses.replace(
                plan,
                primary_tf=(
                    _dynamic_result.selected_primary_tf
                ),
                manage_tf=(
                    _dynamic_result.selected_manage_tf
                ),
                dynamic_entry_tf=(
                    _dynamic_result.selected_entry_tf
                ),
                max_holding_bars=(
                    _dynamic_result.max_holding_bars
                ),
                tp_sl_ratio_range=(
                    _dynamic_result.tp_sl_ratio_range
                ),
            )
            _regime_tf = _dynamic_result.selected_regime_tf
            _htf_tfs = (
                _dynamic_result.selected_htf_alignment_tfs
            )
        else:
            _regime_tf = bot.config.regime_detection_tf
            _htf_tfs = list(bot.config.htf_alignment_tfs)

        # 6. レジーム検出
        regime_result = bot._detect_regime(
            ctx.current_time, regime_tf=_regime_tf,
        )

        # 7. HTF整合度
        htf_alignment = bot._get_htf_alignment(
            ctx.current_time, htf_tfs=_htf_tfs,
        )

        # 分析用に最後のモード/レジームを保持
        bot._last_mode = plan.mode
        bot._last_regime = regime_result.regime.value

        # 8. TFルーティング
        tf_set = bot.tf_router.route(plan)

        # コンテキストに保存
        ctx.plan = plan
        ctx.tf_signals = tf_signals
        ctx.regime_result = regime_result
        ctx.htf_alignment = htf_alignment
        ctx.regime_tf = _regime_tf
        ctx.htf_tfs = _htf_tfs
        ctx.tf_set = tf_set

        return ctx


# ============================================================
# Step 3: ConsensusStep
# ============================================================


class ConsensusStep:
    """コンセンサス統合 + ファンダメンタル評価"""

    name = "consensus"

    def execute(
        self,
        ctx: PipelineContext,
        bot: Any,
    ) -> PipelineContext:
        """コンセンサス統合を実行"""
        plan = ctx.plan
        tf_signals = ctx.tf_signals
        tf_set = ctx.tf_set
        regime_result = ctx.regime_result
        htf_alignment = ctx.htf_alignment
        current_time = ctx.current_time

        # 9. コンセンサスはモード別TFセットのみ対象
        consensus_signals: dict[
            str, ConsensusTimeframeSignal
        ] = {}
        for tf in tf_set.all_tfs:
            if tf not in tf_signals:
                continue
            signal = tf_signals[tf]
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
        ctx.consensus_signals = consensus_signals

        # Phase 2b: ファンダメンタル評価
        _fund_assessment = bot._assess_fundamental(
            ctx.fundamental_ctx, ctx.fundamental_memory,
        )
        bot._last_fundamental_assessment = _fund_assessment
        ctx.fund_assessment = _fund_assessment

        # アダプティブオーバーライド取得
        from autotrader.decision.unified.adaptive import (
            AdaptiveOverrides,
        )
        _overrides = (
            bot._adaptive_tuner.get_overrides()
            if bot._adaptive_tuner
            else AdaptiveOverrides()
        )
        ctx.overrides = _overrides

        # コンセンサス統合（閾値オーバーライド適用）
        _threshold_override = None
        _base_threshold = bot.consensus.threshold
        if _overrides.consensus_threshold_delta != 0.0:
            _base_threshold = (
                _base_threshold
                + _overrides.consensus_threshold_delta
            )
        # レジーム別閾値調整
        if (
            bot.config.regime_threshold_enabled
            and regime_result.regime == MarketRegime.TREND
        ):
            _base_threshold = (
                _base_threshold
                + bot.config.regime_trend_threshold_add
            )
        # BREAKOUT時の閾値調整
        if (
            bot.config.regime_breakout_enabled
            and regime_result.regime == MarketRegime.BREAKOUT
        ):
            _base_threshold = (
                _base_threshold
                + bot.config.regime_breakout_threshold_add
            )
        # HTFスコア不一致フィルター
        if (
            bot.config.htf_score_filter_enabled
            and htf_alignment
            <= bot.config.htf_score_filter_min_alignment
        ):
            _base_threshold = (
                _base_threshold
                + bot.config.htf_score_filter_threshold_add
            )
        if _base_threshold != bot.consensus.threshold:
            _threshold_override = _base_threshold
        consensus = bot.consensus.consolidate(
            consensus_signals, plan,
            threshold_override=_threshold_override,
        )

        # Phase 2b: コンビクションブースト救済
        _fund_boosted = False
        if (
            consensus.direction == SignalType.HOLD
            and _fund_assessment is not None
            and consensus.score > 0
        ):
            _prelim_dir = (
                SignalType.BUY
                if consensus.buy_score
                > consensus.sell_score
                else (
                    SignalType.SELL
                    if consensus.sell_score
                    > consensus.buy_score
                    else SignalType.HOLD
                )
            )
            if _prelim_dir != SignalType.HOLD:
                _boost_sign = (
                    1.0
                    if _prelim_dir == SignalType.BUY
                    else -1.0
                )
                _boost_adj = (
                    _fund_assessment
                    .get_threshold_adjustment(
                        signal_direction=_boost_sign,
                    )
                )
                if _boost_adj < 0:
                    _boosted_th = (
                        consensus.threshold + _boost_adj
                    )
                    if consensus.score >= _boosted_th:
                        _fund_boosted = True
                        consensus = ConsensusResult(
                            direction=_prelim_dir,
                            score=consensus.score,
                            threshold=_boosted_th,
                            aligned_tfs=(
                                consensus.aligned_tfs
                            ),
                            reasoning=(
                                "ファンダブースト: "
                                f"score="
                                f"{consensus.score:.2f}"
                                f"≥{_boosted_th:.2f}"
                                f"(adj="
                                f"{_boost_adj:+.2f})"
                            ),
                            buy_score=(
                                consensus.buy_score
                            ),
                            sell_score=(
                                consensus.sell_score
                            ),
                            dynamic_entry_tf=(
                                consensus
                                .dynamic_entry_tf
                            ),
                        )

        # HOLD判定
        if consensus.direction == SignalType.HOLD:
            if bot._flow_analyzer:
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
                bot._flow_analyzer.collect(
                    SignalStepRecord(
                        timestamp=str(current_time),
                        regime=regime_result.regime.value,
                        volatility=(
                            regime_result.volatility_level
                        ),
                        mode=plan.mode,
                        primary_tf=plan.primary_tf,
                        risk_passed=True,
                        tf_details=tf_detail,
                        consensus_direction="HOLD",
                        consensus_score=consensus.score,
                        consensus_threshold=(
                            consensus.threshold
                        ),
                        consensus_passed=False,
                        final_direction="HOLD",
                        hold_reason=consensus.reasoning,
                    ),
                )
            ctx.should_abort = True
            ctx.abort_signal = bot._hold_with_analysis(
                consensus.reasoning,
                plan, tf_signals, consensus,
                regime_result, htf_alignment,
            )
            return ctx

        ctx.consensus = consensus
        ctx.fund_boosted = _fund_boosted

        return ctx


# ============================================================
# Step 4: EdgeAssessmentStep
# ============================================================


class EdgeAssessmentStep:
    """BCA方向性エッジ + ファンダメンタルフィルター"""

    name = "edge_assessment"

    def execute(
        self,
        ctx: PipelineContext,
        bot: Any,
    ) -> PipelineContext:
        """エッジ評価を実行"""
        consensus = ctx.consensus
        tf_signals = ctx.tf_signals
        tf_set = ctx.tf_set
        plan = ctx.plan
        regime_result = ctx.regime_result
        htf_alignment = ctx.htf_alignment
        _fund_assessment = ctx.fund_assessment

        # BCA: 方向性エッジ評価
        _bca_penalty = 0.0
        if bot._edge_assessor is not None:
            _edge_result = bot._edge_assessor.assess(
                consensus, tf_signals, tf_set,
            )
            if not _edge_result.passed:
                ctx.should_abort = True
                ctx.abort_signal = bot._hold_with_analysis(
                    _edge_result.reasoning,
                    plan, tf_signals, consensus,
                    regime_result, htf_alignment,
                )
                return ctx
            _bca_penalty = _edge_result.penalty
        ctx.bca_penalty = _bca_penalty

        # Phase 2b: ファンダメンタル方向フィルター
        if (
            _fund_assessment is not None
            and not ctx.fund_boosted
        ):
            _dir_sign = (
                1.0
                if consensus.direction == SignalType.BUY
                else -1.0
            )
            _fund_adj = (
                _fund_assessment.get_threshold_adjustment(
                    signal_direction=_dir_sign,
                )
            )
            _effective_threshold = (
                consensus.threshold + _fund_adj
            )
            if consensus.score < _effective_threshold:
                ctx.should_abort = True
                ctx.abort_signal = bot._hold_with_analysis(
                    f"ファンダフィルター: "
                    f"bias="
                    f"{_fund_assessment.effective_bias:+.2f}"
                    f", adj={_fund_adj:+.1f}"
                    f", score={consensus.score:.1f}"
                    f"<{_effective_threshold:.1f}",
                    plan, tf_signals, consensus,
                    regime_result, htf_alignment,
                )
                return ctx

        return ctx


# ============================================================
# Step 5: FilterStep
# ============================================================


class FilterStep:
    """SoftGuard + 各種時間帯フィルター"""

    name = "filter"

    def execute(
        self,
        ctx: PipelineContext,
        bot: Any,
    ) -> PipelineContext:
        """フィルター群を実行"""
        consensus = ctx.consensus
        tf_signals = ctx.tf_signals
        plan = ctx.plan
        regime_result = ctx.regime_result
        htf_alignment = ctx.htf_alignment
        current_time = ctx.current_time
        _overrides = ctx.overrides
        _fund_assessment = ctx.fund_assessment
        _bca_penalty = ctx.bca_penalty
        _htf_tfs = ctx.htf_tfs

        # SoftGuardチェック
        _primary_atr_row = bot._get_current_row(
            plan.primary_tf, current_time,
        )
        _atr_ratio = 1.0
        _primary_atr_abs = None
        if _primary_atr_row is not None:
            _atr = _primary_atr_row.get("atr_14")
            _atr_ma = _primary_atr_row.get("atr_ma_20")
            if (
                _atr is not None and _atr_ma is not None
                and not pd.isna(_atr)
                and not pd.isna(_atr_ma)
                and _atr_ma > 0
            ):
                _atr_ratio = float(_atr) / float(_atr_ma)
            if _atr is not None and not pd.isna(_atr):
                _primary_atr_abs = float(_atr)

        _entry_tf_atr_abs = None
        _entry_tf_row = bot._get_current_row(
            plan.entry_tf, current_time,
        )
        if _entry_tf_row is not None:
            _e_atr = _entry_tf_row.get("atr_14")
            if _e_atr is not None and not pd.isna(_e_atr):
                _entry_tf_atr_abs = float(_e_atr)

        # ボリューム比率（エントリーTFから取得）
        _vol_ratio: float | None = None
        if (
            bot.config.volume_filter_enabled
            and _entry_tf_row is not None
        ):
            _vr = _entry_tf_row.get("volume_ratio")
            if _vr is not None and not pd.isna(_vr):
                _vol_ratio = float(_vr)

        sg_context = {
            "spread_pips": bot._get_spread_pips(
                current_time,
            ),
            "current_time": current_time.to_pydatetime(),
            "atr_ratio": _atr_ratio,
            "recent_losses": bot.state.consecutive_losses,
            "trend_strength": regime_result.trend_strength,
            "mtf_alignment": (
                "aligned"
                if htf_alignment >= 0.3
                else "mixed"
            ),
            "volume_ratio": _vol_ratio,
            "volume_filter_enabled": (
                bot.config.volume_filter_enabled
            ),
            "volume_filter_threshold": (
                bot.config.volume_filter_threshold
            ),
            "volume_filter_penalty": (
                bot.config.volume_filter_penalty
            ),
        }
        sg_result = bot.soft_guard.check(
            sg_context,
            is_entry=True,
            fundamental_assessment=(
                _fund_assessment
                if bot.config.fundamental_softguard_enabled
                else None
            ),
        )

        # BCAペナルティをSoftGuard結果に加算
        if _bca_penalty > 0:
            sg_result = dataclasses.replace(
                sg_result,
                total_penalty=(
                    sg_result.total_penalty + _bca_penalty
                ),
            )

        # ボラ方向EXPANDING時のペナルティ加算
        if (
            bot.config.vol_direction_enabled
            and regime_result.volatility_direction
            == "expanding"
            and bot.config.vol_expanding_penalty > 0
        ):
            sg_result = dataclasses.replace(
                sg_result,
                total_penalty=(
                    sg_result.total_penalty
                    + bot.config.vol_expanding_penalty
                ),
            )

        # セッションフィルター
        hour_utc = (
            current_time.hour
            if hasattr(current_time, 'hour')
            else current_time.to_pydatetime().hour
        )

        ctx.sg_result = sg_result
        ctx.hour_utc = hour_utc
        ctx.atr_ratio = _atr_ratio
        ctx.primary_atr_abs = _primary_atr_abs

        def _filt_hold(reason: str) -> ConsolidatedSignal:
            """フィルターHOLD用ローカルヘルパー"""
            return bot._hold_with_analysis(
                reason, plan, tf_signals, consensus,
                regime_result, htf_alignment, sg_result,
            )

        # デモモード: 追加フィルタースキップ
        if not bot.config.demo_mode:
            # 上位足トレンドフィルター
            if not bot._check_htf_trend_alignment(
                current_time, consensus.direction,
                htf_tfs=_htf_tfs,
            ):
                if bot._flow_analyzer:
                    from autotrader.core.diagnostics import (
                        SignalStepRecord,
                    )
                    bot._flow_analyzer.collect(
                        SignalStepRecord(
                            timestamp=str(current_time),
                            regime=(
                                regime_result.regime.value
                            ),
                            volatility=(
                                regime_result.volatility_level
                            ),
                            mode=plan.mode,
                            primary_tf=plan.primary_tf,
                            risk_passed=True,
                            consensus_direction=(
                                consensus.direction.value
                            ),
                            consensus_score=(
                                consensus.score
                            ),
                            consensus_threshold=(
                                consensus.threshold
                            ),
                            consensus_passed=True,
                            htf_passed=False,
                            htf_direction=(
                                consensus.direction.value
                            ),
                            final_direction="HOLD",
                            hold_reason=(
                                f"HTFトレンド不一致"
                                f"({consensus.direction.value})"
                            ),
                        ),
                    )
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"HTFトレンド不一致"
                    f"({consensus.direction.value})"
                )
                return ctx

            # SoftGuardペナルティによるブロック
            if sg_result.total_penalty >= 0.8:
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"SoftGuardブロック: penalty="
                    f"{sg_result.total_penalty:.2f}"
                )
                return ctx

            # ペナルティ上限フィルター
            _eff_penalty_cap = (
                bot.config.penalty_cap
                - _overrides.penalty_cap_delta
            )
            if (
                _eff_penalty_cap < 0.8
                and sg_result.total_penalty
                >= _eff_penalty_cap
            ):
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"ペナルティ上限: "
                    f"{sg_result.total_penalty:.2f}"
                    f" >= {_eff_penalty_cap:.2f}"
                )
                return ctx

            # トレンド強度上限フィルター
            if (
                bot.config.trend_strength_max < 999.0
                and regime_result.trend_strength
                >= bot.config.trend_strength_max
            ):
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"トレンド強度過大: "
                    f"{regime_result.trend_strength:.2f}"
                    f" >= {bot.config.trend_strength_max}"
                )
                return ctx

            # LONDONオフ時間ブロック
            if (
                hour_utc == 7
                and sg_result.total_penalty > 0
            ):
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"LONDONオフ時間ブロック: "
                    f"hour={hour_utc}, "
                    f"penalty="
                    f"{sg_result.total_penalty:.2f}"
                )
                return ctx

            # TOKYOオフ時間フィルター
            if (
                4 <= hour_utc <= 6
                and sg_result.total_penalty > 0
                and consensus.score < 6.6
            ):
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"TOKYOオフ時間フィルター: "
                    f"hour={hour_utc}, "
                    f"score={consensus.score:.1f}<6.6"
                )
                return ctx

            # 東京深夜フィルター
            if (
                17 <= hour_utc <= 21
                and regime_result.regime
                == MarketRegime.TREND
                and consensus.score
                < consensus.threshold + 0.3
            ):
                _tn_threshold = (
                    consensus.threshold + 0.3
                )
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"東京深夜TREND: hour={hour_utc}, "
                    f"score={consensus.score:.1f}"
                    f"<{_tn_threshold:.1f}"
                )
                return ctx

            # off_hours TREND完全ブロック
            if (
                bot.config.off_hours_trend_block
                and hour_utc not in range(8, 18)
                and regime_result.regime
                == MarketRegime.TREND
            ):
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"OffHoursTRENDBlock: "
                    f"hour={hour_utc}"
                )
                return ctx

            # off_hours + 高htf_alignment 複合ブロック
            if (
                bot.config.off_hours_high_align_block
                and hour_utc not in range(8, 18)
                and abs(htf_alignment)
                >= bot.config
                .off_hours_high_align_threshold
            ):
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"OffHoursHighAlignBlock: "
                    f"hour={hour_utc},"
                    f" |align|="
                    f"{abs(htf_alignment):.2f}"
                )
                return ctx

            # RANGE/LOW_VOLフィルタ群
            _range_hold = bot._check_range_regime_filter(
                regime_result=regime_result,
                consensus=consensus,
                sg_result=sg_result,
                hour_utc=hour_utc,
                plan=plan,
                current_time=current_time,
            )
            if _range_hold is not None:
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    _range_hold,
                )
                return ctx

            # TOKYO低ペナルティ帯
            if (
                4 <= hour_utc <= 6
                and 0 < sg_result.total_penalty <= 0.2
                and consensus.score
                < consensus.threshold + 0.2
            ):
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"TOKYO低penalty閾値: penalty="
                    f"{sg_result.total_penalty:.2f}, "
                    f"score={consensus.score:.1f}"
                    f"<{consensus.threshold + 0.2:.1f}"
                )
                return ctx

            # MACDスロープ逆方向フィルター
            _primary_sig = tf_signals.get(
                plan.primary_tf,
            )
            if (
                _primary_sig
                and _primary_sig.score_breakdown
            ):
                _macd_slope = (
                    _primary_sig
                    .score_breakdown
                    .macd_slope
                )
                if (
                    _macd_slope
                    <= bot.config
                    .macd_slope_filter_threshold
                ):
                    ctx.should_abort = True
                    ctx.abort_signal = _filt_hold(
                        f"MACDスロープ逆方向: "
                        f"{_macd_slope:.1f}"
                    )
                    return ctx

        # 高alignment時スコアペナルティ
        if (
            bot.config.high_align_penalty_threshold
            is not None
            and abs(htf_alignment)
            > bot.config.high_align_penalty_threshold
        ):
            _penalty = (
                bot.config.high_align_penalty_score
            )
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
                    f">"
                    f"{bot.config.high_align_penalty_threshold})"
                ),
                buy_score=consensus.buy_score,
                sell_score=consensus.sell_score,
                dynamic_entry_tf=(
                    consensus.dynamic_entry_tf
                ),
            )
            ctx.consensus = consensus
            if consensus.score < consensus.threshold:
                ctx.should_abort = True
                ctx.abort_signal = _filt_hold(
                    f"高alignment penalty: score="
                    f"{consensus.score:.1f}"
                    f"<{consensus.threshold:.1f}"
                )
                return ctx

        return ctx


# ============================================================
# Step 6: SizingStep
# ============================================================


class SizingStep:
    """SL/TP計算 + ポジションサイジング + シグナル構築"""

    name = "sizing"

    def execute(
        self,
        ctx: PipelineContext,
        bot: Any,
    ) -> PipelineContext:
        """サイジングとシグナル構築を実行"""
        consensus = ctx.consensus
        tf_signals = ctx.tf_signals
        plan = ctx.plan
        regime_result = ctx.regime_result
        htf_alignment = ctx.htf_alignment
        sg_result = ctx.sg_result
        current_time = ctx.current_time
        _overrides = ctx.overrides
        _fund_assessment = ctx.fund_assessment

        # SL/TP計算
        primary_signal = tf_signals.get(plan.primary_tf)
        if primary_signal is None:
            if bot._flow_analyzer:
                from autotrader.core.diagnostics import (
                    SignalStepRecord,
                )
                bot._flow_analyzer.collect(
                    SignalStepRecord(
                        timestamp=str(current_time),
                        regime=(
                            regime_result.regime.value
                        ),
                        volatility=(
                            regime_result.volatility_level
                        ),
                        mode=plan.mode,
                        primary_tf=plan.primary_tf,
                        risk_passed=True,
                        consensus_direction=(
                            consensus.direction.value
                        ),
                        consensus_score=consensus.score,
                        consensus_threshold=(
                            consensus.threshold
                        ),
                        consensus_passed=True,
                        htf_passed=True,
                        final_direction="HOLD",
                        hold_reason="primary_tfデータなし",
                    ),
                )
            ctx.should_abort = True
            ctx.abort_signal = bot._hold_with_analysis(
                "primary_tfデータなし",
                plan, tf_signals, consensus,
                regime_result, htf_alignment, sg_result,
            )
            return ctx

        sl_pips = (
            primary_signal.sl_pips
            * _overrides.sl_multiplier
        )
        # TREND時のSL下限上書き
        if (
            bot.config.trend_sl_min_pips is not None
            and regime_result.regime == MarketRegime.TREND
        ):
            sl_pips = max(
                sl_pips, bot.config.trend_sl_min_pips,
            )
        # TREND時のSL上限キャップ
        if (
            bot.config.trend_sl_max_pips is not None
            and regime_result.regime == MarketRegime.TREND
        ):
            sl_pips = min(
                sl_pips, bot.config.trend_sl_max_pips,
            )
        # BREAKOUT時のSL下限上書き
        if (
            bot.config.regime_breakout_sl_min_pips
            is not None
            and regime_result.regime
            == MarketRegime.BREAKOUT
        ):
            sl_pips = max(
                sl_pips,
                bot.config.regime_breakout_sl_min_pips,
            )
        # BREAKOUT時のSL上限キャップ
        if (
            bot.config.regime_breakout_sl_max_pips
            is not None
            and regime_result.regime
            == MarketRegime.BREAKOUT
        ):
            sl_pips = min(
                sl_pips,
                bot.config.regime_breakout_sl_max_pips,
            )
        # EXPANDING時のSL拡大
        if (
            bot.config.vol_direction_enabled
            and regime_result.volatility_direction
            == "expanding"
            and bot.config.vol_expanding_sl_multiplier
            != 1.0
        ):
            sl_pips = (
                sl_pips
                * bot.config.vol_expanding_sl_multiplier
            )
        # レジーム別動的TP比率
        tp_sl_ratio = (
            plan.get_recommended_tp_sl_ratio()
            * bot.config.tp_sl_ratio
        )
        if bot.config.dynamic_tp_enabled:
            _regime = ctx.regime_result
            if _regime is not None:
                _r = _regime.regime
                if _r == MarketRegime.TREND:
                    _dyn = bot.config.dynamic_tp_trend
                elif _r == MarketRegime.RANGE:
                    _dyn = bot.config.dynamic_tp_range
                elif _r == MarketRegime.HIGH_VOL:
                    _dyn = (
                        bot.config.dynamic_tp_high_vol
                    )
                elif _r == MarketRegime.BREAKOUT:
                    _dyn = (
                        bot.config
                        .regime_breakout_tp_multiplier
                    )
                else:
                    _dyn = (
                        bot.config.dynamic_tp_low_vol
                    )
                tp_sl_ratio = tp_sl_ratio * _dyn
        # BREAKOUT時はdynamic_tp無効でもTP倍率を適用
        if (
            bot.config.regime_breakout_enabled
            and not bot.config.dynamic_tp_enabled
            and regime_result.regime
            == MarketRegime.BREAKOUT
        ):
            tp_sl_ratio = (
                tp_sl_ratio
                * bot.config.regime_breakout_tp_multiplier
            )
        tp_pips = sl_pips * tp_sl_ratio

        # ポジションサイジング
        lot = 0.01
        if bot.config.enable_position_sizing:
            confidence = (
                consensus.score / consensus.threshold
            )
            _liq_factor = 1.0
            _vol_mult = 1.0
            if ctx.fundamental_ctx is not None:
                _liq_factor = (
                    ctx.fundamental_ctx.liquidity_factor
                )
                _vol_mult = (
                    ctx.fundamental_ctx
                    .volatility_multiplier
                )
            if _fund_assessment is not None:
                _liq_factor = min(
                    _liq_factor,
                    _fund_assessment.lot_multiplier,
                )
            sizing_context = SizingContext(
                equity=bot.state.equity,
                sl_pips=sl_pips,
                confidence=confidence,
                regime=regime_result.regime,
                consecutive_losses=(
                    bot.state.consecutive_losses
                ),
                current_dd_pct=(
                    bot.state.current_dd_pct
                ),
                initial_equity=(
                    bot.state.initial_equity
                ),
                open_exposure_lot=(
                    bot.state.open_exposure_lot
                ),
                open_same_direction_lot=(
                    bot.state.open_same_direction_lot
                ),
                liquidity_factor=_liq_factor,
                volatility_multiplier=_vol_mult,
            )
            sizing_result = (
                bot.position_sizer.calculate(
                    sizing_context,
                )
            )
            if sizing_result.blocked:
                ctx.should_abort = True
                ctx.abort_signal = bot._hold_with_analysis(
                    f"資金管理: {sizing_result.reasoning}",
                    plan, tf_signals, consensus,
                    regime_result, htf_alignment,
                    sg_result,
                )
                return ctx
            lot = sizing_result.lot

        rationale = (
            f"{consensus.reasoning}, "
            f"mode={plan.mode}, "
            f"lot={lot:.2f}"
        )

        # フロー分析: シグナル発生記録
        if bot._flow_analyzer:
            from autotrader.core.diagnostics import (
                SignalStepRecord,
            )
            bot._flow_analyzer.collect(
                SignalStepRecord(
                    timestamp=str(current_time),
                    regime=regime_result.regime.value,
                    volatility=(
                        regime_result.volatility_level
                    ),
                    mode=plan.mode,
                    primary_tf=plan.primary_tf,
                    risk_passed=True,
                    consensus_direction=(
                        consensus.direction.value
                    ),
                    consensus_score=consensus.score,
                    consensus_threshold=(
                        consensus.threshold
                    ),
                    consensus_passed=True,
                    htf_passed=True,
                    sl_pips=sl_pips,
                    tp_pips=tp_pips,
                    final_direction=(
                        consensus.direction.value
                    ),
                    hold_reason="",
                ),
            )

        # TF別スコア内訳を集約
        tf_breakdowns: dict[str, dict[str, float]] = {}
        for tf_name, sig in tf_signals.items():
            if sig.score_breakdown is not None:
                tf_breakdowns[tf_name] = (
                    sig.score_breakdown.to_dict()
                )

        # TF別方向を集約
        tf_directions: dict[str, str] = {
            tf: sig.direction.value
            for tf, sig in tf_signals.items()
        }

        # 返却用confidence計算
        ret_confidence = min(
            consensus.score / consensus.threshold,
            1.0,
        )

        # strategy_id構築
        _strategy_id = (
            f"{plan.mode}_{plan.selection_reason}"
            if plan.selection_reason
            else plan.mode
        )

        # 最終シグナル構築
        ctx.should_abort = True
        ctx.abort_signal = ConsolidatedSignal(
            direction=consensus.direction,
            confidence=ret_confidence,
            primary_tf=plan.primary_tf,
            aligned_tfs=consensus.aligned_tfs,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            rationale=rationale,
            scores={
                tf: sig.confidence
                for tf, sig in tf_signals.items()
            },
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
                r.value: v
                for r, v in sg_result.penalties.items()
            },
            trend_strength=(
                regime_result.trend_strength
            ),
            buy_score=consensus.buy_score,
            sell_score=consensus.sell_score,
            lot=lot,
        )

        return ctx


def build_default_pipeline() -> SignalPipeline:
    """デフォルトのパイプラインを構築

    Returns:
        SignalPipeline: 6ステップの標準パイプライン
    """
    return SignalPipeline(
        steps=[
            RiskCheckStep(),
            TimeframeEvalStep(),
            ConsensusStep(),
            EdgeAssessmentStep(),
            FilterStep(),
            SizingStep(),
        ],
    )
