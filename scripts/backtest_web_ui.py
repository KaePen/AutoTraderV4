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
import sys
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# パス設定
_DATA_ROOT = Path("D:/Projects/AutoTraderV4_data")
_QUEUE_FILE = _DATA_ROOT / "backtest_queue.json"
_STATE_FILE = _DATA_ROOT / "runner_state.json"
_CMD_FILE = _DATA_ROOT / "runner_commands.json"
_RESULTS_DIR = _DATA_ROOT / "backtest_results"
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


def _list_result_files() -> list[dict[str, Any]]:
    """完了済み結果ファイル一覧を読み取り"""
    if not _RESULTS_DIR.exists():
        return []
    results: list[dict[str, Any]] = []
    for f in sorted(
        _RESULTS_DIR.glob("*.json"), reverse=True,
    ):
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


@app.get("/api/result/{result_id}")
async def api_result_detail(
    result_id: str,
) -> JSONResponse:
    """特定ジョブの詳細結果"""
    # result_idに対応するファイルを検索
    for f in _RESULTS_DIR.glob("*.json"):
        if result_id in f.stem:
            try:
                data = json.loads(
                    f.read_text(encoding="utf-8"),
                )
                return JSONResponse(data)
            except (json.JSONDecodeError, OSError):
                pass
    return JSONResponse(
        {"error": "結果が見つかりません"},
        status_code=404,
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

    if not args.no_browser:
        import threading

        def _open() -> None:
            import time
            time.sleep(1.5)
            webbrowser.open(f"http://localhost:{args.port}")

        threading.Thread(
            target=_open, daemon=True,
        ).start()

    print(f"Web UI: http://localhost:{args.port}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
