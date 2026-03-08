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
import hashlib
import io
import json
import logging
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
    max_year_workers: int = 0  # 0=年数から自動計算
    overrides: dict[str, Any] = field(default_factory=dict)
    multi_pair_config: dict[str, Any] = field(
        default_factory=dict,
    )

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
        )

    def effective_year_workers(self) -> int:
        """実効年並列数（0なら年数から自動計算）

        multi_pairジョブでmax_year_workers > 1の場合、
        年並列実行のため実際のワーカー数を返す。
        max_year_workers == 0 なら年数から自動計算。
        """
        if self.type in ("multi_pair", "portfolio"):
            if self.max_year_workers > 0:
                return self.max_year_workers
            # 0=自動: 年数から計算
            start, end = parse_years(self.years)
            return max(1, end - start + 1)
        if self.max_year_workers > 0:
            return self.max_year_workers
        start, end = parse_years(self.years)
        return max(1, end - start + 1)

    def cpu_cost(self) -> float:
        """このジョブが消費するCPUスレッド数

        年並列実行時は max_year_workers * THREADS_PER_YEAR。
        """
        return self.effective_year_workers() * THREADS_PER_YEAR


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
class RunningJob:
    """実行中ジョブのトラッキング"""

    job: Job
    thread: threading.Thread
    cancel_event: threading.Event
    result_holder: list[JobResult | None]
    max_year_workers: int
    started_at: float  # time.time()
    result_id: str = ""  # 連番付きID

    @property
    def cpu_cost(self) -> float:
        """消費CPUスレッド数"""
        return self.max_year_workers * THREADS_PER_YEAR


def _compute_queue_hash() -> str:
    """キューファイルのジョブID一覧からハッシュを計算

    キュー内容（ジョブIDリスト）が変わったら
    completed_ids をリセットするために使用。

    Returns:
        str: ハッシュ文字列（空キューは空文字列）
    """
    if not QUEUE_FILE.exists():
        return ""
    try:
        data = json.loads(
            QUEUE_FILE.read_text(encoding="utf-8"),
        )
        ids = [j.get("id", "") for j in data.get("jobs", [])]
        content = json.dumps(ids, sort_keys=True)
        return hashlib.sha256(
            content.encode(),
        ).hexdigest()[:16]
    except (json.JSONDecodeError, KeyError):
        return ""


