#!/usr/bin/env python3
"""バックテストキューランナー（並行実行・CPUバジェット管理）

キューファイル（backtest_queue.json）を監視し、
CPUバジェット内で複数ジョブを並行実行する常駐スクリプト。

各ジョブは max_year_workers を申告し、1年=1.5スレッドとして
利用可能CPUスレッド数の範囲内で同時実行される。

再起動安全: completed_ids ベースで管理し、
中断されたジョブは再起動時に自動的に再実行される。

使い方:
    uv run python scripts/backtest_queue_runner.py --cpu-threads 12

対話コマンド:
    stop    - 全実行中ジョブ停止+ログ削除+キュー先頭に戻す
    pause   - 新規ジョブ取得を一時停止
    resume  - 一時停止解除
    status  - 現在の状態表示
    cpu N   - CPUスレッド数を動的変更（超過分は最新ジョブから停止）
    quit    - ランナー終了
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Windows cp932エンコーディング回避
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        errors="replace",
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer,
        encoding="utf-8",
        errors="replace",
    )
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# プロジェクトルート
try:
    _project_root = Path(__file__).parent.parent
except NameError:
    _project_root = Path("D:/Projects/AutoTraderV4")
sys.path.insert(0, str(_project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("queue_runner")

# パス定数（データ専用ディレクトリに出力）
_DATA_ROOT = Path("D:/Projects/AutoTraderV4_data")
QUEUE_FILE = _DATA_ROOT / "backtest_queue.json"
STATE_FILE = _DATA_ROOT / "backtest_queue_state.json"
RESULTS_DIR = _DATA_ROOT / "backtest_results"
DEFAULT_DATA_DIR = str(_DATA_ROOT / "data")

POLL_INTERVAL = 2.0  # キューポーリング間隔（秒）
THREADS_PER_YEAR = 1.5  # 1年あたりの必要CPUスレッド数


# ===================================================================
# データモデル
# ===================================================================


@dataclass
class Job:
    """バックテストジョブ"""

    id: str
    symbol: str = "USDJPY"
    years: str = "2023-2025"
    description: str = ""
    max_year_workers: int = 0  # 0=年数から自動計算
    overrides: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        """dictからJob生成"""
        return cls(
            id=d["id"],
            symbol=d.get("symbol", "USDJPY"),
            years=d.get("years", "2023-2025"),
            description=d.get("description", ""),
            max_year_workers=d.get("max_year_workers", 0),
            overrides=d.get("overrides", {}),
        )

    def effective_year_workers(self) -> int:
        """実効年並列数（0なら年数から自動計算）"""
        if self.max_year_workers > 0:
            return self.max_year_workers
        start, end = parse_years(self.years)
        return max(1, end - start + 1)

    def cpu_cost(self) -> float:
        """このジョブが消費するCPUスレッド数"""
        return self.effective_year_workers() * THREADS_PER_YEAR


@dataclass
class JobResult:
    """ジョブ実行結果"""

    job_id: str
    status: str = "pending"
    symbol: str = ""
    years: str = ""
    description: str = ""
    net_profit: float = 0.0
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    monthly_plus_rate: float = 0.0
    yearly_details: list[dict[str, Any]] = field(
        default_factory=list,
    )
    elapsed_seconds: float = 0.0
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""
    overrides_used: dict[str, Any] = field(
        default_factory=dict,
    )
    log_dir: str = ""
    trades_csv: str = ""
    summary_log: str = ""


@dataclass
class RunningJob:
    """実行中ジョブのトラッキング"""

    job: Job
    thread: threading.Thread
    cancel_event: threading.Event
    result_holder: list[JobResult | None]
    max_year_workers: int
    started_at: float  # time.time()

    @property
    def cpu_cost(self) -> float:
        """消費CPUスレッド数"""
        return self.max_year_workers * THREADS_PER_YEAR


@dataclass
class QueueState:
    """キュー処理状態（completed_ids ベース）

    next_index は廃止。再起動時は常にキュー先頭から
    スキャンし、completed_ids にあるジョブをスキップする。
    """

    completed_ids: list[str] = field(default_factory=list)

    def save(self) -> None:
        """状態ファイルに保存"""
        STATE_FILE.write_text(
            json.dumps(
                asdict(self), indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> QueueState:
        """状態ファイルから読み込み"""
        if STATE_FILE.exists():
            data = json.loads(
                STATE_FILE.read_text(encoding="utf-8"),
            )
            return cls(
                completed_ids=data.get(
                    "completed_ids", [],
                ),
            )
        return cls()


# ===================================================================
# 起動時クリーンアップ
# ===================================================================


def cleanup_stale_running(state: QueueState) -> None:
    """前回中断されたジョブの結果ファイルをクリーンアップ

    status が "running" の結果ファイルを削除し、
    再起動時に再実行されるようにする。
    """
    if not RESULTS_DIR.exists():
        return
    cleaned = 0
    for path in RESULTS_DIR.glob("*.json"):
        try:
            data = json.loads(
                path.read_text(encoding="utf-8"),
            )
            status = data.get("status", "")
            job_id = data.get("job_id", "")
            if status == "running":
                path.unlink()
                # completed_ids から除外（念のため）
                if job_id in state.completed_ids:
                    state.completed_ids.remove(job_id)
                cleaned += 1
                logger.info(
                    "クリーンアップ: %s (中断済み)",
                    path.name,
                )
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    if cleaned > 0:
        state.save()
        logger.info(
            "中断ジョブ %d件をクリーンアップ完了", cleaned,
        )


# ===================================================================
# キュー読み込み
# ===================================================================


def load_queue() -> list[Job]:
    """キューファイルからジョブリスト読み込み"""
    if not QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(
            QUEUE_FILE.read_text(encoding="utf-8"),
        )
        jobs_raw = data.get("jobs", [])
        return [Job.from_dict(j) for j in jobs_raw]
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("キューファイル読み込みエラー: %s", e)
        return []


# ===================================================================
# ジョブ実行
# ===================================================================


def parse_years(years_str: str) -> tuple[int, int]:
    """年範囲パース"""
    if "-" in years_str:
        parts = years_str.split("-")
        return int(parts[0]), int(parts[1])
    y = int(years_str)
    return y, y


def execute_job(
    job: Job,
    cancel_event: threading.Event,
    max_year_workers: int = 5,
) -> JobResult:
    """バックテストジョブを実行

    Args:
        job: 実行するジョブ
        cancel_event: キャンセルイベント
        max_year_workers: 年並列実行数

    Returns:
        JobResult: 実行結果
    """
    from autotrader.backtest.service import (
        BacktestService,
        BacktestServiceConfig,
    )
    from autotrader.config.trading_params import (
        get_preset,
        get_symbol_overrides,
    )
    from autotrader.decision.unified import UnifiedBotConfig
    from autotrader.decision.unified.position_manager import (
        PositionManagerConfig,
    )

    result = JobResult(
        job_id=job.id,
        status="running",
        symbol=job.symbol,
        years=job.years,
        description=job.description,
        started_at=datetime.now().isoformat(),
        overrides_used=job.overrides,
    )
    _save_result(result)

    start_time = time.time()
    start_year, end_year = parse_years(job.years)

    try:
        # プリセット取得
        preset = get_preset(job.symbol)
        sym_ovr = get_symbol_overrides(job.symbol)

        # bot overrides 構築
        pip_unit = (
            0.01 if "JPY" in job.symbol.upper() else 0.0001
        )
        bot_ovr: dict[str, Any] = {}
        # L1: プリセット
        bot_ovr.update({
            "max_positions": preset.max_positions,
            "bonus_max_positions": (
                preset.bonus_max_positions
            ),
            "bonus_score_threshold": (
                preset.bonus_score_threshold
            ),
            "base_risk_pct": preset.base_risk_pct,
            "max_lot_per_trade": preset.max_lot_per_trade,
            "max_total_exposure_lot": (
                preset.max_total_exposure_lot
            ),
            "equity_floor_pct": preset.equity_floor_pct,
            "pip_unit": pip_unit,
        })
        # L2: ペア別 signal/filter/risk_mgmt
        bot_ovr.update(sym_ovr.get("signal", {}))
        bot_ovr.update(sym_ovr.get("filter", {}))
        bot_ovr.update(sym_ovr.get("risk_mgmt", {}))
        # L3: ジョブ指定 overrides（最高優先）
        bot_ovr.update(job.overrides.get("bot", {}))

        # pm overrides 構築
        pm_ovr: dict[str, Any] = {}
        pm_ovr.update(sym_ovr.get("pm_config", {}))
        pm_ovr.update(job.overrides.get("pm", {}))

        bot_config = UnifiedBotConfig(**bot_ovr)
        pm_config = PositionManagerConfig(**pm_ovr)

        # バックテスト設定
        bt_ovr = job.overrides.get("backtest", {})
        data_dir = bt_ovr.get(
            "data_dir", DEFAULT_DATA_DIR,
        )
        initial_balance = bt_ovr.get(
            "initial_balance", 1_000_000,
        )

        svc_config = BacktestServiceConfig(
            start_year=start_year,
            end_year=end_year,
            initial_balance=initial_balance,
            data_dir=data_dir,
            symbol=job.symbol,
            spread_pips=preset.spread_pips,
            slippage_pips=preset.slippage_pips,
            max_positions=preset.max_positions,
            bonus_max_positions=(
                bot_config.bonus_max_positions
            ),
            bonus_score_threshold=(
                bot_config.bonus_score_threshold
            ),
            pip_value=preset.pip_value,
            commission_per_lot=preset.commission_per_lot,
            use_short_timeframe=True,
        )

        service = BacktestService(svc_config)
        runner = service.create_runner()

        # キャンセルコールバック設定
        runner.set_cancel_callback(cancel_event.is_set)

        # ログパスを記録
        _fl = runner._file_listener
        if _fl is not None:
            result.log_dir = str(_fl.log_dir)
            result.trades_csv = str(_fl.trades_file)
            result.summary_log = str(
                _fl.summary_file,
            )

        # データ読み込み
        logger.info(
            "[%s] データ読み込み中... (%s %s, workers=%d)",
            job.id,
            job.symbol,
            job.years,
            max_year_workers,
        )
        runner.load_data()

        # バックテスト実行
        logger.info("[%s] バックテスト実行中...", job.id)
        bt_result = runner.run_unified(
            start_year=start_year,
            end_year=end_year,
            config=bot_config,
            pm_config=pm_config,
            use_m1=True,
            max_year_workers=max_year_workers,
        )

        if cancel_event.is_set():
            result.status = "cancelled"
            result.error = "ユーザーにより停止"
        else:
            result.status = "completed"
            result.net_profit = bt_result.net_profit
            result.trades = bt_result.trades
            result.win_rate = bt_result.win_rate
            result.profit_factor = bt_result.profit_factor
            result.max_drawdown = bt_result.max_drawdown
            result.sharpe_ratio = bt_result.sharpe_ratio
            result.yearly_details = (
                bt_result.yearly_results
            )

            # 月間プラス率を計算
            if bt_result.monthly_results:
                plus_months = sum(
                    1
                    for m in bt_result.monthly_results
                    if m.get("profit", 0) > 0
                )
                total = len(bt_result.monthly_results)
                result.monthly_plus_rate = (
                    plus_months / total * 100
                    if total > 0
                    else 0
                )

    except Exception as e:
        result.status = "failed"
        result.error = str(e)
        logger.exception("[%s] ジョブ失敗: %s", job.id, e)

    result.elapsed_seconds = round(
        time.time() - start_time, 1,
    )
    result.finished_at = datetime.now().isoformat()
    _save_result(result)
    return result


def _save_result(result: JobResult) -> None:
    """結果をファイルに保存"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{result.job_id}.json"
    path.write_text(
        json.dumps(
            asdict(result),
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )


# ===================================================================
# 対話コマンドリーダー
# ===================================================================


def stdin_reader(
    cmd_queue: "import queue; queue.Queue[str]",
) -> None:
    """stdinからコマンドを読み取るスレッド"""
    while True:
        try:
            line = input().strip()
            if line:
                cmd_queue.put(line)
        except EOFError:
            break
        except Exception:
            break


# ===================================================================
# CPUバジェット管理
# ===================================================================


def calc_used_threads(
    running: list[RunningJob],
) -> float:
    """実行中ジョブの合計CPUスレッド消費量"""
    return sum(rj.cpu_cost for rj in running)


def stop_newest_jobs_until_budget(
    running: list[RunningJob],
    cpu_threads: int,
    state: QueueState,
) -> None:
    """CPUバジェット超過時、最新ジョブから停止

    Args:
        running: 実行中ジョブリスト（変更される）
        cpu_threads: 利用可能CPUスレッド数
        state: キュー状態
    """
    # 開始時刻の新しい順にソート
    by_newest = sorted(
        running,
        key=lambda rj: rj.started_at,
        reverse=True,
    )
    for rj in by_newest:
        if calc_used_threads(running) <= cpu_threads:
            break
        logger.info(
            ">>> CPU超過: [%s] を停止中 (cost=%.1f)...",
            rj.job.id,
            rj.cpu_cost,
        )
        rj.cancel_event.set()
        rj.thread.join(timeout=15)
        # ログ削除
        _rpath = RESULTS_DIR / f"{rj.job.id}.json"
        if _rpath.exists():
            _rpath.unlink()
            logger.info(">>> ログ削除: %s", _rpath.name)
        # completed_ids から除外（再実行対象に戻す）
        if rj.job.id in state.completed_ids:
            state.completed_ids.remove(rj.job.id)
        running.remove(rj)
    state.save()


# ===================================================================
# メインループ
# ===================================================================


def main() -> None:
    """キューランナーのメインループ"""
    import queue as _q  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description="バックテストキューランナー",
    )
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=os.cpu_count() or 4,
        help="使用可能なCPUスレッド数（デフォルト: OS検出値）",
    )
    cli_args = parser.parse_args()

    cpu_threads: int = cli_args.cpu_threads

    print("=" * 60)
    print("  バックテストキューランナー（並行実行）")
    print("=" * 60)
    print(f"  キューファイル: {QUEUE_FILE}")
    print(f"  結果ディレクトリ: {RESULTS_DIR}")
    print(f"  ポーリング間隔: {POLL_INTERVAL}s")
    print(
        f"  CPUスレッド: {cpu_threads}"
        f" (1年={THREADS_PER_YEAR}スレッド)",
    )
    print()
    print("  コマンド:")
    print("    stop   - 全ジョブ停止+ログ削除+キュー先頭")
    print("    pause  - 新規ジョブ取得を一時停止")
    print("    resume - 一時停止解除")
    print("    status - 現在の状態表示")
    print("    cpu N  - CPUスレッド数を変更")
    print("    quit   - ランナー終了")
    print("=" * 60)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # 状態読み込み + 中断ジョブのクリーンアップ
    state = QueueState.load()
    cleanup_stale_running(state)

    # コマンドキュー
    cmd_queue: _q.Queue[str] = _q.Queue()

    # stdinリーダースレッド
    reader_thread = threading.Thread(
        target=stdin_reader,
        args=(cmd_queue,),
        daemon=True,
    )
    reader_thread.start()

    # 状態
    paused = False
    running_jobs: list[RunningJob] = []

    def _run_job_wrapper(
        job: Job,
        cancel_ev: threading.Event,
        holder: list[JobResult | None],
        workers: int,
    ) -> None:
        """ジョブ実行スレッド"""
        holder[0] = execute_job(job, cancel_ev, workers)

    while True:
        # -------------------------------------------------------
        # コマンド処理
        # -------------------------------------------------------
        try:
            while True:
                raw_cmd = cmd_queue.get_nowait()
                cmd = raw_cmd.lower().strip()

                if cmd == "stop":
                    if running_jobs:
                        logger.info(
                            ">>> 全ジョブ停止中"
                            " (%d件)...",
                            len(running_jobs),
                        )
                        for rj in running_jobs:
                            rj.cancel_event.set()
                        for rj in running_jobs:
                            rj.thread.join(timeout=15)
                            _rp = (
                                RESULTS_DIR
                                / f"{rj.job.id}.json"
                            )
                            if _rp.exists():
                                _rp.unlink()
                                logger.info(
                                    ">>> ログ削除: %s",
                                    _rp.name,
                                )
                        running_jobs.clear()
                        # 全リセット
                        state.completed_ids.clear()
                        state.save()
                        logger.info(
                            ">>> キュー先頭にリセット",
                        )
                    else:
                        logger.info(
                            ">>> 実行中ジョブなし",
                        )

                elif cmd == "pause":
                    paused = True
                    logger.info(">>> 一時停止")

                elif cmd == "resume":
                    paused = False
                    logger.info(">>> 再開")

                elif cmd.startswith("cpu"):
                    parts = cmd.split()
                    if len(parts) == 2:
                        try:
                            new_cpu = int(parts[1])
                            if new_cpu < 1:
                                new_cpu = 1
                            old_cpu = cpu_threads
                            cpu_threads = new_cpu
                            logger.info(
                                ">>> CPUスレッド: "
                                "%d → %d",
                                old_cpu,
                                cpu_threads,
                            )
                            # 超過チェック
                            used = calc_used_threads(
                                running_jobs,
                            )
                            if used > cpu_threads:
                                logger.info(
                                    ">>> 使用中: %.1f"
                                    " > 上限: %d"
                                    " → 最新ジョブから停止",
                                    used,
                                    cpu_threads,
                                )
                                stop_newest_jobs_until_budget(
                                    running_jobs,
                                    cpu_threads,
                                    state,
                                )
                        except ValueError:
                            logger.error(
                                ">>> 無効な値: %s"
                                " (例: cpu 8)",
                                parts[1],
                            )
                    else:
                        logger.info(
                            ">>> 現在のCPUスレッド:"
                            " %d (使用例: cpu 8)",
                            cpu_threads,
                        )

                elif cmd == "status":
                    _jobs = load_queue()
                    used = calc_used_threads(
                        running_jobs,
                    )
                    _done = len(state.completed_ids)
                    _total = len(_jobs)
                    _remain = _total - _done
                    print(
                        f"  状態: "
                        f"{'一時停止' if paused else '稼働中'}"
                    )
                    print(
                        f"  CPUスレッド: "
                        f"{used:.1f}/{cpu_threads} 使用中"
                    )
                    print(
                        f"  実行中ジョブ: "
                        f"{len(running_jobs)}件"
                    )
                    for rj in running_jobs:
                        elapsed = (
                            time.time() - rj.started_at
                        )
                        print(
                            f"    - [{rj.job.id}]"
                            f" {rj.job.symbol}"
                            f" {rj.job.years}"
                            f" workers="
                            f"{rj.max_year_workers}"
                            f" cost={rj.cpu_cost:.1f}"
                            f" ({elapsed:.0f}s)"
                        )
                    print(
                        f"  進捗: {_done}/{_total}"
                        f" (残り{_remain}件)"
                    )

                elif cmd == "quit":
                    if running_jobs:
                        logger.info(
                            ">>> 全ジョブ停止中...",
                        )
                        for rj in running_jobs:
                            rj.cancel_event.set()
                        for rj in running_jobs:
                            rj.thread.join(timeout=15)
                    logger.info(">>> ランナー終了")
                    return

        except _q.Empty:
            pass

        # -------------------------------------------------------
        # 完了ジョブの回収
        # -------------------------------------------------------
        finished: list[RunningJob] = []
        for rj in running_jobs:
            if not rj.thread.is_alive():
                finished.append(rj)

        for rj in finished:
            _res = rj.result_holder[0]
            if _res and _res.status == "completed":
                logger.info(
                    "[%s] 完了: profit=%.0f,"
                    " WR=%.1f%%, PF=%.2f, DD=%.2f%%"
                    " (%.0fs)",
                    _res.job_id,
                    _res.net_profit,
                    _res.win_rate,
                    _res.profit_factor,
                    _res.max_drawdown,
                    _res.elapsed_seconds,
                )
                if _res.job_id not in state.completed_ids:
                    state.completed_ids.append(
                        _res.job_id,
                    )
                state.save()
            elif _res and _res.status == "cancelled":
                logger.info(
                    "[%s] キャンセル済み", _res.job_id,
                )
            elif _res and _res.status == "failed":
                logger.error(
                    "[%s] 失敗: %s",
                    _res.job_id,
                    _res.error,
                )
                state.save()
            running_jobs.remove(rj)

        # -------------------------------------------------------
        # 新規ジョブ取得（CPUバジェット内で複数起動）
        # 常にインデックス0からスキャン（再起動安全）
        # -------------------------------------------------------
        if not paused:
            jobs = load_queue()
            running_ids = {
                rj.job.id for rj in running_jobs
            }
            for job in jobs:
                # 完了済みスキップ
                if job.id in state.completed_ids:
                    continue

                # 実行中スキップ
                if job.id in running_ids:
                    continue

                # 結果ファイルで完了済みならスキップ
                _existing = (
                    RESULTS_DIR / f"{job.id}.json"
                )
                if _existing.exists():
                    try:
                        _ex = json.loads(
                            _existing.read_text(
                                encoding="utf-8",
                            ),
                        )
                        if (
                            _ex.get("status")
                            == "completed"
                        ):
                            logger.info(
                                "[%s] スキップ"
                                "（結果ファイルあり）",
                                job.id,
                            )
                            if (
                                job.id
                                not in state.completed_ids
                            ):
                                state.completed_ids.append(
                                    job.id,
                                )
                                state.save()
                            continue
                    except (
                        json.JSONDecodeError,
                        KeyError,
                    ):
                        pass

                # CPUバジェットチェック
                workers = job.effective_year_workers()
                cost = job.cpu_cost()
                used = calc_used_threads(running_jobs)
                remaining = cpu_threads - used

                if cost > remaining:
                    # バジェット不足 → 次サイクルで
                    break

                # ジョブ起動
                logger.info(
                    "[%s] 開始: %s %s %s"
                    " (workers=%d, cost=%.1f,"
                    " used=%.1f/%.0f)",
                    job.id,
                    job.symbol,
                    job.years,
                    job.description,
                    workers,
                    cost,
                    used + cost,
                    cpu_threads,
                )
                cancel_ev = threading.Event()
                holder: list[JobResult | None] = [None]
                t = threading.Thread(
                    target=_run_job_wrapper,
                    args=(
                        job, cancel_ev, holder, workers,
                    ),
                    daemon=True,
                )
                rj_new = RunningJob(
                    job=job,
                    thread=t,
                    cancel_event=cancel_ev,
                    result_holder=holder,
                    max_year_workers=workers,
                    started_at=time.time(),
                )
                running_jobs.append(rj_new)
                running_ids.add(job.id)
                t.start()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
