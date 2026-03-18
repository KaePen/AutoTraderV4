"""バックテストキューランナー Web UI

キューランナーの状態を監視・制御するWebダッシュボード。
runner_state.json を読み取り、runner_commands.json で制御する。

使い方:
    uv run python scripts/backtest_web_ui.py --port 8888
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# パス設定（paths.py に集約）
from autotrader.config.paths import (
    get_bt_webui_cmd_file,
    get_queue_file,
    get_results_dir,
    get_runner_cmd_file,
    get_runner_state_file,
)

_QUEUE_FILE = get_queue_file()
_STATE_FILE = get_runner_state_file()
_CMD_FILE = get_runner_cmd_file()
_RESULTS_DIR = get_results_dir()
_BT_WEBUI_CMD_FILE = get_bt_webui_cmd_file()
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("web_ui")

app = FastAPI(title="バックテストキューランナー")


# ===================================================================
# リクエストモデル
# ===================================================================


class CommandRequest(BaseModel):
    """コマンドリクエスト"""

    command: str


# ===================================================================
# ヘルパー
# ===================================================================


def _read_json(path: Path) -> dict[str, Any]:
    """JSONファイルを安全に読み取り"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _merge_compound_result(
    result_dir: Path,
    data: dict[str, Any],
) -> None:
    """compound_result.jsonが存在すればportfolio_metricsに統合"""
    pm = data.get("portfolio_metrics")
    if isinstance(pm, dict) and pm.get("compound_metrics"):
        return  # すでにcompound_metricsが含まれている
    cr_path = result_dir / "compound_result.json"
    if not cr_path.exists():
        return
    try:
        cr = json.loads(
            cr_path.read_text(encoding="utf-8"),
        )
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(pm, dict):
        data["portfolio_metrics"] = {}
        pm = data["portfolio_metrics"]
    pm["compound_metrics"] = cr


def _list_result_files() -> list[dict[str, Any]]:
    """完了済み結果ファイル一覧を読み取り（新旧両形式対応）"""
    if not _RESULTS_DIR.exists():
        return []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    # 新形式: サブディレクトリ内の result.json
    for d in sorted(
        _RESULTS_DIR.iterdir(), reverse=True,
    ):
        if d.is_dir():
            rp = d / "result.json"
            if not rp.exists():
                continue
            try:
                data = json.loads(
                    rp.read_text(encoding="utf-8"),
                )
                data["_file"] = f"{d.name}/result.json"
                # compound_result.json統合
                _merge_compound_result(d, data)
                results.append(data)
                seen.add(d.name)
            except (json.JSONDecodeError, OSError):
                continue
    # 旧形式: フラットJSON（後方互換）
    for f in sorted(
        _RESULTS_DIR.glob("*.json"), reverse=True,
    ):
        if f.stem in seen:
            continue
        try:
            data = json.loads(
                f.read_text(encoding="utf-8"),
            )
            data["_file"] = f.name
            results.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return results


# ===================================================================
# APIエンドポイント
# ===================================================================


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """ダッシュボードHTML"""
    tpl = _TEMPLATES_DIR / "queue_dashboard.html"
    if not tpl.exists():
        return HTMLResponse(
            "<h1>テンプレートが見つかりません</h1>",
            status_code=500,
        )
    html = tpl.read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/status")
async def api_status() -> JSONResponse:
    """キューランナー状態"""
    state = _read_json(_STATE_FILE)
    return JSONResponse(state)


@app.get("/api/queue")
async def api_queue() -> JSONResponse:
    """キューファイル内容"""
    data = _read_json(_QUEUE_FILE)
    return JSONResponse(data)


@app.get("/api/results")
async def api_results() -> JSONResponse:
    """完了済みジョブ結果一覧"""
    results = _list_result_files()
    return JSONResponse(results)


def _find_result_json(result_id: str) -> Path | None:
    """新形式(dir/result.json)と旧形式(flat .json)を検索"""
    # 完全一致: 新形式
    new = _RESULTS_DIR / result_id / "result.json"
    if new.exists():
        return new
    # 完全一致: 旧形式
    old = _RESULTS_DIR / f"{result_id}.json"
    if old.exists():
        return old
    # 部分一致: 新形式
    for d in _RESULTS_DIR.iterdir():
        if d.is_dir() and result_id in d.name:
            rp = d / "result.json"
            if rp.exists():
                return rp
    # 部分一致: 旧形式
    for f in _RESULTS_DIR.glob("*.json"):
        if result_id in f.stem:
            return f
    return None


