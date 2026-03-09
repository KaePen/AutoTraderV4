"""並列バックテストワーカーモジュール

ProcessPoolExecutor用のモジュールレベル関数。
pickle可能な関数として年単位のバックテストを並列実行する。
"""

from __future__ import annotations

from typing import Any

# ワーカープロセスの進捗キュー（initializerが設定）
_WORKER_PROGRESS_QUEUE: Any = None


def _worker_process_init(
    project_root: str,
    progress_queue: Any,
) -> None:
    """ProcessPoolワーカープロセス初期化

    spawn起動後にPythonパスと進捗キューを設定する。
    各ワーカープロセスで一度だけ呼ばれる。

    Args:
        project_root: プロジェクトルートパス
        progress_queue: メインプロセスへの進捗通知キュー
    """
    import sys

    global _WORKER_PROGRESS_QUEUE
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    _WORKER_PROGRESS_QUEUE = progress_queue


def _run_year_worker(
    task_args: tuple,
) -> "dict[str, Any] | None":
    """年バックテストワーカー関数（ProcessPool用モジュールレベル）

    ProcessPoolExecutorから呼び出されるpicklable関数。
    サブプロセス内でBacktestRunnerを再構築して_run_unified_yearを実行。
    進捗はグローバルキュー経由でメインプロセスへ送信する。

    Args:
        task_args: (data_dir_base, backtest_config, bot_config,
                    sim_config, year, year_market_data,
                    use_m1, fundamental_provider,
                    period_start, period_end) のタプル

    Returns:
        年別結果 または None
    """
    from autotrader.backtest.events import BacktestEventEmitter
    from autotrader.backtest.runner import BacktestRunner
    from autotrader.backtest.year_runner import run_unified_year

    # adaptive_config は後方互換のためオプション
    if len(task_args) >= 11:
        (
            data_dir_base,
            backtest_config,
            bot_config,
            sim_config,
            year,
            year_market_data,
            use_m1,
            fundamental_provider,
            period_start,
            period_end,
            adaptive_config,
        ) = task_args
    else:
        (
            data_dir_base,
            backtest_config,
            bot_config,
            sim_config,
            year,
            year_market_data,
            use_m1,
            fundamental_provider,
            period_start,
            period_end,
        ) = task_args
        adaptive_config = None

    # サブプロセス内でRunnerを最小設定で再構築（リスナーなし）
    runner = BacktestRunner(
        data_dir=data_dir_base,
        config=backtest_config,
        verbose=False,
        log_to_file=False,
    )

    # 全時間足のインスタンス変数を設定（run_unified_yearが参照）
    runner._m1_df = year_market_data.get("M1")
    runner._m5_df = year_market_data.get("M5")
    runner._m15_df = year_market_data.get("M15")
    runner._m30_df = year_market_data.get("M30")
    runner._h1_df = year_market_data.get("H1")
    runner._h4_df = year_market_data.get("H4")
    runner._h8_df = year_market_data.get("H8")
    runner._d1_df = year_market_data.get("D1")

    # 進捗コールバック → グローバルキューに送信
    def _progress_cb(done: int, total: int) -> None:
        if _WORKER_PROGRESS_QUEUE is not None:
            try:
                _WORKER_PROGRESS_QUEUE.put_nowait(
                    {"year": year, "done": done, "total": total}
                )
            except Exception:
                pass

    from autotrader.backtest.file_listener import (
        TradeRowCollector,
    )

    # トレードデータ収集用エミッターとコレクターを設定
    _emitter = BacktestEventEmitter()
    _collector = TradeRowCollector()
    _emitter.add_listener(_collector)

    result = run_unified_year(
        runner=runner,
        bot_config=bot_config,
        sim_config=sim_config,
        year=year,
        market_data=year_market_data,
        use_m1=use_m1,
        fundamental_provider=fundamental_provider,
        period_start=period_start,
        period_end=period_end,
        emitter=_emitter,
        row_progress_callback=_progress_cb,
        adaptive_config=adaptive_config,
    )

    # 収集したトレードデータを結果に付加
    if result is not None:
        result["_worker_trade_rows"] = (
            _collector._trade_rows
        )
        result["_worker_stats"] = _collector.get_stats()

    return result
