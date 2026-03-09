"""年次バックテスト実行モジュール

統合ボットを使用した年単位のバックテスト実行ロジック。
並列実行に対応し、年ごとに独立したボットインスタンスを生成する。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from autotrader.backtest.candle_arrays import CandleArrays
from autotrader.backtest.events import BacktestEventEmitter
from autotrader.backtest.file_listener import FileEventListener
from autotrader.backtest.metrics import MetricsCalculator
from autotrader.backtest.position_event_logger import (
    PositionEventLogger,
)
from autotrader.backtest.simulator import SimulatorConfig, TradeSimulator
from autotrader.core.entities import Signal
from autotrader.core.enums import (
    ExitReason,
    SignalType,
    Timeframe,
)


def run_unified_year(
    runner: "BacktestRunner",
    bot_config: "UnifiedBotConfig",
    sim_config: SimulatorConfig,
    year: int,
    market_data: "dict[str, pd.DataFrame]",
    use_m1: bool = False,
    fundamental_provider: Any = None,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    emitter: "BacktestEventEmitter | None" = None,
    row_progress_callback: ("Callable[[int, int], None] | None") = None,
    adaptive_config: "TunerConfig | None" = None,
) -> dict[str, Any] | None:
    """統合ボットで1年分のバックテスト実行（self-contained）

    年ごとに新しい UnifiedTradeBot インスタンスを生成するため、
    年をまたいだ状態の累積が発生しない。
    並列実行に対応している。

    Args:
        bot_config: ボット設定（各年で fresh な bot を生成）
        sim_config: シミュレーター設定
        year: 対象年
        market_data: 全時間足データ
        use_m1: M1データを基準タイムフレームとして使用
        fundamental_provider: ファンダメンタルプロバイダー
        period_start: 日単位の開始日時（Noneで年始）
        period_end: 日単位の終了日時・exclusive（Noneで年末）
        emitter: イベントエミッター（Noneの場合は runner._emitter）
        row_progress_callback: 行レベル進捗コールバック
            (completed_rows, total_rows) → None。
            並列実行時にUIへリアルタイム進捗を通知する。

    Returns:
        年別結果（monthly_results フィールドを含む）
    """
    from autotrader.decision.unified import (  # noqa: F401
        UnifiedBotConfig,
        UnifiedTradeBot,
    )

    # 年ごとに fresh な bot を生成（状態の累積を防止）
    bot = UnifiedTradeBot(
        bot_config,
        adaptive_config=adaptive_config,
    )
    bot.state = bot.state.with_initial_equity(sim_config.initial_balance)
    bot.set_market_data(market_data)

    # イベントエミッター（並列時はリスナーなしの no-op emitter）
    _emitter = emitter if emitter is not None else runner._emitter

    # 年の標準範囲
    start_date = datetime(year, 1, 1)
    end_date = datetime(year + 1, 1, 1)
    # 日単位の期間指定で範囲を絞り込む
    if period_start is not None:
        start_date = max(start_date, period_start)
    if period_end is not None:
        end_date = min(end_date, period_end)
    # 有効な期間がなければスキップ
    if start_date >= end_date:
        return None

    # 基準タイムフレームの選択（M1 > M5 > M15 > H1）
    if use_m1 and runner._m1_df is not None:
        df = runner._m1_df
        tf = Timeframe.M1
    elif use_m1 and runner._m5_df is not None:
        df = runner._m5_df
        tf = Timeframe.M5
    elif runner._m15_df is not None:
        df = runner._m15_df
        tf = Timeframe.M15
    elif runner._h1_df is not None:
        df = runner._h1_df
        tf = Timeframe.H1
    else:
        return None

    period_df = df[
        (df["time"] >= start_date) & (df["time"] < end_date)
    ].reset_index(drop=True)

    _log = logging.getLogger(__name__)
    _log.info(
        "%d年: period_df=%d行, tf=%s",
        year,
        len(period_df),
        tf.value,
    )

    if period_df.empty:
        return None

    simulator = TradeSimulator(config=sim_config)

    # ポジションイベントロガー設定
    _pos_evt_logger = PositionEventLogger()
    simulator.set_position_event_logger(_pos_evt_logger)

    # 月別トラッキング（スレッド安全なローカルリスト）
    _monthly_results: list[dict[str, Any]] = []
    current_month = None
    month_start_balance = sim_config.initial_balance
    month_trades = 0

    # 進捗トラッキング
    total_rows = len(period_df)
    start_time = time.time()

    # メトリクス追跡
    winning_trades = 0
    losing_trades = 0
    # モード/レジーム追跡（ポジション別）
    _pos_mode_regime: dict[str, tuple[str, str]] = {}

    last_candle = None

    # numpy配列ベースのループ
    arrays = CandleArrays.from_dataframe(period_df)
    for idx in range(arrays.n_rows):
        candle = arrays.get_candle(idx, runner.config.symbol, tf)
        last_candle = candle
        candle_time = arrays.get_time(idx)

        # 月変わり検出
        candle_month = (candle_time.year, candle_time.month)
        if current_month is None:
            current_month = candle_month
            month_start_balance = simulator.state.balance
        elif candle_month != current_month:
            # 月末処理
            month_pnl = simulator.state.balance - month_start_balance
            month_return = month_pnl / month_start_balance * 100
            month_result = {
                "year": current_month[0],
                "month": current_month[1],
                "trades": month_trades,
                "pnl": month_pnl,
                "return_pct": month_return,
            }
            _monthly_results.append(month_result)
            _emitter.emit_month_end(month_result)

            current_month = candle_month
            month_start_balance = simulator.state.balance
            month_trades = 0

        # 資金管理: botのequityとエクスポージャーを同期
        open_positions = simulator.get_open_positions()
        exposure_lot = sum(p.volume for p in open_positions)
        # 同方向ロット: BUY/SELLの大きい方を設定
        buy_lot = sum(
            p.volume for p in open_positions if p.signal_type == SignalType.BUY
        )
        sell_lot = sum(
            p.volume
            for p in open_positions
            if p.signal_type == SignalType.SELL
        )
        buy_count = sum(
            1 for p in open_positions if p.signal_type == SignalType.BUY
        )
        sell_count = sum(
            1 for p in open_positions if p.signal_type == SignalType.SELL
        )
        bot.state = bot.state.with_exposure(
            open_exposure_lot=exposure_lot,
            open_same_direction_lot=max(buy_lot, sell_lot),
            open_buy_count=buy_count,
            open_sell_count=sell_count,
        )

        # Layer 5: エクイティ記録
        py_time = candle_time
        bot.risk_manager.record_equity(
            py_time,
            simulator.state.equity,
        )

        # Layer 5: 急速DD検知
        bot.risk_manager.check_rapid_dd(
            py_time,
            simulator.state.equity,
        )

        # Layer 4: サーキットブレーカー
        if bot.risk_manager.config.circuit_breaker_enabled and open_positions:
            _unrealized_pnl = sum(p.unrealized_pnl for p in open_positions)
            _equity = simulator.state.equity
            if _equity > 0:
                _loss_pct = abs(min(_unrealized_pnl, 0)) / _equity
                if (
                    _loss_pct
                    >= bot.risk_manager.config.circuit_breaker_loss_pct
                ):
                    # 全ポジション強制決済
                    for pos in list(open_positions):
                        simulator._close_position(
                            pos,
                            candle.close,
                            candle_time,
                            ExitReason.CIRCUIT_BREAKER,
                        )
                    bot.risk_manager.trigger_circuit_breaker(
                        py_time,
                    )

        # [FUNDAMENTAL] 重要指標前スキップチェック
        current_time = pd.Timestamp(candle_time)
        if fundamental_provider is not None:
            import datetime as _dt

            _now_utc = _dt.datetime(
                candle_time.year,
                candle_time.month,
                candle_time.day,
                candle_time.hour,
                candle_time.minute,
                tzinfo=_dt.timezone.utc,
            )
            _fctx = fundamental_provider.get_context(
                _now_utc, runner.config.symbol
            )
            # PRE_EVENT: 高インパクト指標30分前は常にスキップ
            if _fctx.has_high_impact_within_30min:
                continue
            # Phase 2b無効時: caution_levelベースの追加ブロック
            # Phase 2b有効時: アセッサーの方向フィルターに委任
            if (
                not bot_config.fundamental_assessor_enabled
                and _fctx.event_caution_level
                >= (bot_config.fundamental_caution_block_level)
            ):
                continue  # 超重要指標日はスキップ

        # 統合ボットでシグナル生成
        # Phase 2b: FundamentalMemoryスナップショットを渡す
        _fund_mem_snap = None
        if (
            fundamental_provider is not None
            and hasattr(fundamental_provider, "memory")
            and fundamental_provider.memory is not None
        ):
            _fund_mem_snap = fundamental_provider.memory.snapshot()
        consolidated = bot.generate_signal(
            current_time,
            candle,
            fundamental_ctx=(
                _fctx if fundamental_provider is not None else None
            ),
            fundamental_memory=_fund_mem_snap,
        )

        # シグナルイベント発行（HOLD以外）
        if consolidated.direction.value != "HOLD":
            _emitter.emit_signal(
                signal_type=consolidated.direction.value,
                symbol=runner.config.symbol,
                timeframe=tf.value,
                confidence=consolidated.confidence,
                sl_pips=consolidated.sl_pips,
                tp_pips=consolidated.tp_pips,
                rationale=consolidated.rationale,
                aligned_timeframes=consolidated.aligned_tfs,
                candle_time=candle_time,
                score_breakdowns=(consolidated.tf_score_breakdowns),
            )

        # Signalオブジェクトに変換
        signal = None
        if consolidated.direction != SignalType.HOLD:
            if consolidated.confidence >= 0.5:
                # SL/TPをpips値で格納（ライブと統一）
                _sl_pips = (
                    consolidated.sl_pips
                    if consolidated.sl_pips > 0
                    else None
                )
                _tp_pips = (
                    consolidated.tp_pips
                    if consolidated.tp_pips > 0
                    else None
                )

                # ATR実測値をスナップショットに格納（PM用）
                _row = period_df.iloc[idx]
                _atr_val = float(_row.get("atr_14", 0) or 0)
                _indicators: dict[str, Any] = {}
                if _atr_val > 0:
                    _indicators["atr_14"] = _atr_val

                signal = Signal(
                    symbol=runner.config.symbol,
                    timeframe=tf,
                    signal_type=consolidated.direction,
                    confidence=min(consolidated.confidence, 1.0),
                    stop_loss=_sl_pips,
                    take_profit=_tp_pips,
                    reasoning=consolidated.rationale,
                    regime=consolidated.regime,
                    mode=consolidated.mode,
                    consensus_score=consolidated.consensus_score,
                    lot=consolidated.lot,
                    indicators_snapshot=_indicators,
                )

        prev_position_ids = {
            p.position_id for p in simulator.get_open_positions()
        }
        prev_trade_count = len(simulator.get_closed_trades())

        # コンセンサススコアを渡す（逆転exit用）
        _consensus_scores = None
        if consolidated is not None:
            _consensus_scores = (
                consolidated.buy_score,
                consolidated.sell_score,
            )
        # Phase 2b: ファンダメンタル評価をPMへ渡す
        _fund_assess = getattr(
            bot,
            "_last_fundamental_assessment",
            None,
        )
        simulator.process_candle(
            candle,
            signal,
            consensus_scores=_consensus_scores,
            fundamental_assessment=_fund_assess,
        )

        # 新規ポジション検出
        current_positions = simulator.get_open_positions()
        for pos in current_positions:
            if pos.position_id not in prev_position_ids:
                _mode = (
                    consolidated.mode or getattr(bot, "_last_mode", "")
                    if consolidated
                    else getattr(bot, "_last_mode", "")
                )
                _regime = (
                    consolidated.regime or getattr(bot, "_last_regime", "")
                    if consolidated
                    else getattr(bot, "_last_regime", "")
                )
                _key = pos.position_id
                # エントリー時メトリクス取得
                row = period_df.iloc[idx]
                _entry_atr = float(row.get("atr_14", 0) or 0)
                _entry_adx = float(row.get("adx", 0) or 0)
                _entry_bb_w = float(row.get("bb_width", 0) or 0)
                # エントリー時メトリクス（sim側）
                _em = simulator.get_entry_metrics(
                    pos.position_id,
                )
                # スプレッド: 価格→pips変換
                _entry_spread_pips = (
                    _em.get("spread", 0) / sim_config.pip_unit
                    if _em else 0.0
                )
                _pos_mode_regime[_key] = {
                    "mode": _mode,
                    "regime": _regime,
                    "primary_tf": (consolidated.primary_tf),
                    "strategy_id": (consolidated.strategy_id),
                    "score_breakdowns": (consolidated.tf_score_breakdowns),
                    "confidence": consolidated.confidence,
                    "consensus_score": (consolidated.consensus_score or 0.0),
                    "sl_pips": consolidated.sl_pips,
                    "tp_pips": consolidated.tp_pips,
                    "rationale": consolidated.rationale,
                    "entry_spread_pips": (_entry_spread_pips),
                    "entry_atr": _entry_atr,
                    "entry_adx": _entry_adx,
                    "entry_bb_width": _entry_bb_w,
                    "position_id": pos.position_id,
                    "equity_before": (
                        _em.get("equity_before", 0) if _em else 0
                    ),
                    "dd_pct_at_entry": (
                        _em.get("dd_pct_at_entry", 0) if _em else 0
                    ),
                    "consecutive_losses": int(
                        _em.get(
                            "consecutive_losses",
                            0,
                        )
                        if _em
                        else 0
                    ),
                    "risk_per_trade_pct": (
                        _em.get(
                            "risk_per_trade_pct",
                            0,
                        )
                        if _em
                        else 0
                    ),
                    "lot": pos.volume,
                    # Phase5: 新メタデータ
                    "entry_threshold": (consolidated.entry_threshold),
                    "htf_alignment": (consolidated.htf_alignment),
                    "penalty_total": (consolidated.penalty_total),
                    "penalty_breakdown": (consolidated.penalty_breakdown),
                    "trend_strength": (consolidated.trend_strength),
                }
                _emitter.emit_trade_opened(
                    trade_id=pos.position_id,
                    symbol=pos.symbol,
                    direction=pos.signal_type.value,
                    entry_price=pos.entry_price,
                    volume=pos.volume,
                    candle_time=candle_time,
                    trading_mode=_mode,
                    market_regime=_regime,
                )

        # 決済検出
        closed_trades = simulator.get_closed_trades()
        if len(closed_trades) > prev_trade_count:
            month_trades += 1

            # 最新の決済トレードを取得
            new_trade = closed_trades[-1]
            pnl = new_trade.profit_loss or 0

            # TradeRecord生成（アダプティブ調整用）
            from autotrader.decision.unified.adaptive import (
                TradeRecord,
            )

            _trade_record = TradeRecord.from_trade(new_trade)

            # リスク管理に記録（PnLを渡して複利計算に反映）
            bot.on_trade_executed(
                candle_time,
                pnl=pnl,
                trade_record=_trade_record,
            )

            if pnl > 0:
                winning_trades += 1
            else:
                losing_trades += 1

            # 保有時間・pips計算
            _holding_min = 0.0
            if new_trade.opened_at and candle_time:
                _td = candle_time - new_trade.opened_at
                _holding_min = _td.total_seconds() / 60.0
            _pips = 0.0
            if new_trade.exit_price and new_trade.entry_price:
                _pips = (
                    (new_trade.exit_price - new_trade.entry_price)
                    / sim_config.pip_unit
                    if new_trade.signal_type.value == "BUY"
                    else (new_trade.entry_price - new_trade.exit_price)
                    / sim_config.pip_unit
                )
            _ckey = new_trade.position_id or str(new_trade.opened_at)
            _sig_data = _pos_mode_regime.get(_ckey, {})
            if not _sig_data:
                logging.getLogger(__name__).warning(
                    "sig_data欠落: key=%s",
                    _ckey,
                )
            _cm = _sig_data.get("mode", "")
            _cr = _sig_data.get("regime", "")
            # MFE/MAE取得
            _pos_id = new_trade.position_id or _sig_data.get("position_id", "")
            _mfe_mae = simulator.get_position_mfe_mae(
                _pos_id,
            )
            # 完全クローズ時のみpop
            _still_open = any(
                p.position_id == _pos_id
                for p in simulator.get_open_positions()
            )
            if not _still_open:
                _pos_mode_regime.pop(_ckey, None)
            # Exit時メトリクス取得
            _xm = simulator.get_exit_metrics(
                new_trade.trade_id,
            )
            _exit_spread_pips = _xm.get("exit_spread", 0) / sim_config.pip_unit
            # time_to_mfe計算
            _time_to_mfe_min = 0.0
            _mfe_time = _mfe_mae.get("mfe_time")
            if _mfe_time and new_trade.opened_at:
                _td_mfe = _mfe_time - new_trade.opened_at
                _time_to_mfe_min = _td_mfe.total_seconds() / 60.0
            # session判定（UTC時間ベース）
            _session = ""
            if new_trade.opened_at:
                _h = new_trade.opened_at.hour
                if 0 <= _h < 7:
                    _session = "TOKYO"
                elif 7 <= _h < 12:
                    _session = "LONDON"
                elif 12 <= _h < 17:
                    _session = "NEWYORK"
                else:
                    _session = "TOKYO"
            # parent_trade_id / position_id
            _parent_id = new_trade.parent_trade_id or ""
            _position_id = new_trade.position_id or ""
            # Exit詳細理由を取得（STAGNATION等の診断用）
            _exit_detail = simulator.get_exit_detail(
                _pos_id,
            )
            _emitter.emit_trade_closed(
                trade_id=new_trade.trade_id,
                symbol=new_trade.symbol,
                direction=new_trade.signal_type.value,
                entry_price=new_trade.entry_price,
                exit_price=new_trade.exit_price or 0,
                volume=new_trade.volume,
                profit_loss=pnl,
                exit_reason=(
                    new_trade.exit_reason.value
                    if new_trade.exit_reason
                    else "UNKNOWN"
                ),
                candle_time=candle_time,
                opened_at=new_trade.opened_at,
                holding_minutes=_holding_min,
                pips=_pips,
                trading_mode=_cm,
                market_regime=_cr,
                signal_data=_sig_data,
                mfe_pips=_mfe_mae.get("mfe", 0.0),
                mae_pips=_mfe_mae.get("mae", 0.0),
                entry_spread_pips=_sig_data.get(
                    "entry_spread_pips",
                    0.0,
                ),
                entry_atr=_sig_data.get(
                    "entry_atr",
                    0.0,
                ),
                entry_adx=_sig_data.get(
                    "entry_adx",
                    0.0,
                ),
                entry_bb_width=_sig_data.get(
                    "entry_bb_width",
                    0.0,
                ),
                exit_spread_pips=_exit_spread_pips,
                slippage_pips=_xm.get(
                    "slippage_pips",
                    0.0,
                ),
                commission=_xm.get(
                    "commission",
                    0.0,
                ),
                equity_before=_sig_data.get(
                    "equity_before",
                    0.0,
                ),
                equity_after=_xm.get(
                    "equity_after",
                    0.0,
                ),
                dd_pct_at_entry=_sig_data.get(
                    "dd_pct_at_entry",
                    0.0,
                ),
                consecutive_losses=_sig_data.get(
                    "consecutive_losses",
                    0,
                ),
                risk_per_trade_pct=_sig_data.get(
                    "risk_per_trade_pct",
                    0.0,
                ),
                lot=_sig_data.get("lot", 0.0),
                # Phase5-6: 新フィールド
                parent_trade_id=_parent_id,
                position_id=_position_id,
                entry_threshold=_sig_data.get(
                    "entry_threshold",
                    0.0,
                ),
                htf_alignment=_sig_data.get(
                    "htf_alignment",
                    0.0,
                ),
                penalty_total=_sig_data.get(
                    "penalty_total",
                    0.0,
                ),
                penalty_breakdown=_sig_data.get(
                    "penalty_breakdown",
                    {},
                ),
                trend_strength=_sig_data.get(
                    "trend_strength",
                    0.0,
                ),
                mfe_r=_mfe_mae.get("mfe_r", 0.0),
                mae_r=_mfe_mae.get("mae_r", 0.0),
                time_to_mfe_minutes=(_time_to_mfe_min),
                session=_session,
                strategy_id=_sig_data.get(
                    "strategy_id",
                    "",
                ),
                trigger_price=_xm.get(
                    "trigger_price",
                    0.0,
                ),
                fill_price=_xm.get(
                    "fill_price",
                    0.0,
                ),
                exit_reason_detail=_exit_detail,
            )

            # メトリクス発行
            total_trades = len(closed_trades)
            _emitter.emit_metrics(
                balance=simulator.state.balance,
                equity=simulator.state.balance,
                total_trades=total_trades,
                winning_trades=winning_trades,
                losing_trades=losing_trades,
                max_drawdown=simulator.state.max_drawdown * 100,
            )

        # 進捗イベント（M1時は500行ごと、それ以外は100行ごと）
        progress_interval = 500 if tf == Timeframe.M1 else 100
        if idx % progress_interval == 0:
            elapsed = time.time() - start_time
            _emitter.emit_progress(
                current=idx,
                total=total_rows,
                elapsed=elapsed,
                message=f"{year}年処理中 ({tf.value})",
            )
            # 並列実行時：行レベル進捗をメインスレッドへ通知
            if row_progress_callback is not None:
                row_progress_callback(idx, total_rows)

            # キャンセルチェック
            if runner._check_cancel_requested():
                _emitter.emit_backtest_end({"cancelled": True})
                return None

    # ループ完了を100%として通知
    if row_progress_callback is not None:
        row_progress_callback(total_rows, total_rows)

    # 強制決済
    if last_candle:
        simulator.force_close_all(last_candle, ExitReason.FORCE_CLOSE)

    # 最終月の結果
    if current_month:
        month_pnl = simulator.state.balance - month_start_balance
        month_return = month_pnl / month_start_balance * 100
        month_result = {
            "year": current_month[0],
            "month": current_month[1],
            "trades": month_trades,
            "pnl": month_pnl,
            "return_pct": month_return,
        }
        _monthly_results.append(month_result)
        _emitter.emit_month_end(month_result)

    # ポジションイベントCSV出力
    if _pos_evt_logger.event_count > 0:
        for listener in runner._emitter._listeners:
            if isinstance(listener, FileEventListener):
                evt_path = listener.log_dir / f"position_events_{year}.csv"
                _pos_evt_logger.write_csv(evt_path)
                break

    trades = simulator.get_closed_trades()
    calculator = MetricsCalculator(initial_balance=sim_config.initial_balance)
    metrics = calculator.calculate(trades, simulator.state.daily_pnl)

    # ブレークダウン生成（regime/mode/exit_reason別）
    breakdown = calculator.generate_breakdown(trades)

    # ログ品質チェック
    validate_trade_log(trades, year)

    return {
        "year": year,
        "trades": len(trades),
        "win_rate": metrics.win_rate * 100,
        "non_loss_rate": metrics.non_loss_rate * 100,
        "profit_factor": metrics.profit_factor,
        "net_profit": simulator.state.balance - sim_config.initial_balance,
        "max_drawdown": metrics.max_drawdown_pct * 100,
        "sharpe": metrics.sharpe_ratio or 0,
        "breakdown": breakdown,
        "monthly_results": _monthly_results,
    }


def validate_trade_log(
    trades: list,
    year: int,
) -> None:
    """トレードログの品質チェック

    regime/mode/scoreが欠落していないか検証。
    部分決済(parent_trade_id付き)は親情報を継承するため
    score=0でもエラーとしない。

    Args:
        trades: トレードリスト
        year: 対象年
    """
    _log = logging.getLogger(__name__)
    errors: list[str] = []
    for t in trades:
        tid = t.trade_id[:8] if t.trade_id else "?"
        if not t.regime or t.regime == "UNKNOWN":
            errors.append(f"regime欠落: {tid}")
        if not t.mode or t.mode == "UNKNOWN":
            errors.append(f"mode欠落: {tid}")
        if (t.consensus_score or 0) == 0 and not t.parent_trade_id:
            errors.append(f"score=0: {tid}")
    if errors:
        msg = f"{year}年ログ品質警告({len(errors)}件):\n" + "\n".join(
            errors[:10]
        )
        _log.warning(msg)