@app.get("/api/result/{result_id}")
async def api_result_detail(
    result_id: str,
) -> JSONResponse:
    """特定ジョブの詳細結果"""
    path = _find_result_json(result_id)
    if path is None:
        return JSONResponse(
            {"error": "結果が見つかりません"},
            status_code=404,
        )
    try:
        data = json.loads(
            path.read_text(encoding="utf-8"),
        )
        return JSONResponse(data)
    except (json.JSONDecodeError, OSError):
        return JSONResponse(
            {"error": "結果の読み取りに失敗"},
            status_code=500,
        )


@app.get("/api/result/{result_id}/trades")
async def api_result_trades(
    result_id: str,
) -> Any:
    """トレードCSVダウンロード"""
    from fastapi.responses import FileResponse

    csv_path = _RESULTS_DIR / result_id / "trades.csv"
    if not csv_path.exists():
        return JSONResponse(
            {"error": "trades.csv が見つかりません"},
            status_code=404,
        )
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=f"{result_id}_trades.csv",
    )


@app.get("/api/result/{result_id}/blocked")
async def api_result_blocked(
    result_id: str,
) -> Any:
    """ブロックシグナルCSVダウンロード"""
    from fastapi.responses import FileResponse

    csv_path = (
        _RESULTS_DIR / result_id / "blocked_signals.csv"
    )
    if not csv_path.exists():
        return JSONResponse(
            {"error": "blocked_signals.csv が見つかりません"},
            status_code=404,
        )
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=f"{result_id}_blocked_signals.csv",
    )


@app.get("/api/result/{result_id}/whatif")
async def api_result_whatif(
    result_id: str,
) -> Any:
    """What-IfトレードCSVダウンロード"""
    from fastapi.responses import FileResponse

    csv_path = (
        _RESULTS_DIR / result_id / "whatif_trades.csv"
    )
    if not csv_path.exists():
        return JSONResponse(
            {"error": "whatif_trades.csv が見つかりません"},
            status_code=404,
        )
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=f"{result_id}_whatif_trades.csv",
    )


@app.post("/api/command")
async def api_command(req: CommandRequest) -> JSONResponse:
    """コマンド送信"""
    cmd = req.command.strip().lower()
    valid = {
        "stop", "pause", "resume", "quit", "status",
    }
    # cpu N コマンド対応
    if cmd.startswith("cpu "):
        pass  # OK
    elif cmd not in valid:
        return JSONResponse(
            {
                "error": f"無効なコマンド: {cmd}",
                "valid": list(valid),
            },
            status_code=400,
        )

    # コマンドファイルに書き込み
    existing: list[str] = []
    if _CMD_FILE.exists():
        try:
            data = json.loads(
                _CMD_FILE.read_text(encoding="utf-8"),
            )
            existing = data.get("commands", [])
        except (json.JSONDecodeError, OSError):
            pass

    existing.append(cmd)
    _CMD_FILE.write_text(
        json.dumps(
            {"commands": existing},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    logger.info("コマンド送信: %s", cmd)
    return JSONResponse({"ok": True, "command": cmd})


# ===================================================================
# メイン
# ===================================================================


def main() -> None:
    """Web UIサーバー起動"""
    parser = argparse.ArgumentParser(
        description="バックテストキューランナー Web UI",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8888,
        help="ポート番号 (default: 8888)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="ブラウザを自動で開かない",
    )
    args = parser.parse_args()

    import uvicorn

    # コマンドファイル監視スレッド起動（supervisor連携）
    from autotrader.ipc import command_watcher

    threading.Thread(
        target=command_watcher,
        args=(_BT_WEBUI_CMD_FILE, logger),
        daemon=True,
    ).start()

    if not args.no_browser:
        def _open() -> None:
            time.sleep(1.5)
            webbrowser.open(f"http://localhost:{args.port}")

        threading.Thread(
            target=_open, daemon=True,
        ).start()

    logger.info("Web UI: http://localhost:%d", args.port)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port,
        log_level="warning",
        ws="wsproto",
    )


if __name__ == "__main__":
    main()