@dataclass
class QueueState:
    """キュー処理状態（completed_ids ベース）

    再起動時は常にキュー先頭からスキャンし、
    completed_ids にあるジョブをスキップする。
    job_counter はグローバル連番で結果ファイル名の
    一意性を保証する。
    queue_hash でキューファイルの変更を検知し、
    変更時は completed_ids を自動リセットする。
    """

    completed_ids: list[str] = field(default_factory=list)
    job_counter: int = 0
    queue_hash: str = ""

    def sync_with_queue(self) -> None:
        """キューハッシュを検証し、変更時にリセット"""
        current_hash = _compute_queue_hash()
        if not current_hash:
            return
        if self.queue_hash and self.queue_hash != current_hash:
            old_count = len(self.completed_ids)
            self.completed_ids.clear()
            self.queue_hash = current_hash
            self.save()
            logger.info(
                "キュー内容変更検知: completed_ids リセット（旧%d件）",
                old_count,
            )
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
            "中断ジョブ %d件をクリーンアップ完了",
            cleaned,
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
    result_id: str = "",
) -> JobResult:
    """バックテストジョブを実行

    Args:
        job: 実行するジョブ
        cancel_event: キャンセルイベント
        max_year_workers: 年並列実行数
        result_id: 連番付き結果ID

    Returns:
        JobResult: 実行結果
    """
    from autotrader.backtest.service import (
        BacktestService,
        BacktestServiceConfig,
    )
    from autotrader.config.trading_params import (
        get_pip_unit,
        get_preset,
        get_symbol_overrides,
    )
    from autotrader.decision.unified import UnifiedBotConfig
    from autotrader.decision.unified.position_manager import (
        PositionManagerConfig,
    )

    _rid = result_id or job.id
    result = JobResult(
        job_id=_rid,
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
        pip_unit = get_pip_unit(job.symbol)
        bot_ovr: dict[str, Any] = {}
        # L1: プリセット
        bot_ovr.update(
            {
                "max_positions": preset.max_positions,
                "bonus_max_positions": (preset.bonus_max_positions),
                "bonus_score_threshold": (preset.bonus_score_threshold),
                "base_risk_pct": preset.base_risk_pct,
                "max_lot_per_trade": preset.max_lot_per_trade,
                "max_total_exposure_lot": (preset.max_total_exposure_lot),
                "equity_floor_pct": preset.equity_floor_pct,
                "pip_unit": pip_unit,
            }
        )
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
            "data_dir",
            DEFAULT_DATA_DIR,
        )
        initial_balance = bt_ovr.get(
            "initial_balance",
            1_000_000,
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
            bonus_max_positions=(bot_config.bonus_max_positions),
            bonus_score_threshold=(bot_config.bonus_score_threshold),
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
            result.yearly_details = bt_result.yearly_results

            # 月間プラス率を計算
            if bt_result.monthly_results:
                plus_months = sum(
                    1
                    for m in bt_result.monthly_results
                    if m.get("profit", 0) > 0
                )
                total = len(bt_result.monthly_results)
                result.monthly_plus_rate = (
                    plus_months / total * 100 if total > 0 else 0
                )

    except Exception as e:
        result.status = "failed"
        result.error = str(e)
        logger.exception("[%s] ジョブ失敗: %s", job.id, e)

    result.elapsed_seconds = round(
        time.time() - start_time,
        1,
    )
    result.finished_at = datetime.now().isoformat()
    _save_result(result)
    return result


def execute_multi_pair_job(
    job: Job,
    cancel_event: threading.Event,
    max_year_workers: int = 1,
    result_id: str = "",
) -> JobResult:
    """マルチ通貨ペアインターリーブジョブを実行

    時系列インターリーブ方式で複数ペアを共有エクイティプール＋
    グローバルポジション制限で同時実行する。
    max_year_workers > 1 の場合、年並列実行を行う。

    Args:
        job: 実行するジョブ（type="multi_pair"）
        cancel_event: キャンセルイベント
        max_year_workers: 年並列ワーカー数（1=順次）
        result_id: 連番付き結果ID

    Returns:
        JobResult: インターリーブ実行結果
    """
    from scripts.run_multi_pair_backtest import (
        TEST_MATRIX,
        MultiPairConfig,
        load_pair_data,
        run_test_case,
    )

    _rid = result_id or job.id

    # 対象シンボル決定（空なら全6ペア）
    _default_symbols = [
        "USDJPY",
        "EURJPY",
        "GBPJPY",
        "AUDJPY",
        "CADJPY",
        "CHFJPY",
    ]
    symbols = job.symbols or _default_symbols
    symbols_str = ",".join(symbols)

    result = JobResult(
        job_id=_rid,
        status="running",
        job_type="multi_pair",
        symbol=symbols_str,
        years=job.years,
        description=job.description,
        started_at=datetime.now().isoformat(),
        overrides_used=job.overrides,
    )
    _save_result(result)

    start_time = time.time()
    start_year, end_year = parse_years(job.years)

    # バックテスト共通設定
    bt_ovr = job.overrides.get("backtest", {})
    data_dir = bt_ovr.get("data_dir", DEFAULT_DATA_DIR)

    # MultiPairConfig 構築
    mpc = job.multi_pair_config
    test_name = mpc.get("test_name", "")
    if test_name and test_name in TEST_MATRIX:
        multi_config = TEST_MATRIX[test_name]
        logger.info(
            "[%s] TEST_MATRIX '%s' を使用",
            _rid,
            test_name,
        )
    else:
        multi_config = MultiPairConfig(
            name=mpc.get("name", job.id),
            global_max_positions=mpc.get(
                "global_max_positions",
                6,
            ),
            per_pair_max_positions=mpc.get(
                "per_pair_max_positions",
                1,
            ),
            global_max_exposure_lot=mpc.get(
                "global_max_exposure_lot",
                10.0,
            ),
            base_risk_pct=mpc.get(
                "base_risk_pct",
                0.02,
            ),
            consensus_threshold=mpc.get(
                "consensus_threshold",
                9.0,
            ),
        )

    # bot追加オーバーライド
    bot_extra: dict[str, Any] = {}
    bot_ovr_cfg = job.overrides.get("bot", {})
    if bot_ovr_cfg:
        bot_extra.update(bot_ovr_cfg)

    is_parallel = max_year_workers > 1

    try:
        logger.info(
            "[%s] マルチペア実行開始 (%s, %s, workers=%d)",
            _rid,
            symbols_str,
            job.years,
            max_year_workers,
        )

        # データロード（順次実行時のみ事前ロード）
        runners: dict[str, Any] = {}
        if not is_parallel:
            for sym in symbols:
                runners[sym] = load_pair_data(
                    sym, data_dir,
                )
                logger.info(
                    "[%s] %s ロード完了", _rid, sym,
                )

        # run_test_caseに委譲（順次/並列を統一処理）
        agg = run_test_case(
            test_config=multi_config,
            runners=runners,
            symbols=symbols,
            start_year=start_year,
            end_year=end_year,
            max_year_workers=max_year_workers,
            data_dir=data_dir,
            bot_extra_overrides=bot_extra,
        )

        if cancel_event.is_set():
            result.status = "cancelled"
            result.error = "ユーザーにより停止"
        else:
            result.status = "completed"
            result.net_profit = agg["total_profit"]
            result.trades = agg["total_trades"]
            result.win_rate = agg["wr"]
            result.profit_factor = agg["pf"]
            result.max_drawdown = agg["max_dd_pct"]
            result.sharpe_ratio = agg["sharpe"]
            result.monthly_plus_rate = agg["monthly_wr"]
            result.yearly_details = agg["yearly_results"]

            # ペア別結果
            pair_details = []
            for sym in symbols:
                pm = agg["pair_metrics"].get(sym, {})
                if pm.get("trades", 0) > 0:
                    pair_details.append(
                        {
                            "symbol": sym,
                            "net_profit": pm["profit"],
                            "trades": pm["trades"],
                            "win_rate": pm["wr"],
                            "profit_factor": pm["pf"],
                            "contribution_pct": (
                                pm["contribution"]
                            ),
                        }
                    )
            result.pair_details = pair_details

            # ポートフォリオメトリクス
            result.portfolio_metrics = {
                "total_profit": agg["total_profit"],
                "annual_return_pct": (
                    agg["annual_return_pct"]
                ),
                "max_dd_pct": agg["max_dd_pct"],
                "sharpe_ratio": agg["sharpe"],
                "portfolio_wr": agg["wr"],
                "portfolio_pf": agg["pf"],
                "monthly_win_rate": agg["monthly_wr"],
                "blocked_global": (
                    agg["blocked_global"]
                ),
                "blocked_per_pair": (
                    agg["blocked_per_pair"]
                ),
                "blocked_exposure": (
                    agg["blocked_exposure"]
                ),
                "final_equity": agg["final_equity"],
            }

    except Exception as e:
        result.status = "failed"
        result.error = str(e)
        logger.exception(
            "[%s] マルチペアジョブ失敗: %s",
            job.id,
            e,
        )

    result.elapsed_seconds = round(
        time.time() - start_time,
        1,
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


def _kill_job_child_processes(
    rj: RunningJob,
) -> int:
    """ジョブの子プロセスを強制終了

    ジョブ開始時刻以降に作成された子プロセスを特定し、
    強制終了する。

    Args:
        rj: 対象のRunningJob

    Returns:
        int: 終了させたプロセス数
    """
    import psutil  # noqa: PLC0415

    killed = 0
    try:
        parent = psutil.Process(os.getpid())
        children = parent.children(recursive=True)
        # ジョブ開始後に生成された子プロセスのみ対象
        targets = [
            c for c in children if c.create_time() >= rj.started_at - 1.0
        ]
        for child in targets:
            try:
                child.kill()
                killed += 1
            except psutil.NoSuchProcess:
                pass
        if targets:
            psutil.wait_procs(targets, timeout=5)
    except psutil.NoSuchProcess:
        pass
    return killed


def force_stop_running_job(
    rj: RunningJob,
) -> None:
    """ジョブを確実に停止（子プロセスも強制終了）

    1. cancel_event でgraceful停止を試行
    2. タイムアウトしたら子プロセスを強制終了
    3. スレッド終了を待機

    Args:
        rj: 停止対象のRunningJob
    """
    rj.cancel_event.set()

    # graceful停止を短時間試行
    rj.thread.join(timeout=5)

    if rj.thread.is_alive():
        # 子プロセス強制終了
        killed = _kill_job_child_processes(rj)
        if killed > 0:
            logger.info(
                ">>> [%s] 子プロセス%d個を強制終了",
                rj.job.id,
                killed,
            )
        # スレッド終了を再待機
        rj.thread.join(timeout=10)
        if rj.thread.is_alive():
            logger.warning(
                ">>> [%s] デーモンスレッド残存（プロセス終了時に回収）",
                rj.job.id,
            )

    # 結果ファイル削除
    _rpath = RESULTS_DIR / f"{rj.result_id}.json"
    if _rpath.exists():
        _rpath.unlink()
        logger.info(
            ">>> 結果ファイル削除: %s",
            _rpath.name,
        )


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
        force_stop_running_job(rj)
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
        f"  CPUスレッド: {cpu_threads} (1年={THREADS_PER_YEAR}スレッド)",
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

    # 状態読み込み + キューハッシュ検証 + 中断ジョブクリーンアップ
    state = QueueState.load()
    state.sync_with_queue()
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
        rid: str = "",
    ) -> None:
        """ジョブ実行スレッド（typeに応じて振り分け）"""
        if job.type in ("multi_pair", "portfolio"):
            holder[0] = execute_multi_pair_job(
                job,
                cancel_ev,
                max_year_workers=workers,
                result_id=rid,
            )
        else:
            holder[0] = execute_job(
                job,
                cancel_ev,
                workers,
                rid,
            )

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
                            ">>> 全ジョブ停止中 (%d件)...",
                            len(running_jobs),
                        )
                        # 全ジョブにcancel通知
                        for rj in running_jobs:
                            rj.cancel_event.set()
                        # 全ジョブを確実に停止
                        for rj in running_jobs:
                            force_stop_running_job(rj)
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
                                ">>> CPUスレッド: %d → %d",
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
                                ">>> 無効な値: %s (例: cpu 8)",
                                parts[1],
                            )
                    else:
                        logger.info(
                            ">>> 現在のCPUスレッド: %d (使用例: cpu 8)",
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
                    print(f"  状態: {'一時停止' if paused else '稼働中'}")
                    print(f"  CPUスレッド: {used:.1f}/{cpu_threads} 使用中")
                    print(f"  実行中ジョブ: {len(running_jobs)}件")
                    for rj in running_jobs:
                        elapsed = time.time() - rj.started_at
                        # multi_pairの場合はシンボル一覧
                        if rj.job.type in (
                            "multi_pair",
                            "portfolio",
                        ):
                            _sym = ",".join(rj.job.symbols)
                            _label = f"[multi_pair] {_sym}"
                        else:
                            _label = rj.job.symbol
                        print(
                            f"    - [{rj.job.id}]"
                            f" {_label}"
                            f" {rj.job.years}"
                            f" workers="
                            f"{rj.max_year_workers}"
                            f" cost={rj.cpu_cost:.1f}"
                            f" ({elapsed:.0f}s)"
                        )
                    print(f"  進捗: {_done}/{_total} (残り{_remain}件)")

                elif cmd == "quit":
                    if running_jobs:
                        logger.info(
                            ">>> 全ジョブ停止中...",
                        )
                        for rj in running_jobs:
                            rj.cancel_event.set()
                        for rj in running_jobs:
                            force_stop_running_job(rj)
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
                if _res.job_type == "multi_pair":
                    _pm = _res.portfolio_metrics
                    logger.info(
                        "[%s] マルチペア完了:"
                        " profit=%.0f,"
                        " 年間=%.1f%%,"
                        " WR=%.1f%%,"
                        " PF=%.2f,"
                        " DD=%.2f%%,"
                        " Sharpe=%.2f,"
                        " 月間+=%.1f%%"
                        " (%dペア, %.0fs)",
                        _res.job_id,
                        _res.net_profit,
                        _pm.get(
                            "annual_return_pct",
                            0,
                        ),
                        _res.win_rate,
                        _res.profit_factor,
                        _res.max_drawdown,
                        _res.sharpe_ratio,
                        _res.monthly_plus_rate,
                        len(_res.pair_details),
                        _res.elapsed_seconds,
                    )
                    # 制限発動統計
                    _bg = _pm.get(
                        "blocked_global",
                        0,
                    )
                    _bp = _pm.get(
                        "blocked_per_pair",
                        0,
                    )
                    _be = _pm.get(
                        "blocked_exposure",
                        0,
                    )
                    if _bg or _bp or _be:
                        logger.info(
                            "  制限発動: global=%d, per_pair=%d, exposure=%d",
                            _bg,
                            _bp,
                            _be,
                        )
                    # 各ペアの結果も表示
                    for pd in _res.pair_details:
                        logger.info(
                            "  %s:"
                            " profit=%.0f,"
                            " WR=%.1f%%,"
                            " PF=%.2f,"
                            " 寄与=%.1f%%",
                            pd["symbol"],
                            pd["net_profit"],
                            pd["win_rate"],
                            pd["profit_factor"],
                            pd.get(
                                "contribution_pct",
                                0,
                            ),
                        )
                else:
                    logger.info(
                        "[%s] 完了: profit=%.0f,"
                        " WR=%.1f%%,"
                        " PF=%.2f,"
                        " DD=%.2f%%"
                        " (%.0fs)",
                        _res.job_id,
                        _res.net_profit,
                        _res.win_rate,
                        _res.profit_factor,
                        _res.max_drawdown,
                        _res.elapsed_seconds,
                    )
                # 元のjob.idで完了記録（連番なし）
                _orig_id = rj.job.id
                if _orig_id not in state.completed_ids:
                    state.completed_ids.append(_orig_id)
                state.save()
            elif _res and _res.status == "cancelled":
                logger.info(
                    "[%s] キャンセル済み",
                    _res.job_id,
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
            running_ids = {rj.job.id for rj in running_jobs}
            _done = set(state.completed_ids)
            for job in jobs:
                # 完了済みスキップ
                if job.id in _done:
                    continue

                # 実行中スキップ
                if job.id in running_ids:
                    continue

                # CPUバジェットチェック
                workers = job.effective_year_workers()
                cost = job.cpu_cost()
                used = calc_used_threads(running_jobs)
                remaining = cpu_threads - used

                if cost > remaining:
                    # バジェット不足 → 次サイクルで
                    break

                # 連番付き結果ID生成
                _cnt = state.next_counter()
                _rid = f"{_cnt:03d}_{job.id}"

                # ジョブ起動ログ
                if job.type in (
                    "multi_pair",
                    "portfolio",
                ):
                    _sym_label = "[multi_pair] " + ",".join(job.symbols)
                else:
                    _sym_label = job.symbol
                logger.info(
                    "[%s] 開始: %s %s %s"
                    " (workers=%d, cost=%.1f,"
                    " used=%.1f/%.0f)",
                    _rid,
                    _sym_label,
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
                        job,
                        cancel_ev,
                        holder,
                        workers,
                        _rid,
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
                    result_id=_rid,
                )
                running_jobs.append(rj_new)
                running_ids.add(job.id)
                t.start()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
