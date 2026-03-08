#!/usr/bin/env python3
"""バックテストキューランナー

キューファイル（backtest_queue.json）を監視し、
ジョブを順次実行する常駐スクリプト。

使い方:
    uv run python scripts/backtest_queue_runner.py

対話コマンド:
    stop   - 実行中ジョブを即時停止、ログ削除、キュー先頭に戻す
    pause  - 新規ジョブの取得を一時停止
    resume - 一時停止を解除
    status - 現在の状態を表示
    quit   - ランナーを終了
"""

from __future__ import annotations

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
    overrides: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        """dictからJob生成"""
        return cls(
            id=d["id"],
            symbol=d.get("symbol", "USDJPY"),
            years=d.get("years", "2023-2025"),
            description=d.get("description", ""),
            overrides=d.get("overrides", {}),
        )


@dataclass
class JobResult:
    """ジョブ実行結果"""

    job_id: str
    status: str = "pending"  # pending|running|completed|failed|cancelled
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


@dataclass
class QueueState:
    """キュー処理状態"""

    next_index: int = 0
    completed_ids: list[str] = field(default_factory=list)

    def save(self) -> None:
        """状態ファイルに保存"""
        STATE_FILE.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
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
                next_index=data.get("next_index", 0),
                completed_ids=data.get("completed_ids", []),
            )
        return cls()


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
) -> JobResult:
    """バックテストジョブを実行

    Args:
        job: 実行するジョブ
        cancel_event: キャンセルイベント

    Returns:
        JobResult: 実行結果
    """
    from autotrader.backtest.runner import BacktestRunner
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

        # bot overrides 構築（run_backtest.py と同じ優先順位）
        pip_unit = (
            0.01 if "JPY" in job.symbol.upper() else 0.0001
        )
        bot_ovr: dict[str, Any] = {}
        # L1: プリセット
        bot_ovr.update({
            "max_positions": preset.max_positions,
            "bonus_max_positions": preset.bonus_max_positions,
            "bonus_score_threshold": preset.bonus_score_threshold,
            "base_risk_pct": preset.base_risk_pct,
            "max_lot_per_trade": preset.max_lot_per_trade,
            "max_total_exposure_lot": preset.max_total_exposure_lot,
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
        data_dir = bt_ovr.get("data_dir", DEFAULT_DATA_DIR)
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
            bonus_max_positions=bot_config.bonus_max_positions,
            bonus_score_threshold=bot_config.bonus_score_threshold,
            pip_value=preset.pip_value,
            commission_per_lot=preset.commission_per_lot,
            use_short_timeframe=True,
        )

        service = BacktestService(svc_config)
        runner = service.create_runner()

        # キャンセルコールバック設定
        runner.set_cancel_callback(cancel_event.is_set)

        # データ読み込み
        logger.info(
            "[%s] データ読み込み中... (%s %s)",
            job.id, job.symbol, job.years,
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
                total_months = len(bt_result.monthly_results)
                result.monthly_plus_rate = (
                    plus_months / total_months * 100
                    if total_months > 0
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
    import queue as _q  # noqa: PLC0415

    while True:
        try:
            line = input().strip().lower()
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

    print("=" * 60)
    print("  バックテストキューランナー")
    print("=" * 60)
    print(f"  キューファイル: {QUEUE_FILE}")
    print(f"  結果ディレクトリ: {RESULTS_DIR}")
    print(f"  ポーリング間隔: {POLL_INTERVAL}s")
    print()
    print("  コマンド:")
    print("    stop   - 実行中ジョブ停止+ログ削除+キュー先頭")
    print("    pause  - 新規ジョブ取得を一時停止")
    print("    resume - 一時停止解除")
    print("    status - 現在の状態表示")
    print("    quit   - ランナー終了")
    print("=" * 60)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

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
    state = QueueState.load()
    cancel_event = threading.Event()
    paused = False
    current_job: Job | None = None
    job_thread: threading.Thread | None = None
    job_result_holder: list[JobResult | None] = [None]

    def _run_job(j: Job) -> None:
        """ジョブ実行スレッド"""
        job_result_holder[0] = execute_job(j, cancel_event)

    while True:
        # コマンド処理
        try:
            while True:
                cmd = cmd_queue.get_nowait()
                if cmd == "stop":
                    if job_thread and job_thread.is_alive():
                        logger.info(">>> ジョブ停止中...")
                        cancel_event.set()
                        job_thread.join(timeout=10)
                        # ログ削除
                        if current_job:
                            _result_path = (
                                RESULTS_DIR / f"{current_job.id}.json"
                            )
                            if _result_path.exists():
                                _result_path.unlink()
                                logger.info(
                                    ">>> ログ削除: %s",
                                    _result_path.name,
                                )
                        # キュー先頭に戻す
                        state.next_index = 0
                        state.completed_ids.clear()
                        state.save()
                        logger.info(
                            ">>> キュー先頭にリセット",
                        )
                        cancel_event.clear()
                        current_job = None
                        job_thread = None
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

                elif cmd == "status":
                    _jobs = load_queue()
                    _running = (
                        current_job.id
                        if current_job
                        else "なし"
                    )
                    print(
                        f"  状態: {'一時停止' if paused else '実行中'}"
                    )
                    print(f"  現在のジョブ: {_running}")
                    print(
                        f"  キュー位置: {state.next_index}/{len(_jobs)}"
                    )
                    print(
                        f"  完了済み: {len(state.completed_ids)}件"
                    )

                elif cmd == "quit":
                    if job_thread and job_thread.is_alive():
                        cancel_event.set()
                        job_thread.join(timeout=10)
                    logger.info(">>> ランナー終了")
                    return

        except _q.Empty:
            pass

        # ジョブ完了チェック
        if job_thread and not job_thread.is_alive():
            _res = job_result_holder[0]
            if _res and _res.status == "completed":
                logger.info(
                    "[%s] 完了: profit=%.0f, WR=%.1f%%, DD=%.2f%%",
                    _res.job_id,
                    _res.net_profit,
                    _res.win_rate,
                    _res.max_drawdown,
                )
                if _res.job_id not in state.completed_ids:
                    state.completed_ids.append(_res.job_id)
                state.next_index += 1
                state.save()
            elif _res and _res.status == "cancelled":
                logger.info("[%s] キャンセル済み", _res.job_id)
            elif _res and _res.status == "failed":
                logger.error(
                    "[%s] 失敗: %s", _res.job_id, _res.error,
                )
                state.next_index += 1
                state.save()
            current_job = None
            job_thread = None
            cancel_event.clear()

        # 新規ジョブ取得
        if (
            not paused
            and job_thread is None
            and not cancel_event.is_set()
        ):
            jobs = load_queue()
            if state.next_index < len(jobs):
                job = jobs[state.next_index]
                # 既に完了済みならスキップ
                if job.id in state.completed_ids:
                    state.next_index += 1
                    state.save()
                    continue
                # 結果ファイルが既にあればスキップ
                _existing = RESULTS_DIR / f"{job.id}.json"
                if _existing.exists():
                    try:
                        _ex_data = json.loads(
                            _existing.read_text(encoding="utf-8"),
                        )
                        if _ex_data.get("status") == "completed":
                            logger.info(
                                "[%s] スキップ（結果ファイルあり）",
                                job.id,
                            )
                            if job.id not in state.completed_ids:
                                state.completed_ids.append(job.id)
                            state.next_index += 1
                            state.save()
                            continue
                    except (json.JSONDecodeError, KeyError):
                        pass

                logger.info(
                    "[%s] 開始: %s %s %s",
                    job.id,
                    job.symbol,
                    job.years,
                    job.description,
                )
                current_job = job
                cancel_event.clear()
                job_result_holder[0] = None
                job_thread = threading.Thread(
                    target=_run_job,
                    args=(job,),
                    daemon=True,
                )
                job_thread.start()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
