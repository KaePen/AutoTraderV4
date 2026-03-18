"""プロセススーパーバイザー + WebUI

origin/main の更新を自動検知し、管理プロセスを安全に再起動する。
子プロセスは独立動作（supervisor停止時も稼働継続）。

使い方:
    uv run python scripts/process_supervisor.py
    → http://localhost:8899
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
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

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("supervisor")

# ===================================================================
# 定数
# ===================================================================

from autotrader.config.paths import (
    get_state_dir,
    get_supervisor_events_file,
    get_supervisor_state_file,
)

PROJECT_DIR = Path("D:/Projects/AutoTraderV4")
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

STATE_FILE = get_supervisor_state_file()
EVENTS_FILE = get_supervisor_events_file()
_STATE_DIR = get_state_dir()

SUPERVISOR_PORT = 8899
GIT_POLL_INTERVAL = 60  # 秒
HEALTH_CHECK_INTERVAL = 5  # 秒
STATE_WRITE_INTERVAL = 3  # 秒
MAX_EVENTS = 100

# graceful shutdown待機（秒）
GRACEFUL_TIMEOUT_LONG = 30  # queue_runner用
GRACEFUL_TIMEOUT_SHORT = 10  # WebUI用


# ===================================================================
# プロセス設定
# ===================================================================


@dataclass
class ProcessConfig:
    """管理プロセス定義"""

    name: str
    label: str
    command: list[str]
    cwd: str
    restart_on_update: bool
    cmd_file: str
    stop_command: str
    detect_pattern: str
    port: int | None
    graceful_timeout: int = GRACEFUL_TIMEOUT_SHORT


MANAGED_PROCESSES: list[ProcessConfig] = [
    ProcessConfig(
        name="queue_runner",
        label="バックテストキューランナー",
        command=[
            "uv", "run", "python",
            "scripts/backtest_queue_runner.py",
            "--cpu-threads", "12",
        ],
        cwd=str(PROJECT_DIR),
        restart_on_update=True,
        cmd_file="runner_commands.json",
        stop_command="shutdown",
        detect_pattern="backtest_queue_runner.py",
        port=None,
        graceful_timeout=GRACEFUL_TIMEOUT_LONG,
    ),
    ProcessConfig(
        name="bt_webui",
        label="BT WebUI",
        command=[
            "uv", "run", "python",
            "scripts/backtest_web_ui.py",
            "--port", "8888", "--no-browser",
        ],
        cwd=str(PROJECT_DIR),
        restart_on_update=True,
        cmd_file="bt_webui_commands.json",
        stop_command="shutdown",
        detect_pattern="backtest_web_ui.py",
        port=8888,
    ),
    ProcessConfig(
        name="live_webui",
        label="ライブ WebUI",
        command=[
            "uv", "run", "python", "-m", "autotrader.web",
        ],
        cwd=str(PROJECT_DIR),
        restart_on_update=False,
        cmd_file="live_webui_commands.json",
        stop_command="shutdown",
        detect_pattern="-m autotrader.web",
        port=8000,
    ),
]


# ===================================================================
# プロセス状態
# ===================================================================


@dataclass
class ProcessState:
    """実行中プロセスの状態"""

    config: ProcessConfig
    process: subprocess.Popen | None = None
    pid: int | None = None
    started_at: str | None = None
    restart_count: int = 0
    status: str = "stopped"  # running / stopped / stopping

    def to_dict(self) -> dict[str, Any]:
        """API応答用辞書"""
        uptime = ""
        if self.started_at and self.status == "running":
            delta = (
                datetime.now()
                - datetime.fromisoformat(self.started_at)
            )
            secs = int(delta.total_seconds())
            if secs >= 3600:
                uptime = f"{secs // 3600}h {(secs % 3600) // 60}m"
            elif secs >= 60:
                uptime = f"{secs // 60}m {secs % 60}s"
            else:
                uptime = f"{secs}s"
        return {
            "name": self.config.name,
            "label": self.config.label,
            "pid": self.pid,
            "status": self.status,
            "started_at": self.started_at,
            "uptime": uptime,
            "restart_count": self.restart_count,
            "port": self.config.port,
            "restart_on_update": self.config.restart_on_update,
        }


# ===================================================================
# Git状態
# ===================================================================


@dataclass
class GitState:
    """Git状態"""

    local_hash: str = ""
    local_message: str = ""
    local_date: str = ""
    remote_hash: str = ""
    remote_message: str = ""
    remote_date: str = ""
    has_update: bool = False
    last_fetch: str = ""
    auto_pull: bool = True
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """API応答用辞書"""
        return {
            "local_hash": self.local_hash[:7] if self.local_hash else "",
            "local_message": self.local_message,
            "local_date": self.local_date,
            "remote_hash": (
                self.remote_hash[:7] if self.remote_hash else ""
            ),
            "remote_message": self.remote_message,
            "remote_date": self.remote_date,
            "has_update": self.has_update,
            "last_fetch": self.last_fetch,
            "auto_pull": self.auto_pull,
            "last_error": self.last_error,
        }


# ===================================================================
# イベントログ
# ===================================================================


@dataclass
class EventLog:
    """イベントログ管理"""

    events: list[dict[str, str]] = field(default_factory=list)
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
    )

    def add(self, event_type: str, message: str) -> None:
        """イベント追加"""
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            "message": message,
        }
        with self._lock:
            self.events.insert(0, entry)
            self.events = self.events[:MAX_EVENTS]
        logger.info("[%s] %s", event_type, message)

    def to_list(self) -> list[dict[str, str]]:
        """イベントリスト取得"""
        with self._lock:
            return list(self.events)


# ===================================================================
# スーパーバイザーコア
# ===================================================================


class Supervisor:
    """プロセス管理・Git監視の統合クラス"""

    def __init__(self) -> None:
        self.processes: dict[str, ProcessState] = {}
        self.git_state = GitState()
        self.event_log = EventLog()
        self.started_at = datetime.now().isoformat()
        self._lock = threading.Lock()

        # プロセス状態の初期化
        for cfg in MANAGED_PROCESSES:
            self.processes[cfg.name] = ProcessState(config=cfg)

    # ---------------------------------------------------------------
    # プロセス発見（コマンドライン解析）
    # ---------------------------------------------------------------

    def _find_existing_processes(self) -> dict[str, int]:
        """psutilで既存プロセスを検出しPIDを返す。

        PowerShell/WMI はプロセス数が多いとタイムアウト
        するため、psutil で直接取得する（~30ms）。
        """
        import psutil

        found: dict[str, int] = {}
        try:
            for proc in psutil.process_iter(
                ["pid", "name", "cmdline"],
            ):
                try:
                    info = proc.info
                    name = info.get("name") or ""
                    if "python" not in name.lower():
                        continue
                    cmdline = " ".join(
                        info.get("cmdline") or [],
                    )
                    for cfg in MANAGED_PROCESSES:
                        if cfg.detect_pattern in cmdline:
                            found[cfg.name] = info["pid"]
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                ):
                    continue
        except Exception as e:
            logger.warning("プロセス検出エラー: %s", e)
        return found

    # ---------------------------------------------------------------
    # ファイルIPCコマンド送信
    # ---------------------------------------------------------------

    def _send_file_command(
        self,
        config: ProcessConfig,
        command: str,
    ) -> None:
        """コマンドファイル経由でプロセスに指示を送信"""
        cmd_path = _STATE_DIR / config.cmd_file
        data = {"commands": [command]}
        try:
            cmd_path.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(
                "%s にコマンド送信: %s → %s",
                config.name,
                command,
                cmd_path,
            )
        except OSError as e:
            logger.error(
                "コマンドファイル書き込み失敗: %s", e,
            )

    # ---------------------------------------------------------------
    # プロセス起動 / 停止
    # ---------------------------------------------------------------

    def start_process(self, name: str) -> bool:
        """プロセスを起動"""
        with self._lock:
            ps = self.processes.get(name)
            if not ps:
                return False
            if (
                ps.status == "running"
                and ps.process
                and ps.process.poll() is None
            ):
                return True  # すでに稼働中

            cfg = ps.config
            try:
                proc = subprocess.Popen(
                    cfg.command,
                    cwd=cfg.cwd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32"
                        else 0
                    ),
                )
                ps.process = proc
                ps.pid = proc.pid
                ps.started_at = datetime.now().isoformat()
                ps.status = "running"
                self.event_log.add(
                    "started",
                    f"{cfg.label} 起動 (PID: {proc.pid})",
                )
                return True
            except OSError as e:
                self.event_log.add(
                    "error",
                    f"{cfg.label} 起動失敗: {e}",
                )
                return False

    def stop_process(
        self,
        name: str,
        *,
        wait: bool = True,
    ) -> bool:
        """プロセスをgracefulに停止"""
        with self._lock:
            ps = self.processes.get(name)
            if not ps or ps.status == "stopped":
                return False
            cfg = ps.config
            ps.status = "stopping"
            proc = ps.process
            pid = ps.pid

        # ファイルIPC経由で停止コマンド送信
        self._send_file_command(cfg, cfg.stop_command)

        if not wait:
            return True

        # graceful待機
        timeout = cfg.graceful_timeout
        deadline = time.time() + timeout

        # Popen管理のプロセスの場合
        if proc and proc.poll() is None:
            while time.time() < deadline:
                if proc.poll() is not None:
                    self.event_log.add(
                        "stopped",
                        f"{cfg.label} 停止 (graceful)",
                    )
                    with self._lock:
                        ps.status = "stopped"
                        ps.process = None
                        ps.pid = None
                    return True
                time.sleep(1)

            # タイムアウト: 強制終了
            self._force_kill(ps)
            return True

        # 外部プロセス（PID指定）の場合
        if pid:
            while time.time() < deadline:
                if not self._is_pid_alive(pid):
                    self.event_log.add(
                        "stopped",
                        f"{cfg.label} 停止 (graceful)",
                    )
                    with self._lock:
                        ps.status = "stopped"
                        ps.pid = None
                    return True
                time.sleep(1)

            # タイムアウト: 強制終了
            self._force_kill(ps)
            return True

        with self._lock:
            ps.status = "stopped"
        return True

    def _force_kill(self, ps: ProcessState) -> None:
        """強制終了"""
        cfg = ps.config
        pid = ps.pid or (
            ps.process.pid if ps.process else None
        )
        if pid:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    timeout=5,
                )
                self.event_log.add(
                    "force_killed",
                    f"{cfg.label} 強制終了 (PID: {pid})",
                )
            except (subprocess.TimeoutExpired, OSError):
                pass
        with self._lock:
            ps.status = "stopped"
            ps.process = None
            ps.pid = None

    def restart_process(self, name: str) -> bool:
        """プロセスを再起動"""
        ps = self.processes.get(name)
        if not ps:
            return False
        self.stop_process(name, wait=True)
        result = self.start_process(name)
        if result:
            ps.restart_count += 1
        return result

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """PIDのプロセスが生存しているか確認"""
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=5,
                encoding="utf-8",
                errors="replace",
            )
            return str(pid) in result.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False

    # ---------------------------------------------------------------
    # Git操作
    # ---------------------------------------------------------------

    def _git_cmd(
        self,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        """gitコマンド実行"""
        return subprocess.run(
            ["git", "-C", str(PROJECT_DIR), *args],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

    def fetch_and_check(self) -> bool:
        """git fetchして更新を確認"""
        try:
            self._git_cmd("fetch", "origin", "main")
            self.git_state.last_fetch = (
                datetime.now().strftime("%H:%M:%S")
            )

            # ローカルHEAD
            r = self._git_cmd(
                "log", "-1",
                "--format=%H%x1f%s%x1f%ci",
                "HEAD",
            )
            if r.returncode == 0:
                parts = r.stdout.strip().split("\x1f", 2)
                if len(parts) == 3:
                    self.git_state.local_hash = parts[0]
                    self.git_state.local_message = parts[1]
                    self.git_state.local_date = parts[2]

            # リモートHEAD
            r = self._git_cmd(
                "log", "-1",
                "--format=%H%x1f%s%x1f%ci",
                "origin/main",
            )
            if r.returncode == 0:
                parts = r.stdout.strip().split("\x1f", 2)
                if len(parts) == 3:
                    self.git_state.remote_hash = parts[0]
                    self.git_state.remote_message = parts[1]
                    self.git_state.remote_date = parts[2]

            self.git_state.has_update = (
                self.git_state.local_hash
                != self.git_state.remote_hash
                and bool(self.git_state.remote_hash)
            )
            self.git_state.last_error = ""
            return self.git_state.has_update
        except (subprocess.TimeoutExpired, OSError) as e:
            self.git_state.last_error = str(e)
            self.event_log.add("error", f"git fetch失敗: {e}")
            return False

    def pull_and_restart(self) -> dict[str, Any]:
        """git pull → 対象プロセス再起動"""
        result: dict[str, Any] = {
            "success": False,
            "restarted": [],
            "error": "",
        }

        # 1. 再起動対象プロセスを停止
        targets = [
            name for name, ps in self.processes.items()
            if ps.config.restart_on_update
            and ps.status == "running"
        ]
        for name in targets:
            self.stop_process(name, wait=True)

        # 2. git fetch + reset（mainへの直接コミット禁止のため常に安全）
        self.event_log.add("pull_start", "git pull開始")
        r = self._git_cmd("fetch", "origin", "main")
        if r.returncode != 0:
            err = r.stderr.strip() or r.stdout.strip()
            self.event_log.add(
                "pull_failed", f"git fetch失敗: {err}",
            )
            result["error"] = err
            for name in targets:
                self.start_process(name)
            self.event_log.add(
                "rollback",
                "fetch失敗のため旧コードで再起動",
            )
            return result

        r = self._git_cmd("reset", "--hard", "origin/main")
        if r.returncode != 0:
            err = r.stderr.strip() or r.stdout.strip()
            self.event_log.add(
                "pull_failed", f"git reset失敗: {err}",
            )
            result["error"] = err
            for name in targets:
                self.start_process(name)
            self.event_log.add(
                "rollback",
                "reset失敗のため旧コードで再起動",
            )
            return result

        # pullの結果からコミットハッシュ更新
        self.fetch_and_check()
        self.event_log.add(
            "pull_success",
            f"pull完了: {self.git_state.local_hash[:7]}",
        )

        # 3. 停止したプロセスを新コードで再起動
        for name in targets:
            if self.start_process(name):
                ps = self.processes[name]
                ps.restart_count += 1
                result["restarted"].append(name)

        self.event_log.add(
            "pull_restart_complete",
            f"{len(result['restarted'])}プロセス再起動",
        )
        result["success"] = True
        return result

    # ---------------------------------------------------------------
    # バックグラウンドスレッド
    # ---------------------------------------------------------------

    def _git_poll_loop(self) -> None:
        """Git更新を定期チェック"""
        # 起動時に即座に1回チェック
        self.fetch_and_check()
        while True:
            time.sleep(GIT_POLL_INTERVAL)
            try:
                has_update = self.fetch_and_check()
                if has_update and self.git_state.auto_pull:
                    self.event_log.add(
                        "update_detected",
                        "新しいコミットを検出、自動pull開始",
                    )
                    self.pull_and_restart()
            except Exception as e:
                logger.exception("git poll エラー: %s", e)

    def _health_monitor_loop(self) -> None:
        """子プロセス生存確認"""
        while True:
            time.sleep(HEALTH_CHECK_INTERVAL)
            for ps in self.processes.values():
                with self._lock:
                    if ps.status != "running":
                        continue
                    proc = ps.process
                    pid = ps.pid

                alive = False
                if proc:
                    alive = proc.poll() is None
                elif pid:
                    alive = self._is_pid_alive(pid)

                if not alive:
                    with self._lock:
                        ps.status = "stopped"
                        ps.process = None
                        old_pid = ps.pid
                        ps.pid = None
                    self.event_log.add(
                        "crashed",
                        f"{ps.config.label} が停止を検出"
                        f" (PID: {old_pid})",
                    )

    def _state_writer_loop(self) -> None:
        """状態ファイル定期書き出し"""
        while True:
            time.sleep(STATE_WRITE_INTERVAL)
            try:
                state = {
                    "started_at": self.started_at,
                    "processes": {
                        n: ps.to_dict()
                        for n, ps in self.processes.items()
                    },
                    "git": self.git_state.to_dict(),
                    "updated_at": (
                        datetime.now().isoformat()
                    ),
                }
                STATE_FILE.write_text(
                    json.dumps(
                        state,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                EVENTS_FILE.write_text(
                    json.dumps(
                        self.event_log.to_list(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass

    # ---------------------------------------------------------------
    # 起動シーケンス
    # ---------------------------------------------------------------

    def startup(self) -> None:
        """supervisor起動処理"""
        self.event_log.add("supervisor_start", "起動")

        # 既存プロセスの検出
        existing = self._find_existing_processes()
        for name, pid in existing.items():
            ps = self.processes[name]
            ps.pid = pid
            ps.status = "running"
            self.event_log.add(
                "discovered",
                f"{ps.config.label} 検出 (PID: {pid})",
            )

        # バックグラウンドスレッド起動
        for target in (
            self._git_poll_loop,
            self._health_monitor_loop,
            self._state_writer_loop,
        ):
            threading.Thread(
                target=target, daemon=True,
            ).start()


# ===================================================================
# FastAPI アプリケーション
# ===================================================================

sv = Supervisor()


@asynccontextmanager
async def _lifespan(_app: FastAPI):  # noqa: ANN201, RUF029
    """FastAPIライフサイクル管理"""
    sv.startup()
    yield


app = FastAPI(title="Process Supervisor", lifespan=_lifespan)


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """ダッシュボード表示"""
    tmpl = TEMPLATES_DIR / "supervisor_dashboard.html"
    html = tmpl.read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/status")
async def api_status() -> JSONResponse:
    """全プロセス状態 + Git状態"""
    # supervisor uptime
    delta = datetime.now() - datetime.fromisoformat(
        sv.started_at,
    )
    secs = int(delta.total_seconds())
    if secs >= 3600:
        uptime = f"{secs // 3600}h {(secs % 3600) // 60}m"
    elif secs >= 60:
        uptime = f"{secs // 60}m {secs % 60}s"
    else:
        uptime = f"{secs}s"

    return JSONResponse({
        "supervisor": {
            "started_at": sv.started_at,
            "uptime": uptime,
        },
        "processes": {
            n: ps.to_dict()
            for n, ps in sv.processes.items()
        },
        "git": sv.git_state.to_dict(),
    })


@app.get("/api/events")
async def api_events() -> JSONResponse:
    """イベントログ（最新100件）"""
    return JSONResponse(sv.event_log.to_list())


@app.post("/api/process/{name}/start")
async def api_start(name: str) -> JSONResponse:
    """プロセス起動"""
    if name not in sv.processes:
        return JSONResponse(
            {"ok": False, "error": "不明なプロセス"},
            status_code=404,
        )
    ok = sv.start_process(name)
    return JSONResponse({"ok": ok})


@app.post("/api/process/{name}/stop")
async def api_stop(name: str) -> JSONResponse:
    """プロセス停止"""
    if name not in sv.processes:
        return JSONResponse(
            {"ok": False, "error": "不明なプロセス"},
            status_code=404,
        )
    # 停止を非同期で実行（UIブロック防止）
    threading.Thread(
        target=sv.stop_process,
        args=(name,),
        kwargs={"wait": True},
        daemon=True,
    ).start()
    return JSONResponse({"ok": True, "status": "stopping"})


@app.post("/api/process/{name}/restart")
async def api_restart(name: str) -> JSONResponse:
    """プロセス再起動"""
    if name not in sv.processes:
        return JSONResponse(
            {"ok": False, "error": "不明なプロセス"},
            status_code=404,
        )
    threading.Thread(
        target=sv.restart_process,
        args=(name,),
        daemon=True,
    ).start()
    return JSONResponse({"ok": True, "status": "restarting"})


@app.post("/api/pull-restart")
async def api_pull_restart() -> JSONResponse:
    """手動 Pull & Restart"""
    def _run() -> None:
        sv.pull_and_restart()

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"ok": True, "status": "pulling"})


@app.post("/api/check-updates")
async def api_check_updates() -> JSONResponse:
    """手動 git fetch"""
    has_update = sv.fetch_and_check()
    return JSONResponse({
        "ok": True,
        "has_update": has_update,
        "git": sv.git_state.to_dict(),
    })


# ===================================================================
# メイン
# ===================================================================


def main() -> None:
    """supervisorサーバー起動"""
    import uvicorn

    logger.info(
        "Process Supervisor: http://localhost:%d",
        SUPERVISOR_PORT,
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=SUPERVISOR_PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
