"""月単位並列バックテスト実行モジュール

年を12ヶ月に分割し、各月を独立したequity（100万円）で並列実行する。
M1解像度バックテスト時の速度低下を12並列で相殺する。
"""

from __future__ import annotations

import logging
import multiprocessing
import pickle as _pickle
from concurrent.futures import (
    ProcessPoolExecutor,
    as_completed,
)
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from autotrader.backtest.simulator import SimulatorConfig

if TYPE_CHECKING:
    from autotrader.backtest.events import (
        BacktestEventEmitter,
    )
    from autotrader.backtest.runner import (
        BacktestRunner,
    )
    from autotrader.decision.unified import (
        UnifiedBotConfig,
    )
    from autotrader.decision.unified.adaptive import (
        TunerConfig,
    )

_log = logging.getLogger(__name__)


def _filter_market_data_for_month(
    market_data: dict[str, pd.DataFrame],
    year: int,
    month: int,
) -> dict[str, pd.DataFrame]:
    """月単位で market_data をフィルタリング

    IPC転送量を削減するため、対象月のデータのみ抽出する。

    Args:
        market_data: 全時間足データ
        year: 対象年
        month: 対象月（1-12）

    Returns:
        フィルタリング済みの時間足別データフレーム
    """
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    filtered: dict[str, pd.DataFrame] = {}
    for tf, df in market_data.items():
        mask = (df["time"] >= start) & (df["time"] < end)
        filtered[tf] = df[mask].reset_index(drop=True)
    return filtered


