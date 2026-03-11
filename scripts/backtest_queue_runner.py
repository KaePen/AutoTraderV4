#!/usr/bin/env python3
"""バックテストキューランナー（月レベルスケジューラ）

月タスク = スケジューリングの原子単位 = 1CPU。
キュー先頭のジョブから月タスクを生成し、CPUスロットを埋める。
月完了 → 即保存 → 次の月タスクを投入 → CPUを常に使い切る。

再起動安全: month_results/ ディレクトリベースのチェックポイントで
中断された月をスキップし残月のみ再実行。

使い方:
    uv run python scripts/backtest_queue_runner.py --cpu-threads 12

対話コマンド:
    stop    - 全実行中タスク停止+キュー先頭に戻す
    pause   - 新規タスク取得を一時停止
    resume  - 一時停止解除
    status  - 現在の状態表示
    cpu N   - CPUスレッド数を動的変更
    quit    - ランナー終了
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
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
MONTH_RESULTS_DIR = _DATA_ROOT / "month_results"
DEFAULT_DATA_DIR = str(_DATA_ROOT / "data")

POLL_INTERVAL = 2.0  # キューポーリング間隔（秒）

# Web UI連携用ファイル
RUNNER_STATE_FILE = _DATA_ROOT / "runner_state.json"
RUNNER_CMD_FILE = _DATA_ROOT / "runner_commands.json"
WORKER_PROGRESS_DIR = _DATA_ROOT / "worker_progress"


# ===================================================================
# データモデル
# ===================================================================


@dataclass
class Job:
    """バックテストジョブ

    type が "single" の場合は1通貨ペア、
    "multi_pair" の場合は時系列インターリーブ方式で
    複数ペアを共有エクイティプールで同時実行する。
    後方互換のため "portfolio" も "multi_pair" として処理する。
    """

    id: str
    type: str = "single"  # "single" or "multi_pair" ("portfolio"も可)
    symbol: str = "USDJPY"  # single用
    symbols: list[str] = field(
        default_factory=list,
    )  # multi_pair用
    years: str = "2023-2025"
    description: str = ""
    max_year_workers: int = 0  # 後方互換（無視される）
    overrides: dict[str, Any] = field(default_factory=dict)
    multi_pair_config: dict[str, Any] = field(
        default_factory=dict,
    )
    code_dir: str = ""  # 指定時はそのディレクトリのコードで実行

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        """dictからJob生成"""
        return cls(
            id=d["id"],
            type=d.get("type", "single"),
            symbol=d.get("symbol", "USDJPY"),
            symbols=d.get("symbols", []),
            years=d.get("years", "2023-2025"),
            description=d.get("description", ""),
            max_year_workers=d.get("max_year_workers", 0),
            overrides=d.get("overrides", {}),
            multi_pair_config=d.get(
                "multi_pair_config",
                {},
            ),
            code_dir=d.get("code_dir", ""),
        )


@dataclass
class JobResult:
    """ジョブ実行結果"""

    job_id: str
    status: str = "pending"
    job_type: str = "single"
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
    # ポートフォリオ用追加フィールド
    portfolio_metrics: dict[str, Any] = field(
        default_factory=dict,
    )
    pair_details: list[dict[str, Any]] = field(
        default_factory=list,
    )


@dataclass
class MonthTask:
    """月タスク（スケジューリングの原子単位）

    CPUコスト: シングル=1.2, マルチペア=1.5
    """

    job_id: str  # 元ジョブID
    result_id: str  # 連番付きID ("003_usdjpy-verify")
    job_type: str  # "single" / "multi_pair"
    year: int
    month: int  # 1-12
    job_dict: dict  # Job定義（サブプロセスに渡す）
    cpu_cost: float = 1.0  # スケジューリング時のCPUコスト


# ジョブタイプ別CPUコスト
CPU_COST_SINGLE: float = 1.2
CPU_COST_MULTI_PAIR: float = 1.5


def _cpu_cost_for_type(job_type: str) -> float:
    """ジョブタイプからCPUコストを返す"""
    if job_type == "multi_pair":
        return CPU_COST_MULTI_PAIR
    return CPU_COST_SINGLE


def _current_cpu_load(
    running_tasks: list[RunningMonthTask],
) -> float:
    """実行中タスクの合計CPUコストを返す"""
    return sum(rt.task.cpu_cost for rt in running_tasks)


@dataclass
class RunningMonthTask:
    """実行中月タスクのトラッキング"""

    task: MonthTask
    process: subprocess.Popen  # type: ignore[type-arg]
    started_at: float  # time.time()


@dataclass
class JobProgress:
    """ジョブ進捗"""

    job_id: str
    result_id: str
    job_type: str
    symbol: str  # 表示用
    years: str
    description: str
    total_months: int  # 例: 36 (3年×12月)
    completed_months: set[tuple[int, int]] = field(
        default_factory=set,
    )
    status: str = "pending"  # "pending" / "in_progress" / "completed"
    started_at: float = 0.0

    @property
    def completed_count(self) -> int:
        return len(self.completed_months)

    @property
    def pct(self) -> float:
        if self.total_months <= 0:
            return 0.0
        return self.completed_count / self.total_months * 100


def _get_queue_job_ids() -> set[str]:
    """キューファイルのジョブID一覧を取得"""
    if not QUEUE_FILE.exists():
        return set()
    try:
        data = json.loads(
            QUEUE_FILE.read_text(encoding="utf-8"),
        )
        return {
            j.get("id", "")
            for j in data.get("jobs", [])
        }
    except (json.JSONDecodeError, KeyError):
        return set()


def _compute_queue_hash() -> str:
    """キューファイルのジョブID一覧からハッシュを計算"""
    ids = sorted(_get_queue_job_ids())
    if not ids:
        return ""
    content = json.dumps(ids)
    return hashlib.sha256(
        content.encode(),
    ).hexdigest()[:16]


@dataclass
class QueueState:
    """キュー処理状態（completed_ids ベース）

    再起動時は常にキュー先頭からスキャンし、
    completed_ids にあるジョブをスキップする。
    job_counter はグローバル連番で結果ファイル名の
    一意性を保証する。
    """

    completed_ids: list[str] = field(default_factory=list)
    job_counter: int = 0
    queue_hash: str = ""

    def sync_with_queue(self) -> None:
        """キュー変更を検知し、削除されたジョブのみ除去"""
        current_hash = _compute_queue_hash()
        if not current_hash:
            return
        if self.queue_hash and self.queue_hash != current_hash:
            current_ids = _get_queue_job_ids()
            removed = [
                cid for cid in self.completed_ids
                if cid not in current_ids
            ]
            if removed:
                for cid in removed:
                    self.completed_ids.remove(cid)
                logger.info(
                    "キュー変更検知: 削除済み%d件を除去 %s",
                    len(removed),
                    removed,
                )
            else:
                logger.info(
                    "キュー変更検知: ジョブ追加のみ"
                    "（完了記録%d件を保持）",
                    len(self.completed_ids),
                )
            self.queue_hash = current_hash
            self.save()
        elif not self.queue_hash:
            self.queue_hash = current_hash
            self.save()

    def next_counter(self) -> int:
        """連番を発行して保存"""
        self.job_counter += 1
        self.save()
        return self.job_counter

    def save(self) -> None:
        """状態ファイルに保存"""
        STATE_FILE.write_text(
            json.dumps(
                asdict(self),
                indent=2,
                ensure_ascii=False,
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
                    "completed_ids",
                    [],
                ),
                job_counter=data.get(
                    "job_counter",
                    0,
                ),
                queue_hash=data.get(
                    "queue_hash",
                    "",
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
            "中断ジョブ %d件をクリーンアップ完了",
            cleaned,
        )


def cleanup_worker_progress() -> None:
    """起動時にワーカー進捗ファイルを全削除"""
    if not WORKER_PROGRESS_DIR.exists():
        return
    cleaned = 0
    for path in WORKER_PROGRESS_DIR.glob("*.json"):
        with contextlib.suppress(OSError):
            path.unlink()
            cleaned += 1
    if cleaned > 0:
        logger.info(
            "ワーカー進捗ファイル %d件をクリーンアップ",
            cleaned,
        )
    # 旧形式（_DATA_ROOT直下）も掃除
    for path in _DATA_ROOT.glob(".worker_progress_*.json"):
        with contextlib.suppress(OSError):
            path.unlink()


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


def _remove_job_from_queue(job_id: str) -> None:
    """完了ジョブをキューファイルから除去"""
    if not QUEUE_FILE.exists():
        return
    try:
        data = json.loads(
            QUEUE_FILE.read_text(encoding="utf-8"),
        )
        jobs_raw = data.get("jobs", [])
        before = len(jobs_raw)
        data["jobs"] = [
            j for j in jobs_raw
            if j.get("id", "") != job_id
        ]
        if len(data["jobs"]) < before:
            QUEUE_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            logger.info(
                "キューから完了ジョブ除去: %s", job_id,
            )
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            "キュー更新失敗（無視）: %s", e,
        )


# ===================================================================
# ユーティリティ
# ===================================================================


def parse_years(years_str: str) -> tuple[int, int]:
    """年範囲パース"""
    if "-" in years_str:
        parts = years_str.split("-")
        return int(parts[0]), int(parts[1])
    y = int(years_str)
    return y, y


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
# 月タスク生成・チェックポイント
# ===================================================================


def _get_completed_months(
    result_id: str,
) -> set[tuple[int, int]]:
    """月結果ディレクトリから完了月を取得

    Args:
        result_id: 結果ID

    Returns:
        完了月のセット {(year, month), ...}
    """
    result_dir = MONTH_RESULTS_DIR / result_id
    completed: set[tuple[int, int]] = set()
    if not result_dir.exists():
        return completed
    for path in result_dir.glob("*.json"):
        name = path.stem
        # year_YYYY_MM.json 形式はスキップ（年集約）
        if name.startswith("year_"):
            continue
        parts = name.split("_")
        if len(parts) == 2:
            try:
                yr = int(parts[0])
                mo = int(parts[1])
                completed.add((yr, mo))
            except ValueError:
                continue
    return completed


def generate_pending_months(
    job: Job,
    result_id: str,
) -> list[MonthTask]:
    """ジョブから未完了月タスクを生成

    Args:
        job: ジョブ定義
        result_id: 結果ID

    Returns:
        未完了の月タスクリスト（年月順）
    """
    start_year, end_year = parse_years(job.years)
    completed = _get_completed_months(result_id)

    tasks: list[MonthTask] = []
    for yr in range(start_year, end_year + 1):
        for mo in range(1, 13):
            if (yr, mo) in completed:
                continue
            tasks.append(
                MonthTask(
                    job_id=job.id,
                    result_id=result_id,
                    job_type=job.type,
                    year=yr,
                    month=mo,
                    job_dict=asdict(job),
                    cpu_cost=_cpu_cost_for_type(job.type),
                )
            )
    return tasks


def _is_year_complete(
    result_id: str,
    year: int,
) -> bool:
    """指定年の12ヶ月が全て完了しているか"""
    completed = _get_completed_months(result_id)
    return all((year, mo) in completed for mo in range(1, 13))


def _is_job_complete(
    job: Job,
    result_id: str,
) -> bool:
    """ジョブの全月完了 かつ 全年集約ファイル存在を確認

    月完了と年集約ファイル書出しにタイムラグがあるため、
    年集約ファイルの存在も確認して集約の早期実行を防ぐ。
    """
    start_year, end_year = parse_years(job.years)
    for yr in range(start_year, end_year + 1):
        if not _is_year_complete(result_id, yr):
            return False
        # 年集約ファイルも存在するか確認
        year_path = (
            MONTH_RESULTS_DIR / result_id
            / f"year_{yr}.json"
        )
        if not year_path.exists():
            return False
    return True


# ===================================================================
# 月結果の読み込み
# ===================================================================


def _load_month_results(
    result_id: str,
    year: int,
) -> list[dict[str, Any]]:
    """指定年の12ヶ月結果を読み込み

    Args:
        result_id: 結果ID
        year: 対象年

    Returns:
        月結果リスト（月順ソート済み）
    """
    result_dir = MONTH_RESULTS_DIR / result_id
    results: list[dict[str, Any]] = []
    for mo in range(1, 13):
        path = result_dir / f"{year}_{mo:02d}.json"
        if path.exists():
            try:
                data = json.loads(
                    path.read_text(encoding="utf-8"),
                )
                results.append(data)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "月結果読み込み失敗 %s: %s",
                    path.name,
                    e,
                )
    results.sort(key=lambda r: r.get("month", 0))
    return results


# ===================================================================
# 年集約
# ===================================================================


def _aggregate_year_single(
    result_id: str,
    year: int,
) -> dict[str, Any] | None:
    """単独BTの年集約

    month_runner._aggregate_monthly_to_yearly を再利用。

    Args:
        result_id: 結果ID
        year: 対象年

    Returns:
        年次結果辞書
    """
    from autotrader.backtest.month_runner import (
        _aggregate_monthly_to_yearly,
    )

    monthly = _load_month_results(result_id, year)
    if not monthly:
        return None

    initial_balance = monthly[0].get(
        "initial_balance", 1_000_000,
    )
    yearly = _aggregate_monthly_to_yearly(
        monthly, year, initial_balance,
    )

    # 年集約結果を保存
    year_path = (
        MONTH_RESULTS_DIR / result_id / f"year_{year}.json"
    )
    year_path.write_text(
        json.dumps(
            yearly, indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    logger.info(
        "[%s] %d年集約完了: trades=%d, profit=%.0f",
        result_id,
        year,
        yearly.get("trades", 0),
        yearly.get("net_profit", 0.0),
    )
    return yearly


def _aggregate_year_multi_pair(
    result_id: str,
    year: int,
) -> dict[str, Any] | None:
    """マルチペアBTの年集約

    12ヶ月分の pair_summaries, monthly_pnl, blocked_* を合算。

    Args:
        result_id: 結果ID
        year: 対象年

    Returns:
        年次結果辞書（aggregate_year_results互換）
    """
    monthly = _load_month_results(result_id, year)
    if not monthly:
        return None

    year_pnl = 0.0
    year_trades = 0
    initial_equity = monthly[0].get(
        "initial_equity", 1_000_000,
    )
    final_equity = initial_equity

    # 月次PnL
    monthly_pnl: dict[str, float] = {}
    # ペア別集計
    pair_summaries: dict[str, dict[str, Any]] = {}
    max_dd_pct = 0.0
    blocked_global = 0
    blocked_per_pair = 0
    blocked_exposure = 0

    for mr in monthly:
        _pnl = mr.get("year_pnl", 0.0)
        year_pnl += _pnl
        year_trades += mr.get("year_trades", 0)

        # 月次PnL
        for key, pnl in mr.get("monthly_pnl", {}).items():
            monthly_pnl[key] = (
                monthly_pnl.get(key, 0.0) + pnl
            )

        # DD
        _dd = mr.get("max_dd_pct", 0.0)
        if _dd > max_dd_pct:
            max_dd_pct = _dd

        # blocked
        blocked_global += mr.get("blocked_global", 0)
        blocked_per_pair += mr.get("blocked_per_pair", 0)
        blocked_exposure += mr.get("blocked_exposure", 0)

        # ペア別
        for sym, ps in mr.get("pair_summaries", {}).items():
            if sym not in pair_summaries:
                pair_summaries[sym] = {
                    "trades": 0,
                    "wins": 0,
                    "gross_profit": 0.0,
                    "gross_loss": 0.0,
                    "net_profit": 0.0,
                }
            agg = pair_summaries[sym]
            agg["trades"] += ps.get("trades", 0)
            agg["wins"] += ps.get("wins", 0)
            agg["gross_profit"] += ps.get("gross_profit", 0.0)
            agg["gross_loss"] += ps.get("gross_loss", 0.0)
            agg["net_profit"] += ps.get("net_profit", 0.0)

    final_equity = initial_equity + year_pnl

    yearly = {
        "year": year,
        "year_pnl": year_pnl,
        "year_trades": year_trades,
        "final_equity": final_equity,
        "max_dd_pct": max_dd_pct,
        "monthly_pnl": monthly_pnl,
        "pair_summaries": pair_summaries,
        "blocked_global": blocked_global,
        "blocked_per_pair": blocked_per_pair,
        "blocked_exposure": blocked_exposure,
    }

    # 年集約結果を保存
    year_path = (
        MONTH_RESULTS_DIR / result_id / f"year_{year}.json"
    )
    year_path.write_text(
        json.dumps(
            yearly, indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    logger.info(
        "[%s] %d年マルチペア集約完了:"
        " trades=%d, pnl=%.0f",
        result_id,
        year,
        year_trades,
        year_pnl,
    )
    return yearly


def aggregate_year(
    result_id: str,
    year: int,
    job_type: str,
) -> dict[str, Any] | None:
    """年集約（タイプ振り分け）"""
    if job_type in ("multi_pair", "portfolio"):
        return _aggregate_year_multi_pair(result_id, year)
    return _aggregate_year_single(result_id, year)


# ===================================================================
# ジョブ集約
# ===================================================================


def _aggregate_job_single(
    job: Job,
    result_id: str,
) -> JobResult:
    """単独BTジョブ全体集約"""
    start_year, end_year = parse_years(job.years)
    yearly_results: list[dict[str, Any]] = []

    for yr in range(start_year, end_year + 1):
        year_path = (
            MONTH_RESULTS_DIR / result_id / f"year_{yr}.json"
        )
        if year_path.exists():
            try:
                yearly = json.loads(
                    year_path.read_text(encoding="utf-8"),
                )
                yearly_results.append(yearly)
            except (json.JSONDecodeError, OSError):
                continue

    if not yearly_results:
        return JobResult(
            job_id=result_id,
            status="completed",
            job_type="single",
            symbol=job.symbol,
            years=job.years,
            description=job.description,
        )

    # 全年集約
    total_trades = sum(
        yr.get("trades", 0) for yr in yearly_results
    )
    total_profit = sum(
        yr.get("net_profit", 0.0) for yr in yearly_results
    )

    # 加重WR
    if total_trades > 0:
        wr = sum(
            yr.get("win_rate", 0.0) * yr.get("trades", 0)
            for yr in yearly_results
        ) / total_trades
    else:
        wr = 0.0

    # PF
    total_wins = sum(
        yr.get("_total_win_amount", 0.0)
        for yr in yearly_results
    )
    total_losses = sum(
        yr.get("_total_loss_amount", 0.0)
        for yr in yearly_results
    )
    # 年集約にwin/loss amountがない場合はPFを加重平均
    if total_wins == 0.0 and total_losses == 0.0:
        # 個別月結果からwin/loss集計
        for yr_data in yearly_results:
            _yr = yr_data.get("year", 0)
            for mr in _load_month_results(result_id, _yr):
                total_wins += mr.get("total_win_amount", 0.0)
                total_losses += mr.get(
                    "total_loss_amount", 0.0,
                )
    pf = (
        total_wins / total_losses
        if total_losses > 0
        else 999.99
    )

    # DD
    max_dd = max(
        yr.get("max_drawdown", 0.0)
        for yr in yearly_results
    )

    # Sharpe: 全月次リターンから計算
    initial_balance = 1_000_000
    all_monthly_returns: list[float] = []
    for yr_data in yearly_results:
        for mr_compat in yr_data.get("monthly_results", []):
            _pnl = mr_compat.get("pnl", 0.0)
            all_monthly_returns.append(
                _pnl / initial_balance
            )

    if len(all_monthly_returns) >= 2:
        import numpy as np
        _arr = np.array(all_monthly_returns)
        _mean = float(np.mean(_arr))
        _std = float(np.std(_arr, ddof=1))
        sharpe = (
            _mean / _std * (12**0.5) if _std > 0 else 0.0
        )
    else:
        sharpe = 0.0

    # 月間プラス率
    if all_monthly_returns:
        plus_months = sum(
            1 for r in all_monthly_returns if r > 0
        )
        monthly_plus = (
            plus_months / len(all_monthly_returns) * 100
        )
    else:
        monthly_plus = 0.0

    # yearly_details
    yr_details = []
    for yr_data in yearly_results:
        yr_details.append({
            "year": yr_data.get("year", 0),
            "trades": yr_data.get("trades", 0),
            "net_profit": yr_data.get("net_profit", 0.0),
            "win_rate": yr_data.get("win_rate", 0.0),
            "profit_factor": yr_data.get("profit_factor", 0.0),
            "max_drawdown": yr_data.get("max_drawdown", 0.0),
            "sharpe": yr_data.get("sharpe", 0.0),
        })

    result = JobResult(
        job_id=result_id,
        status="completed",
        job_type="single",
        symbol=job.symbol,
        years=job.years,
        description=job.description,
        net_profit=total_profit,
        trades=total_trades,
        win_rate=wr,
        profit_factor=pf,
        max_drawdown=max_dd,
        sharpe_ratio=sharpe,
        monthly_plus_rate=monthly_plus,
        yearly_details=yr_details,
        overrides_used=job.overrides,
    )
    return result


def _aggregate_job_multi_pair(
    job: Job,
    result_id: str,
) -> JobResult:
    """マルチペアジョブ全体集約"""
    from scripts.run_multi_pair_backtest import (
        aggregate_year_results,
    )

    start_year, end_year = parse_years(job.years)
    symbols = job.symbols or [
        "USDJPY", "EURJPY", "GBPJPY",
        "AUDJPY", "CADJPY", "CHFJPY",
    ]
    symbols_str = ",".join(symbols)
    num_years = end_year - start_year + 1

    # 年集約結果を読み込み
    year_results: list[dict[str, Any]] = []
    for yr in range(start_year, end_year + 1):
        year_path = (
            MONTH_RESULTS_DIR / result_id / f"year_{yr}.json"
        )
        if year_path.exists():
            try:
                yearly = json.loads(
                    year_path.read_text(encoding="utf-8"),
                )
                year_results.append(yearly)
            except (json.JSONDecodeError, OSError):
                continue

    if not year_results:
        return JobResult(
            job_id=result_id,
            status="completed",
            job_type="multi_pair",
            symbol=symbols_str,
            years=job.years,
            description=job.description,
        )

    # aggregate_year_results を使って集約
    mpc = job.multi_pair_config
    test_name = mpc.get("name", job.id)
    agg = aggregate_year_results(
        test_name, year_results, symbols, num_years,
    )

    # ペア別結果
    pair_details = []
    for sym in symbols:
        pm = agg["pair_metrics"].get(sym, {})
        if pm.get("trades", 0) > 0:
            pair_details.append({
                "symbol": sym,
                "net_profit": pm["profit"],
                "trades": pm["trades"],
                "win_rate": pm["wr"],
                "profit_factor": pm["pf"],
                "contribution_pct": pm["contribution"],
            })

    result = JobResult(
        job_id=result_id,
        status="completed",
        job_type="multi_pair",
        symbol=symbols_str,
        years=job.years,
        description=job.description,
        net_profit=agg["total_profit"],
        trades=agg["total_trades"],
        win_rate=agg["wr"],
        profit_factor=agg["pf"],
        max_drawdown=agg["max_dd_pct"],
        sharpe_ratio=agg["sharpe"],
        monthly_plus_rate=agg["monthly_wr"],
        yearly_details=agg["yearly_results"],
        pair_details=pair_details,
        portfolio_metrics={
            "total_profit": agg["total_profit"],
            "annual_return_pct": agg["annual_return_pct"],
            "max_dd_pct": agg["max_dd_pct"],
            "sharpe_ratio": agg["sharpe"],
            "portfolio_wr": agg["wr"],
            "portfolio_pf": agg["pf"],
            "monthly_win_rate": agg["monthly_wr"],
            "blocked_global": agg["blocked_global"],
            "blocked_per_pair": agg["blocked_per_pair"],
            "blocked_exposure": agg["blocked_exposure"],
            "final_equity": agg["final_equity"],
        },
        overrides_used=job.overrides,
    )
    return result


def aggregate_job(
    job: Job,
    result_id: str,
) -> JobResult:
    """ジョブ全体集約（タイプ振り分け）"""
    if job.type in ("multi_pair", "portfolio"):
        return _aggregate_job_multi_pair(job, result_id)
    return _aggregate_job_single(job, result_id)


# ===================================================================
# サブプロセス実行
# ===================================================================


def _launch_month_subprocess(
    task: MonthTask,
) -> subprocess.Popen:  # type: ignore[type-arg]
    """月タスクをサブプロセスで起動

    Args:
        task: 月タスク

    Returns:
        起動済みPopen
    """
    job_dict = task.job_dict
    code_dir = job_dict.get("code_dir", "") or str(
        _project_root
    )

    # 月結果ディレクトリ作成
    result_dir = MONTH_RESULTS_DIR / task.result_id
    result_dir.mkdir(parents=True, exist_ok=True)

    result_path = str(
        result_dir / f"{task.year}_{task.month:02d}.json"
    )

    # 月タスク情報を一時ファイルに保存
    task_data = {
        "job": job_dict,
        "year": task.year,
        "month": task.month,
        "result_path": result_path,
        "data_dir": DEFAULT_DATA_DIR,
        "result_id": task.result_id,
    }

    fd, task_file = tempfile.mkstemp(
        suffix=".json", prefix="month_task_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(task_data, f, ensure_ascii=False)
    except Exception:
        os.close(fd)
        raise

    runner_script = (
        Path(code_dir) / "scripts" / "backtest_queue_runner.py"
    )
    cmd = [
        sys.executable,
        str(runner_script),
        "--execute-month",
        task_file,
    ]

    # ログファイルにリダイレクト（パイプデッドロック防止）
    log_path = result_dir / f"{task.year}_{task.month:02d}.log"
    log_file = open(  # noqa: SIM115
        log_path, "w", encoding="utf-8", errors="replace",
    )
    proc = subprocess.Popen(
        cmd,
        cwd=code_dir,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    # log_file はプロセス終了後に閉じる
    proc._log_file = log_file  # type: ignore[attr-defined]
    return proc


def _precompute_indicators(
    symbol: str,
    data_dir: str,
) -> None:
    """インジケータ事前計算フェーズ

    全TF・全年のデータをロードしてインジケータを計算し、
    年別parquetキャッシュに保存する。
    月並列ワーカーはこのキャッシュから高速に読み込む。

    ウォームアップは全期間計算で自動処理されるため、
    年境界での指標精度問題は発生しない。

    Args:
        symbol: 通貨ペア名
        data_dir: データディレクトリパス
    """
    from autotrader.backtest.runner import (
        BacktestConfig,
        BacktestRunner,
    )
    from autotrader.config.trading_params import get_preset

    _log = logging.getLogger("queue_runner")

    preset = get_preset(symbol)
    config = BacktestConfig(
        symbol=symbol,
        spread_pips=preset.spread_pips,
        slippage_pips=preset.slippage_pips,
        pip_value=preset.pip_value,
        max_positions=preset.max_positions,
        bonus_max_positions=preset.bonus_max_positions,
        bonus_score_threshold=preset.bonus_score_threshold,
    )
    runner = BacktestRunner(
        data_dir=data_dir,
        config=config,
        verbose=False,
        log_to_file=False,
    )

    _log.info(
        "[%s] インジケータ事前計算開始（全TF・全年）",
        symbol,
    )
    runner._load_all_timeframes(include_m1=True)
    _log.info(
        "[%s] インジケータ事前計算完了 → キャッシュ保存済み",
        symbol,
    )


def _load_month_only(
    runner: Any,
    year: int,
    month: int,
) -> dict[str, Any]:
    """月データのみロード（キャッシュ利用版）

    事前計算フェーズで生成されたインジケータキャッシュから
    年データを読み込み、月フィルタして返す。

    Args:
        runner: BacktestRunner インスタンス
        year: 対象年
        month: 対象月

    Returns:
        月フィルタ済み market_data dict
    """
    import gc

    _log = logging.getLogger("queue_runner")

    # キャッシュから年データをロード
    year_data = runner._load_all_timeframes(
        include_m1=True,
        needed_years=[year],
    )

    if not year_data:
        _log.warning(
            "[%s] 年データなし: %d年",
            runner.config.symbol, year,
        )
        return {}

    # 月フィルタ
    month_start = datetime(year, month, 1)
    if month == 12:
        month_end = datetime(year + 1, 1, 1)
    else:
        month_end = datetime(year, month + 1, 1)

    month_data: dict[str, Any] = {}
    for tf, df in year_data.items():
        if df is None or df.empty:
            continue
        mask = (
            (df["time"] >= month_start)
            & (df["time"] < month_end)
        )
        month_df = df[mask].reset_index(drop=True)
        if not month_df.empty:
            month_data[tf] = month_df

    del year_data
    gc.collect()

    return month_data


def _execute_month_single(
    job_dict: dict[str, Any],
    year: int,
    month: int,
    result_path: str,
    data_dir: str,
) -> None:
    """単独BT月実行（サブプロセス内）

    month_runner._run_month_worker の処理をインラインで実行。
    """
    from autotrader.backtest.events import (
        BacktestEventEmitter,
    )
    from autotrader.backtest.file_listener import (
        TradeRowCollector,
    )
    from autotrader.backtest.service import (
        BacktestService,
        BacktestServiceConfig,
    )
    from autotrader.backtest.simulator import SimulatorConfig
    from autotrader.backtest.year_runner import (
        run_unified_year,
    )
    from autotrader.config.trading_params import (
        get_pip_unit,
        get_preset,
        get_quote_ccy_rate,
        get_symbol_overrides,
    )
    from autotrader.decision.unified import UnifiedBotConfig
    from autotrader.decision.unified.position_manager import (
        PositionManagerConfig,
    )

    job = Job.from_dict(job_dict)
    symbol = job.symbol
    overrides = job.overrides or {}
    bt_ovr = overrides.get("backtest", {})
    initial_balance = bt_ovr.get("initial_balance", 1_000_000)

    # プリセット取得
    preset = get_preset(symbol)
    sym_ovr = get_symbol_overrides(symbol)
    pip_unit = get_pip_unit(symbol)
    qcr = get_quote_ccy_rate(symbol)

    # bot overrides 構築
    bot_ovr: dict[str, Any] = {}
    bot_ovr.update({
        "max_positions": preset.max_positions,
        "bonus_max_positions": preset.bonus_max_positions,
        "bonus_score_threshold": preset.bonus_score_threshold,
        "base_risk_pct": preset.base_risk_pct,
        "max_lot_per_trade": preset.max_lot_per_trade,
        "max_total_exposure_lot": preset.max_total_exposure_lot,
        "equity_floor_pct": preset.equity_floor_pct,
        "pip_unit": pip_unit,
        "quote_ccy_rate": qcr,
    })
    bot_ovr.update(sym_ovr.get("signal", {}))
    bot_ovr.update(sym_ovr.get("filter", {}))
    bot_ovr.update(sym_ovr.get("risk_mgmt", {}))
    bot_ovr.update(overrides.get("bot", {}))

    _valid_fields = {
        f.name
        for f in UnifiedBotConfig.__dataclass_fields__.values()
    }
    bot_ovr = {
        k: v for k, v in bot_ovr.items()
        if k in _valid_fields
    }

    pm_ovr: dict[str, Any] = {}
    pm_ovr.update(sym_ovr.get("pm_config", {}))
    pm_ovr.update(overrides.get("pm", {}))

    bot_config = UnifiedBotConfig(**bot_ovr)
    PositionManagerConfig(**pm_ovr)  # バリデーション用

    # スプレッド倍率
    _sp_mult = bt_ovr.get("spread_multiplier", 1.0)
    _spread = preset.spread_pips * _sp_mult
    _slippage = preset.slippage_pips * _sp_mult

    # BacktestService経由でRunner作成
    svc_config = BacktestServiceConfig(
        start_year=year,
        end_year=year,
        initial_balance=initial_balance,
        data_dir=data_dir,
        symbol=symbol,
        spread_pips=_spread,
        slippage_pips=_slippage,
        max_positions=preset.max_positions,
        bonus_max_positions=bot_config.bonus_max_positions,
        bonus_score_threshold=bot_config.bonus_score_threshold,
        pip_value=preset.pip_value,
        commission_per_lot=preset.commission_per_lot,
        use_short_timeframe=True,
    )
    service = BacktestService(svc_config)
    runner = service.create_runner()

    # 月データのみロード（メモリ効率版）
    # TFを1つずつロード→月フィルタ→年データ解放
    # ピークメモリ: 1TF年分 + 全TF月分
    month_data = _load_month_only(runner, year, month)

    # period_start / period_end
    period_start = datetime(year, month, 1)
    if month == 12:
        period_end = datetime(year + 1, 1, 1)
    else:
        period_end = datetime(year, month + 1, 1)

    # エミッターとコレクター
    _emitter = BacktestEventEmitter()
    _collector = TradeRowCollector()
    _emitter.add_listener(_collector)

    # 月データをRunnerに設定
    runner._m1_df = month_data.get("M1")
    runner._m5_df = month_data.get("M5")
    runner._m15_df = month_data.get("M15")
    runner._m30_df = month_data.get("M30")
    runner._h1_df = month_data.get("H1")
    runner._h4_df = month_data.get("H4")
    runner._h8_df = month_data.get("H8")
    runner._d1_df = month_data.get("D1")

    sim_config = SimulatorConfig(
        initial_balance=initial_balance,
        spread_pips=_spread,
        slippage_pips=_slippage,
        pip_value=preset.pip_value,
        commission_per_lot=preset.commission_per_lot,
        max_positions=preset.max_positions,
        bonus_max_positions=bot_config.bonus_max_positions,
        bonus_score_threshold=bot_config.bonus_score_threshold,
        use_dynamic_lot=bot_config.use_dynamic_lot,
        use_position_manager=bot_config.use_position_manager,
        pip_unit=pip_unit,
        quote_ccy_rate=qcr,
        sl_tp_in_pips=True,
        pm_config=PositionManagerConfig(**pm_ovr),
    )

    result = run_unified_year(
        runner=runner,
        bot_config=bot_config,
        sim_config=sim_config,
        year=year,
        market_data=month_data,
        use_m1=True,
        period_start=period_start,
        period_end=period_end,
        emitter=_emitter,
    )

    if result is None:
        result = {}

    # 月情報を付加
    result["month"] = month
    result["initial_balance"] = initial_balance

    # FORCE_CLOSE/win/loss集計
    fc_count = 0
    fc_pnl = 0.0
    total_win_amount = 0.0
    total_loss_amount = 0.0
    for row in _collector._trade_rows:
        _pnl = float(row.get("profit_loss", 0.0))
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

    # _worker_*キーは月結果保存不要 → 除外
    result.pop("_worker_trade_rows", None)
    result.pop("_worker_stats", None)

    # 結果保存
    Path(result_path).write_text(
        json.dumps(
            result, indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )


def _execute_month_multi_pair(
    job_dict: dict[str, Any],
    year: int,
    month: int,
    result_path: str,
    data_dir: str,
) -> None:
    """マルチペアBT月実行（サブプロセス内）

    1ヶ月分のマルチペアインターリーブ実行を行う。
    PortfolioStateは月ごとにリセット。
    """
    from scripts.run_multi_pair_backtest import (
        INITIAL_EQUITY,
        MultiPairConfig,
        PortfolioState,
        build_bot_config,
        run_multi_pair_year,
        setup_pair_context,
    )

    job = Job.from_dict(job_dict)
    _default_symbols = [
        "USDJPY", "EURJPY", "GBPJPY",
        "AUDJPY", "CADJPY", "CHFJPY",
    ]
    symbols = job.symbols or _default_symbols
    overrides = job.overrides or {}
    mpc = job.multi_pair_config

    # MultiPairConfig 構築
    from scripts.run_multi_pair_backtest import TEST_MATRIX
    test_name = mpc.get("test_name", "")
    if test_name and test_name in TEST_MATRIX:
        multi_config = TEST_MATRIX[test_name]
    else:
        multi_config = MultiPairConfig(
            name=mpc.get("name", job.id),
            global_max_positions=mpc.get(
                "global_max_positions", 6,
            ),
            per_pair_max_positions=mpc.get(
                "per_pair_max_positions", 1,
            ),
            global_max_exposure_lot=mpc.get(
                "global_max_exposure_lot", 10.0,
            ),
        )

    # bot追加オーバーライド
    bot_extra: dict[str, Any] = {}
    if "base_risk_pct" in mpc:
        bot_extra["base_risk_pct"] = mpc["base_risk_pct"]
    if "consensus_threshold" in mpc:
        bot_extra["consensus_threshold"] = mpc[
            "consensus_threshold"
        ]
    bot_extra.update(overrides.get("bot", {}))

    pm_extra: dict[str, Any] = {}
    pm_extra.update(overrides.get("pm", {}))

    spread_mult = mpc.get("spread_multiplier", 1.0)

    # 月ごとに独立PortfolioState
    portfolio = PortfolioState(
        equity=INITIAL_EQUITY,
        initial_equity=INITIAL_EQUITY,
        peak_equity=INITIAL_EQUITY,
    )

    # ペアコンテキスト構築（月データのみ・メモリ効率版）
    # ペアを1つずつロード→月フィルタ→年データ解放
    from scripts.run_multi_pair_backtest import (
        _create_runner,
    )

    contexts: dict[str, Any] = {}
    for sym in symbols:
        _sym_runner = _create_runner(sym, data_dir)
        month_md = _load_month_only(
            _sym_runner, year, month,
        )
        # 月データが空なら skip
        _has_data = any(
            len(df) > 0 for df in month_md.values()
        )
        if not _has_data:
            continue
        runner = _sym_runner

        bot_config = build_bot_config(
            sym,
            extra_overrides=bot_extra or None,
            multi_mode=True,
        )
        ctx = setup_pair_context(
            sym, runner, year, bot_config,
            INITIAL_EQUITY,
            full_market_data=month_md,
            pm_config_overrides=pm_extra or None,
            spread_multiplier=spread_mult,
        )
        if ctx is not None:
            contexts[sym] = ctx

    if not contexts:
        # 月データなし → 空結果
        Path(result_path).write_text(
            json.dumps(
                {
                    "year": year,
                    "month": month,
                    "year_pnl": 0.0,
                    "year_trades": 0,
                    "final_equity": INITIAL_EQUITY,
                    "initial_equity": INITIAL_EQUITY,
                    "max_dd_pct": 0.0,
                    "monthly_pnl": {},
                    "pair_summaries": {},
                    "blocked_global": 0,
                    "blocked_per_pair": 0,
                    "blocked_exposure": 0,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return

    # インターリーブ実行（月データは自動的に1ヶ月分）
    pair_trades = run_multi_pair_year(
        year, contexts, multi_config, portfolio,
    )

    # 月次結果構築
    year_pnl = portfolio.equity - INITIAL_EQUITY
    year_trades = sum(len(t) for t in pair_trades.values())

    # ペア別サマリー
    pair_summaries: dict[str, dict[str, Any]] = {}
    for sym, trades in pair_trades.items():
        wins = 0
        gp = 0.0
        gl = 0.0
        np_ = 0.0
        for trade in trades:
            pnl = trade.get("pnl", 0.0)
            np_ += pnl
            if pnl > 0:
                wins += 1
                gp += pnl
            else:
                gl += abs(pnl)
        pair_summaries[sym] = {
            "trades": len(trades),
            "wins": wins,
            "gross_profit": gp,
            "gross_loss": gl,
            "net_profit": np_,
        }

    # monthly_pnlをstring key化
    monthly_pnl_str: dict[str, float] = {}
    for key, pnl in portfolio.monthly_pnl.items():
        if isinstance(key, tuple):
            monthly_pnl_str[f"{key[0]}-{key[1]:02d}"] = pnl
        else:
            monthly_pnl_str[str(key)] = pnl

    month_result = {
        "year": year,
        "month": month,
        "year_pnl": year_pnl,
        "year_trades": year_trades,
        "final_equity": portfolio.equity,
        "initial_equity": INITIAL_EQUITY,
        "max_dd_pct": portfolio.max_dd_pct,
        "monthly_pnl": monthly_pnl_str,
        "pair_summaries": pair_summaries,
        "blocked_global": portfolio.blocked_global,
        "blocked_per_pair": portfolio.blocked_per_pair,
        "blocked_exposure": portfolio.blocked_exposure,
    }

    Path(result_path).write_text(
        json.dumps(
            month_result,
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )


def _execute_month_from_file(task_file: str) -> None:
    """--execute-monthモード: 月タスクJSONから1ヶ月実行

    サブプロセスとして呼ばれ、指定月を実行して終了する。

    Args:
        task_file: 月タスクJSONファイルのパス
    """
    data = json.loads(
        Path(task_file).read_text(encoding="utf-8"),
    )
    job_dict = data["job"]
    year = data["year"]
    month = data["month"]
    result_path = data["result_path"]
    data_dir = data.get("data_dir", DEFAULT_DATA_DIR)

    job_type = job_dict.get("type", "single")

    try:
        if job_type in ("multi_pair", "portfolio"):
            _execute_month_multi_pair(
                job_dict, year, month, result_path, data_dir,
            )
        else:
            _execute_month_single(
                job_dict, year, month, result_path, data_dir,
            )
    finally:
        # 一時ファイル削除
        with contextlib.suppress(OSError):
            os.unlink(task_file)


# ===================================================================
# 後方互換: --execute-job モード
# ===================================================================


def _execute_job_from_file(job_file: str) -> None:
    """--execute-jobモード: ジョブファイルから単一ジョブを実行

    後方互換のため残存。新規実行は --execute-month を使用。

    Args:
        job_file: ジョブ情報JSONファイルのパス
    """
    data = json.loads(
        Path(job_file).read_text(encoding="utf-8"),
    )
    job = Job.from_dict(data["job"])
    rid = data.get("result_id", job.id)

    # 結果ディレクトリを親プロセスと共有
    global RESULTS_DIR  # noqa: PLW0603
    results_dir = data.get("results_dir", "")
    if results_dir:
        RESULTS_DIR = Path(results_dir)

    global DEFAULT_DATA_DIR  # noqa: PLW0603
    dd = data.get("data_dir", "")
    if dd:
        DEFAULT_DATA_DIR = dd

    # 全年全月を順次実行し結果保存
    start_year, end_year = parse_years(job.years)
    for yr in range(start_year, end_year + 1):
        for mo in range(1, 13):
            result_dir = MONTH_RESULTS_DIR / rid
            result_dir.mkdir(parents=True, exist_ok=True)
            rp = str(result_dir / f"{yr}_{mo:02d}.json")
            if Path(rp).exists():
                continue
            if job.type in ("multi_pair", "portfolio"):
                _execute_month_multi_pair(
                    data["job"], yr, mo, rp, DEFAULT_DATA_DIR,
                )
            else:
                _execute_month_single(
                    data["job"], yr, mo, rp, DEFAULT_DATA_DIR,
                )

    # 年集約 + ジョブ集約
    for yr in range(start_year, end_year + 1):
        aggregate_year(rid, yr, job.type)
    result = aggregate_job(job, rid)
    result.started_at = datetime.now().isoformat()
    result.finished_at = datetime.now().isoformat()
    _save_result(result)


# ===================================================================
# Web UI連携: 状態書き出し・コマンド読み取り
# ===================================================================


def _write_runner_state(
    running_tasks: list[RunningMonthTask],
    job_progress: dict[str, JobProgress],
    state: QueueState,
    paused: bool,
    cpu_threads: int,
) -> None:
    """キューランナー状態をJSONファイルに書き出し"""
    now = datetime.now().isoformat()

    # 実行中タスク
    tasks_data: list[dict[str, Any]] = []
    for rt in running_tasks:
        t = rt.task
        elapsed = time.time() - rt.started_at
        tasks_data.append({
            "job_id": t.job_id,
            "result_id": t.result_id,
            "type": t.job_type,
            "year": t.year,
            "month": t.month,
            "symbol": t.job_dict.get("symbol", ""),
            "elapsed": round(elapsed, 1),
            "started_at": datetime.fromtimestamp(
                rt.started_at,
            ).isoformat(),
        })

    # ジョブ進捗
    progress_data: list[dict[str, Any]] = []
    for jp in job_progress.values():
        if jp.status in ("in_progress", "pending"):
            progress_data.append({
                "job_id": jp.job_id,
                "result_id": jp.result_id,
                "type": jp.job_type,
                "symbol": jp.symbol,
                "years": jp.years,
                "description": jp.description,
                "completed": jp.completed_count,
                "total": jp.total_months,
                "pct": round(jp.pct, 1),
                "status": jp.status,
                "started_at": (
                    datetime.fromtimestamp(jp.started_at).isoformat()
                    if jp.started_at > 0
                    else ""
                ),
            })

    data = {
        "paused": paused,
        "cpu_threads": cpu_threads,
        "running_tasks": tasks_data,
        "job_progress": progress_data,
        "completed_ids": state.completed_ids,
        "updated_at": now,
    }
    try:
        tmp = RUNNER_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                data, ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(RUNNER_STATE_FILE)
    except OSError:
        pass


def _read_runner_commands(
    cmd_queue: Any,
) -> None:
    """Web UIからのコマンドファイルを読み取り"""
    if not RUNNER_CMD_FILE.exists():
        return
    try:
        data = json.loads(
            RUNNER_CMD_FILE.read_text(encoding="utf-8"),
        )
        commands = data.get("commands", [])
        for cmd in commands:
            cmd_queue.put(str(cmd).strip())
        RUNNER_CMD_FILE.unlink(missing_ok=True)
    except (json.JSONDecodeError, OSError):
        pass


# ===================================================================
# 対話コマンドリーダー
# ===================================================================


def stdin_reader(
    cmd_queue: queue.Queue[str],
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
# メインループ
# ===================================================================


def main() -> None:
    """キューランナーのメインループ"""
    import queue as _q  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description="バックテストキューランナー（月スケジューラ）",
    )
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=os.cpu_count() or 4,
        help="使用可能なCPUスレッド数（デフォルト: OS検出値）",
    )
    parser.add_argument(
        "--execute-job",
        type=str,
        default="",
        help="後方互換: 単一ジョブ実行モード（JSONファイルパス）",
    )
    parser.add_argument(
        "--execute-month",
        type=str,
        default="",
        help="月タスク実行モード（JSONファイルパス）",
    )
    cli_args = parser.parse_args()

    # --execute-month モード
    if cli_args.execute_month:
        _execute_month_from_file(cli_args.execute_month)
        return

    # --execute-job モード（後方互換）
    if cli_args.execute_job:
        _execute_job_from_file(cli_args.execute_job)
        return

    cpu_threads: int = cli_args.cpu_threads

    print("=" * 60)
    print("  バックテストキューランナー（月スケジューラ）")
    print("=" * 60)
    print(f"  キューファイル: {QUEUE_FILE}")
    print(f"  結果ディレクトリ: {RESULTS_DIR}")
    print(f"  月結果ディレクトリ: {MONTH_RESULTS_DIR}")
    print(f"  ポーリング間隔: {POLL_INTERVAL}s")
    print(
        f"  CPUスレッド: {cpu_threads}"
        f" (シングル={CPU_COST_SINGLE}CPU,"
        f" マルチ={CPU_COST_MULTI_PAIR}CPU)",
    )
    print()
    print("  コマンド:")
    print("    stop   - 全タスク停止+キュー先頭")
    print("    pause  - 新規タスク取得を一時停止")
    print("    resume - 一時停止解除")
    print("    status - 現在の状態表示")
    print("    cpu N  - CPUスレッド数を変更")
    print("    quit   - ランナー終了")
    print("=" * 60)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    MONTH_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # 状態読み込み + クリーンアップ
    state = QueueState.load()
    state.sync_with_queue()
    cleanup_stale_running(state)
    WORKER_PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_worker_progress()

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
    running_tasks: list[RunningMonthTask] = []
    job_progress: dict[str, JobProgress] = {}
    # result_id → Job のマッピング
    active_jobs: dict[str, Job] = {}
    # 集約済み年の追跡（重複実行防止）
    # key: (result_id, year)
    aggregated_years: set[tuple[str, int]] = set()
    # 事前計算中のジョブ（バックグラウンドスレッド）
    # result_id → Thread
    _precomputing: dict[str, threading.Thread] = {}

    while True:
        # ---------------------------------------------------
        # コマンド処理
        # ---------------------------------------------------
        try:
            while True:
                raw_cmd = cmd_queue.get_nowait()
                cmd = raw_cmd.lower().strip()

                if cmd == "stop":
                    if running_tasks:
                        logger.info(
                            ">>> 全タスク停止中 (%d件)...",
                            len(running_tasks),
                        )
                        for rt in running_tasks:
                            rt.process.terminate()
                        # 全プロセス終了待ち
                        for rt in running_tasks:
                            try:
                                rt.process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                rt.process.kill()
                            _lf = getattr(
                                rt.process, "_log_file", None,
                            )
                            if _lf:
                                with contextlib.suppress(Exception):
                                    _lf.close()
                        running_tasks.clear()
                        # 全リセット
                        state.completed_ids.clear()
                        state.save()
                        job_progress.clear()
                        active_jobs.clear()
                        aggregated_years.clear()
                        # 月結果もクリーン
                        logger.info(
                            ">>> キュー先頭にリセット",
                        )
                    else:
                        logger.info(
                            ">>> 実行中タスクなし",
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
                            new_cpu = max(1, int(parts[1]))
                            old_cpu = cpu_threads
                            cpu_threads = new_cpu
                            logger.info(
                                ">>> CPUスレッド: %d → %d",
                                old_cpu,
                                cpu_threads,
                            )
                            # 超過分を最新から停止
                            while (
                                _current_cpu_load(running_tasks)
                                > cpu_threads
                                and running_tasks
                            ):
                                rt = running_tasks.pop()
                                rt.process.terminate()
                                try:
                                    rt.process.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    rt.process.kill()
                                _lf = getattr(
                                    rt.process,
                                    "_log_file",
                                    None,
                                )
                                if _lf:
                                    with contextlib.suppress(
                                        Exception,
                                    ):
                                        _lf.close()
                                logger.info(
                                    ">>> CPU超過: %s %d/%02d 停止",
                                    rt.task.job_id,
                                    rt.task.year,
                                    rt.task.month,
                                )
                        except ValueError:
                            logger.error(
                                ">>> 無効な値: %s (例: cpu 8)",
                                parts[1],
                            )
                    else:
                        logger.info(
                            ">>> 現在のCPUスレッド: %d"
                            " (負荷: %.1f, タスク: %d)",
                            cpu_threads,
                            _current_cpu_load(running_tasks),
                            len(running_tasks),
                        )

                elif cmd == "status":
                    _jobs = load_queue()
                    _done = len(state.completed_ids)
                    _total = len(_jobs)
                    _remain = _total - _done
                    print(
                        f"  状態: "
                        f"{'一時停止' if paused else '稼働中'}"
                    )
                    _load = _current_cpu_load(running_tasks)
                    print(
                        f"  CPU: {_load:.1f}"
                        f"/{cpu_threads} 使用中"
                        f" ({len(running_tasks)}タスク)"
                    )
                    for rid, jp in job_progress.items():
                        if jp.status == "in_progress":
                            print(
                                f"    [{rid}] {jp.symbol}"
                                f" {jp.years}"
                                f" {jp.completed_count}"
                                f"/{jp.total_months}月"
                                f" ({jp.pct:.0f}%)"
                            )
                    print(
                        f"  進捗: {_done}/{_total}"
                        f" (残り{_remain}件)"
                    )

                elif cmd == "quit":
                    if running_tasks:
                        logger.info(
                            ">>> 全タスク停止中...",
                        )
                        for rt in running_tasks:
                            rt.process.terminate()
                        for rt in running_tasks:
                            try:
                                rt.process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                rt.process.kill()
                            _lf = getattr(
                                rt.process, "_log_file", None,
                            )
                            if _lf:
                                with contextlib.suppress(
                                    Exception,
                                ):
                                    _lf.close()
                    logger.info(">>> ランナー終了")
                    return

        except _q.Empty:
            pass

        # ---------------------------------------------------
        # 完了タスク回収
        # ---------------------------------------------------
        finished: list[RunningMonthTask] = []
        for rt in running_tasks:
            if rt.process.poll() is not None:
                finished.append(rt)

        for rt in finished:
            running_tasks.remove(rt)
            t = rt.task
            rid = t.result_id

            # ログファイルを閉じる
            _lf = getattr(rt.process, "_log_file", None)
            if _lf:
                with contextlib.suppress(Exception):
                    _lf.close()

            # プロセス終了コード確認
            rc = rt.process.returncode
            if rc != 0:
                # ログファイルからエラー内容を取得
                _log_path = (
                    MONTH_RESULTS_DIR / rid
                    / f"{t.year}_{t.month:02d}.log"
                )
                _err_tail = ""
                if _log_path.exists():
                    try:
                        lines = _log_path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        ).splitlines()
                        _err_tail = "\n".join(
                            lines[-10:]
                        )
                    except OSError:
                        pass
                logger.warning(
                    "[%s] %d/%02d 失敗 (rc=%d)%s",
                    rid,
                    t.year,
                    t.month,
                    rc,
                    f"\n{_err_tail}" if _err_tail else "",
                )
                # 失敗した月結果は保存されないので
                # 次回再実行される
                continue

            logger.info(
                "[%s] %d/%02d 完了 (%.0fs)",
                rid,
                t.year,
                t.month,
                time.time() - rt.started_at,
            )

            # 進捗更新
            if rid in job_progress:
                jp = job_progress[rid]
                jp.completed_months.add((t.year, t.month))

                # 年完了チェック（重複防止ガード付き）
                _year_key = (rid, t.year)
                if (
                    _year_key not in aggregated_years
                    and _is_year_complete(rid, t.year)
                ):
                    aggregated_years.add(_year_key)
                    logger.info(
                        "[%s] %d年 全月完了 → 年集約中...",
                        rid, t.year,
                    )
                    aggregate_year(rid, t.year, t.job_type)

                # ジョブ完了チェック
                if rid in active_jobs:
                    job = active_jobs[rid]
                    if _is_job_complete(job, rid):
                        logger.info(
                            "[%s] 全月完了 → ジョブ集約中...",
                            rid,
                        )
                        result = aggregate_job(job, rid)
                        elapsed = (
                            time.time() - jp.started_at
                            if jp.started_at > 0
                            else 0.0
                        )
                        result.elapsed_seconds = round(
                            elapsed, 1,
                        )
                        result.started_at = (
                            datetime.fromtimestamp(
                                jp.started_at,
                            ).isoformat()
                            if jp.started_at > 0
                            else ""
                        )
                        result.finished_at = (
                            datetime.now().isoformat()
                        )
                        _save_result(result)

                        # ログ出力
                        if result.job_type == "multi_pair":
                            _pm = result.portfolio_metrics
                            logger.info(
                                "[%s] マルチペア完了:"
                                " profit=%.0f,"
                                " WR=%.1f%%,"
                                " PF=%.2f,"
                                " DD=%.2f%%,"
                                " Sharpe=%.2f"
                                " (%.0fs)",
                                result.job_id,
                                result.net_profit,
                                result.win_rate,
                                result.profit_factor,
                                result.max_drawdown,
                                result.sharpe_ratio,
                                result.elapsed_seconds,
                            )
                        else:
                            logger.info(
                                "[%s] 完了:"
                                " profit=%.0f,"
                                " WR=%.1f%%,"
                                " PF=%.2f,"
                                " DD=%.2f%%"
                                " (%.0fs)",
                                result.job_id,
                                result.net_profit,
                                result.win_rate,
                                result.profit_factor,
                                result.max_drawdown,
                                result.elapsed_seconds,
                            )

                        # 完了記録
                        _orig_id = job.id
                        if _orig_id not in state.completed_ids:
                            state.completed_ids.append(
                                _orig_id,
                            )
                        state.save()
                        _remove_job_from_queue(_orig_id)

                        # 掃除
                        jp.status = "completed"
                        del active_jobs[rid]

        # ---------------------------------------------------
        # 新規タスク投入（CPUスロット埋め）
        # ---------------------------------------------------
        if not paused:
            jobs = load_queue()
            _done = set(state.completed_ids)
            # 実行中ジョブID
            _active_job_ids = {
                jp.job_id
                for jp in job_progress.values()
                if jp.status == "in_progress"
            }

            # 事前計算中のジョブがあれば完了を待つ
            # （新ジョブ登録をブロックして直列化）
            _pc_busy = False
            for _pc_rid, _pc_t in list(_precomputing.items()):
                if _pc_t.is_alive():
                    _pc_busy = True
                    break
                # 完了 → 除去
                del _precomputing[_pc_rid]

            # アクティブジョブの未完了月タスク数を計算
            # → CPUスレッド数以上の未完了月があれば新ジョブ不要
            _active_pending_months = 0
            for _aj_rid, _aj_job in active_jobs.items():
                _aj_jp = job_progress.get(_aj_rid)
                if _aj_jp and _aj_jp.status == "in_progress":
                    _remaining = (
                        _aj_jp.total_months
                        - len(_aj_jp.completed_months)
                    )
                    _active_pending_months += max(0, _remaining)

            for job in jobs:
                if _pc_busy:
                    break
                if _current_cpu_load(running_tasks) >= cpu_threads:
                    break
                # 未完了月タスクが十分あれば新ジョブ不要
                if _active_pending_months >= cpu_threads:
                    break

                # 完了済みスキップ
                if job.id in _done:
                    continue

                # result_idを決定（初回のみ発行）
                # job_progress に既にあるならそれを使う
                existing_jp = None
                for _jp in job_progress.values():
                    if _jp.job_id == job.id and _jp.status != "completed":
                        existing_jp = _jp
                        break

                if existing_jp:
                    rid = existing_jp.result_id
                else:
                    _cnt = state.next_counter()
                    rid = f"{_cnt:03d}_{job.id}"

                    start_year, end_year = parse_years(
                        job.years,
                    )
                    total_months = (
                        (end_year - start_year + 1) * 12
                    )
                    _sym_label = (
                        ",".join(job.symbols)
                        if job.symbols
                        else job.symbol
                    )
                    jp_new = JobProgress(
                        job_id=job.id,
                        result_id=rid,
                        job_type=job.type,
                        symbol=_sym_label,
                        years=job.years,
                        description=job.description,
                        total_months=total_months,
                        completed_months=_get_completed_months(
                            rid,
                        ),
                        status="in_progress",
                        started_at=time.time(),
                    )
                    job_progress[rid] = jp_new
                    active_jobs[rid] = job
                    _active_pending_months += total_months

                    logger.info(
                        "[%s] 開始: %s %s %s"
                        " (全%d月, CPU=%d)",
                        rid,
                        _sym_label,
                        job.years,
                        job.description,
                        total_months,
                        cpu_threads,
                    )

                    # インジケータ事前計算（バックグラウンド）
                    # メインループをブロックしない
                    _precompute_symbols = (
                        job.symbols
                        if job.symbols
                        else [job.symbol]
                    )

                    def _bg_precompute(
                        symbols: list[str],
                        _rid: str = rid,
                    ) -> None:
                        for _sym in symbols:
                            _precompute_indicators(
                                _sym, DEFAULT_DATA_DIR,
                            )

                    _pc_thread = threading.Thread(
                        target=_bg_precompute,
                        args=(_precompute_symbols,),
                        daemon=True,
                    )
                    _pc_thread.start()
                    _precomputing[rid] = _pc_thread
                    # 事前計算開始→次のポーリングで完了確認
                    break

                # 事前計算待ちのジョブはタスク投入スキップ
                if rid in _precomputing:
                    break

                # 未完了月タスクを生成
                pending = generate_pending_months(job, rid)

                # 実行中の月を除外
                _running_months = {
                    (rt.task.year, rt.task.month)
                    for rt in running_tasks
                    if rt.task.result_id == rid
                }
                pending = [
                    mt for mt in pending
                    if (mt.year, mt.month) not in _running_months
                ]

                for mt in pending:
                    if _current_cpu_load(running_tasks) + mt.cpu_cost > cpu_threads:
                        break
                    proc = _launch_month_subprocess(mt)
                    running_tasks.append(
                        RunningMonthTask(
                            task=mt,
                            process=proc,
                            started_at=time.time(),
                        )
                    )

                if _current_cpu_load(running_tasks) >= cpu_threads:
                    break

        # Web UI連携
        _write_runner_state(
            running_tasks,
            job_progress,
            state,
            paused,
            cpu_threads,
        )
        _read_runner_commands(cmd_queue)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