def _build_month_close_analysis(
    monthly_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """月末強制クローズ分析を生成

    各月結果から FORCE_CLOSE トレードの影響を分析する。

    Args:
        monthly_results: 月別結果リスト

    Returns:
        月末クローズ分析辞書
    """
    total_fc = 0
    total_trades = 0
    # FORCE_CLOSE の損益集計
    fc_wins = 0
    fc_losses = 0
    fc_win_amount = 0.0
    fc_loss_amount = 0.0
    normal_wins = 0
    normal_losses = 0
    normal_win_amount = 0.0
    normal_loss_amount = 0.0

    for mr in monthly_results:
        _fc = mr.get("force_closed_trades", 0)
        _trades = mr.get("trades", 0)
        total_fc += _fc
        total_trades += _trades
        # 詳細が取れない場合は概算
        fc_pnl = mr.get("force_close_pnl", 0.0)
        if _fc > 0:
            if fc_pnl >= 0:
                fc_wins += _fc
                fc_win_amount += fc_pnl
            else:
                fc_losses += _fc
                fc_loss_amount += abs(fc_pnl)

    # 通常トレード（概算: 全体 - FC）
    normal_trades = total_trades - total_fc
    for mr in monthly_results:
        _normal_pnl = mr.get("pnl", 0.0) - mr.get("force_close_pnl", 0.0)
        _normal_t = mr.get("trades", 0) - mr.get("force_closed_trades", 0)
        if _normal_t > 0 and _normal_pnl > 0:
            normal_wins += _normal_t
            normal_win_amount += _normal_pnl
        elif _normal_t > 0:
            normal_losses += _normal_t
            normal_loss_amount += abs(_normal_pnl)

    # WR/PF計算
    def _wr(w: int, total: int) -> float:
        return w / total * 100 if total > 0 else 0.0

    def _pf(win_amt: float, loss_amt: float) -> float:
        return win_amt / loss_amt if loss_amt > 0 else 999.99

    return {
        "total_force_closed": total_fc,
        "total_trades": total_trades,
        "force_close_ratio": (
            total_fc / total_trades if total_trades > 0 else 0.0
        ),
        "wr_with_force_close": _wr(fc_wins + normal_wins, total_trades),
        "wr_without_force_close": _wr(normal_wins, normal_trades),
        "pf_with_force_close": _pf(
            fc_win_amount + normal_win_amount,
            fc_loss_amount + normal_loss_amount,
        ),
        "pf_without_force_close": _pf(normal_win_amount, normal_loss_amount),
    }


def _aggregate_monthly_to_yearly(
    monthly_results: list[dict[str, Any]],
    year: int,
    initial_balance: float,
) -> dict[str, Any]:
    """月別結果を年次結果に集計

    12ヶ月の独立実行結果を1年分の年次結果に変換する。
    各月は独立equityで実行されるため、年間メトリクスは
    月別結果の合計・加重平均で計算する。

    Args:
        monthly_results: 月別結果リスト
        year: 対象年
        initial_balance: 初期残高

    Returns:
        年次結果辞書（run_unified_year()互換フォーマット）
    """
    if not monthly_results:
        return {
            "year": year,
            "trades": 0,
            "win_rate": 0.0,
            "non_loss_rate": 0.0,
            "profit_factor": 0.0,
            "net_profit": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "breakdown": {},
            "monthly_results": [],
        }

    total_trades = sum(r.get("trades", 0) for r in monthly_results)
    total_profit = sum(r.get("net_profit", 0.0) for r in monthly_results)

    # 勝率: トレード数加重平均
    if total_trades > 0:
        weighted_wr = (
            sum(
                r.get("win_rate", 0.0) * r.get("trades", 0)
                for r in monthly_results
            )
            / total_trades
        )
    else:
        weighted_wr = 0.0

    # 非敗率: トレード数加重平均
    if total_trades > 0:
        weighted_nlr = (
            sum(
                r.get("non_loss_rate", 0.0) * r.get("trades", 0)
                for r in monthly_results
            )
            / total_trades
        )
    else:
        weighted_nlr = 0.0

    # PF: 全月の利益合計 / 損失合計
    total_wins = sum(r.get("total_win_amount", 0.0) for r in monthly_results)
    total_losses = sum(
        r.get("total_loss_amount", 0.0) for r in monthly_results
    )
    pf = total_wins / total_losses if total_losses > 0 else 999.99

    # 最大DD: 各月の最大値
    max_dd = max(
        (r.get("max_drawdown", 0.0) for r in monthly_results),
        default=0.0,
    )

    # シャープレシオ: 月次リターンから計算
    monthly_returns = []
    for r in monthly_results:
        _np = r.get("net_profit", 0.0)
        _ret = _np / initial_balance if initial_balance > 0 else 0.0
        monthly_returns.append(_ret)

    if len(monthly_returns) >= 2:
        import numpy as np

        _arr = np.array(monthly_returns)
        _mean = float(np.mean(_arr))
        _std = float(np.std(_arr, ddof=1))
        sharpe = _mean / _std * (12**0.5) if _std > 0 else 0.0
    else:
        sharpe = 0.0

    # monthly_results を year_runner 互換フォーマットに変換
    compat_monthly: list[dict[str, Any]] = []
    for r in monthly_results:
        compat_monthly.append(
            {
                "year": year,
                "month": r.get("month", 0),
                "trades": r.get("trades", 0),
                "pnl": r.get("net_profit", 0.0),
                "return_pct": (
                    r.get("net_profit", 0.0) / initial_balance * 100
                    if initial_balance > 0
                    else 0.0
                ),
            }
        )

    # 月末強制クローズ分析
    month_close_analysis = _build_month_close_analysis(monthly_results)

    return {
        "year": year,
        "trades": total_trades,
        "win_rate": weighted_wr,
        "non_loss_rate": weighted_nlr,
        "profit_factor": pf,
        "net_profit": total_profit,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "breakdown": {},
        "monthly_results": compat_monthly,
        "month_close_analysis": month_close_analysis,
    }


def _run_month_worker(
    task_args: tuple,
) -> dict[str, Any] | None:
    """月バックテストワーカー関数（ProcessPool用）

    ProcessPoolExecutorから呼び出されるpicklable関数。
    サブプロセス内でBacktestRunnerを再構築し、
    1ヶ月分のバックテストを実行する。

    Args:
        task_args: (data_dir_base, backtest_config,
            bot_config, sim_config, year, month,
            month_market_data, use_m1,
            fundamental_provider, adaptive_config)

    Returns:
        月別結果辞書 または None
    """
    from autotrader.backtest.events import (
        BacktestEventEmitter,
    )
    from autotrader.backtest.file_listener import (
        TradeRowCollector,
    )
    from autotrader.backtest.runner import BacktestRunner
    from autotrader.backtest.year_runner import (
        run_unified_year,
    )

    (
        data_dir_base,
        backtest_config,
        bot_config,
        sim_config,
        year,
        month,
        month_market_data,
        use_m1,
        fundamental_provider,
        adaptive_config,
    ) = task_args

    # period_start / period_end を設定
    period_start = datetime(year, month, 1)
    if month == 12:
        period_end = datetime(year + 1, 1, 1)
    else:
        period_end = datetime(year, month + 1, 1)

    # サブプロセス内でRunnerを最小設定で再構築
    runner = BacktestRunner(
        data_dir=data_dir_base,
        config=backtest_config,
        verbose=False,
        log_to_file=False,
    )

    # 全時間足のインスタンス変数を設定
    runner._m1_df = month_market_data.get("M1")
    runner._m5_df = month_market_data.get("M5")
    runner._m15_df = month_market_data.get("M15")
    runner._m30_df = month_market_data.get("M30")
    runner._h1_df = month_market_data.get("H1")
    runner._h4_df = month_market_data.get("H4")
    runner._h8_df = month_market_data.get("H8")
    runner._d1_df = month_market_data.get("D1")

    # トレードデータ収集用エミッターとコレクター
    _emitter = BacktestEventEmitter()
    _collector = TradeRowCollector()
    _emitter.add_listener(_collector)

    result = run_unified_year(
        runner=runner,
        bot_config=bot_config,
        sim_config=sim_config,
        year=year,
        market_data=month_market_data,
        use_m1=use_m1,
        fundamental_provider=fundamental_provider,
        period_start=period_start,
        period_end=period_end,
        emitter=_emitter,
        adaptive_config=adaptive_config,
    )

    if result is None:
        return None

    # 月情報を付加
    result["month"] = month
    result["_worker_trade_rows"] = _collector._trade_rows
    result["_worker_stats"] = _collector.get_stats()

    # FORCE_CLOSE トレード数を集計
    fc_count = 0
    fc_pnl = 0.0
    total_win_amount = 0.0
    total_loss_amount = 0.0
    for row in _collector._trade_rows:
        _pnl = row.get("profit_loss", 0.0)
        if _pnl > 0:
            total_win_amount += _pnl
        else:
            total_loss_amount += abs(_pnl)
        if row.get("exit_reason") == "FORCE_CLOSE":
            fc_count += 1
            fc_pnl += _pnl

    result["force_closed_trades"] = fc_count
    result["force_close_pnl"] = fc_pnl
    result["total_win_amount"] = total_win_amount
    result["total_loss_amount"] = total_loss_amount

    return result


def run_monthly_parallel(
    runner: BacktestRunner,
    bot_config: UnifiedBotConfig,
    sim_config: SimulatorConfig,
    year: int,
    market_data: dict[str, pd.DataFrame],
    use_m1: bool = True,
    fundamental_provider: Any = None,
    max_workers: int = 12,
    emitter: BacktestEventEmitter | None = None,
    adaptive_config: TunerConfig | None = None,
) -> dict[str, Any] | None:
    """年を12ヶ月に分割して並列実行

    各月は独立な equity (sim_config.initial_balance) で
    スタートし、月末にオープンポジションを強制クローズする。
    12ヶ月の結果をマージして年間サマリーを計算。

    Args:
        runner: BacktestRunnerインスタンス
        bot_config: ボット設定
        sim_config: シミュレーター設定
        year: 対象年
        market_data: 全時間足データ
        use_m1: M1基準ループ使用
        fundamental_provider: ファンダメンタルプロバイダー
        max_workers: 最大並列ワーカー数
        emitter: イベントエミッター
        adaptive_config: アダプティブチューナー設定

    Returns:
        年別結果（monthly_results, month_close_analysis
        を含む）。データ不足時は None。
    """
    # pickle可能性チェック
    _can_parallel = True
    for _obj, _name in [
        (bot_config, "bot_config"),
        (sim_config, "sim_config"),
    ]:
        try:
            _pickle.dumps(_obj)
        except Exception as _pe:
            _log.warning(
                "%s がpickle不可: シーケンシャル実行にフォールバック (%s)",
                _name,
                _pe,
            )
            _can_parallel = False
            break

    # fundamental_provider の pickle 確認
    _fp_picklable = fundamental_provider is None
    if not _fp_picklable:
        try:
            _pickle.dumps(fundamental_provider)
            _fp_picklable = True
        except Exception as _fpe:
            _log.warning(
                "fundamental_provider がpickle不可: 並列実行でスキップ (%s)",
                _fpe,
            )
            _fp_picklable = False

    # 月ごとの market_data を事前フィルタリング
    months = list(range(1, 13))
    month_data: dict[int, dict[str, pd.DataFrame]] = {}
    for m in months:
        month_data[m] = _filter_market_data_for_month(market_data, year, m)

    monthly_results: list[dict[str, Any]] = []

    if not _can_parallel or max_workers <= 1:
        # シーケンシャル実行
        _log.info(
            "%d年: 月単位シーケンシャル実行 (12ヶ月)",
            year,
        )
        from autotrader.backtest.year_runner import (
            run_unified_year,
        )

        for m in months:
            period_start = datetime(year, m, 1)
            if m == 12:
                period_end = datetime(year + 1, 1, 1)
            else:
                period_end = datetime(year, m + 1, 1)

            result = run_unified_year(
                runner=runner,
                bot_config=bot_config,
                sim_config=sim_config,
                year=year,
                market_data=month_data[m],
                use_m1=use_m1,
                fundamental_provider=fundamental_provider,
                period_start=period_start,
                period_end=period_end,
                emitter=emitter,
                adaptive_config=adaptive_config,
            )
            if result is not None:
                result["month"] = m
                # FORCE_CLOSE集計（シーケンシャル時は概算）
                result["force_closed_trades"] = 0
                result["force_close_pnl"] = 0.0
                result["total_win_amount"] = 0.0
                result["total_loss_amount"] = 0.0
                monthly_results.append(result)

            # キャンセルチェック
            if runner._check_cancel_requested():
                break
    else:
        # ProcessPool 並列実行
        _project_root = str(Path(__file__).resolve().parent.parent.parent)
        effective_workers = min(max_workers, 12)
        _log.info(
            "%d年: 月単位並列実行 (12ヶ月, workers=%d)",
            year,
            effective_workers,
        )

        from autotrader.backtest.parallel_worker import (
            _worker_process_init,
        )

        _mp_ctx = multiprocessing.get_context("spawn")
        # ダミーキュー（月単位では行レベル進捗不要）
        with multiprocessing.Manager() as _manager:
            _dummy_q = _manager.Queue()

            with ProcessPoolExecutor(
                max_workers=effective_workers,
                mp_context=_mp_ctx,
                initializer=_worker_process_init,
                initargs=(_project_root, _dummy_q),
            ) as executor:
                futures = {
                    executor.submit(
                        _run_month_worker,
                        (
                            str(runner.data_dir.parent),
                            runner.config,
                            bot_config,
                            sim_config,
                            year,
                            m,
                            month_data[m],
                            use_m1,
                            (fundamental_provider if _fp_picklable else None),
                            adaptive_config,
                        ),
                    ): m
                    for m in months
                }

                for future in as_completed(futures):
                    m = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        _log.error(
                            "%d年%d月 バックテスト失敗: %s",
                            year,
                            m,
                            exc,
                            exc_info=True,
                        )
                        result = None
                    if result is not None:
                        monthly_results.append(result)

    if not monthly_results:
        return None

    # 月順ソート
    monthly_results.sort(key=lambda r: r.get("month", 0))

    # 年次結果に集計
    yearly = _aggregate_monthly_to_yearly(
        monthly_results,
        year,
        sim_config.initial_balance,
    )

    # ワーカー収集データをマージ
    all_trade_rows: list[dict[str, Any]] = []
    all_stats: dict[str, Any] = {}
    for mr in monthly_results:
        all_trade_rows.extend(mr.pop("_worker_trade_rows", []))
        _st = mr.pop("_worker_stats", {})
        for k, v in _st.items():
            if isinstance(v, (int, float)):
                all_stats[k] = all_stats.get(k, 0) + v

    yearly["_worker_trade_rows"] = all_trade_rows
    yearly["_worker_stats"] = all_stats

    return yearly
