"""FastAPIアプリケーション"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from autotrader.web.config import get_web_settings
from autotrader.web.routers import dashboard, signals, positions, trades
from autotrader.web.routers import indicators, candles, settings as settings_router
from autotrader.web.routers import backtest
from autotrader.web.routers import trading
from autotrader.web.schemas import ApiResponse, HealthResponse
from autotrader.web.websocket.handlers import (
    handle_market_websocket,
    handle_signals_websocket,
    handle_dashboard_websocket,
    handle_backtest_websocket,
)

# 起動時刻
_start_time: float = 0.0


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションライフサイクル

    Args:
        app: FastAPIアプリケーション
    """
    global _start_time
    _start_time = time.time()

    # DB初期化
    from autotrader.adapters.database.connection import init_db
    init_db()

    # ライブモード判定
    live_mode = os.environ.get("AUTOTRADER_LIVE", "").lower() in (
        "1", "true", "yes",
    )
    if live_mode:
        from autotrader.adapters.mt5.config import MT5Config
        from autotrader.live.config import LiveTradingConfig
        from autotrader.live.engine import LiveTradingEngine

        mt5_config = MT5Config(
            login=int(os.environ.get("MT5_LOGIN", "0")),
            password=os.environ.get("MT5_PASSWORD", ""),
            server=os.environ.get("MT5_SERVER", ""),
            transport=os.environ.get("MT5_TRANSPORT", "bridge"),
            bridge_host=os.environ.get(
                "MT5_BRIDGE_HOST", "localhost"
            ),
            bridge_port=int(os.environ.get(
                "MT5_BRIDGE_PORT", "18812"
            )),
        )
        live_config = LiveTradingConfig(
            symbol=os.environ.get("AUTOTRADER_SYMBOL", "USDJPY"),
            mt5_config=mt5_config,
            enable_auto_trade=os.environ.get(
                "AUTOTRADER_AUTO_TRADE", ""
            ).lower() in ("1", "true", "yes"),
        )
        engine = LiveTradingEngine(live_config)
        app.state.live_engine = engine
        logger.info("ライブモード: エンジン準備完了")

        # MT5自動接続
        try:
            await engine.start()
            logger.info(
                "ライブモード: MT5接続成功 (%s:%d)",
                mt5_config.bridge_host,
                mt5_config.bridge_port,
            )
        except Exception as e:
            logger.error("ライブモード: MT5接続失敗: %s", e)
            logger.info(
                "後からAPI経由で接続可能: "
                "POST /api/v1/trading/mt5/connect"
            )
    else:
        app.state.live_engine = None

    yield

    # シャットダウン
    engine = getattr(app.state, "live_engine", None)
    if engine and engine.running:
        await engine.stop()


def create_app() -> FastAPI:
    """FastAPIアプリケーションを作成

    Returns:
        FastAPI: アプリケーションインスタンス
    """
    web_settings = get_web_settings()

    app = FastAPI(
        title="AutoTrader WebUI",
        description="FXトレーディングボット リアルタイムダッシュボード",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS設定
    app.add_middleware(
        CORSMiddleware,
        allow_origins=web_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ルーター登録
    app.include_router(dashboard.router, prefix="/api/v1", tags=["dashboard"])
    app.include_router(signals.router, prefix="/api/v1", tags=["signals"])
    app.include_router(positions.router, prefix="/api/v1", tags=["positions"])
    app.include_router(trades.router, prefix="/api/v1", tags=["trades"])
    app.include_router(indicators.router, prefix="/api/v1", tags=["indicators"])
    app.include_router(candles.router, prefix="/api/v1", tags=["candles"])
    app.include_router(
        settings_router.router, prefix="/api/v1", tags=["settings"]
    )
    app.include_router(
        backtest.router, prefix="/api/v1", tags=["backtest"]
    )
    app.include_router(
        trading.router, prefix="/api/v1", tags=["trading"]
    )

    # ヘルスチェック
    @app.get("/api/v1/health", response_model=ApiResponse[HealthResponse])
    async def health_check() -> ApiResponse[HealthResponse]:
        """ヘルスチェック

        Returns:
            ApiResponse[HealthResponse]: ヘルスチェック結果
        """
        uptime = time.time() - _start_time if _start_time else 0.0
        return ApiResponse(
            data=HealthResponse(
                status="ok",
                version="1.0.0",
                uptime=uptime,
            )
        )

    # WebSocketエンドポイント
    @app.websocket("/ws/market/{symbol}")
    async def websocket_market(websocket: WebSocket, symbol: str):
        """市場データWebSocket

        Args:
            websocket: WebSocketインスタンス
            symbol: 通貨ペア
        """
        await handle_market_websocket(websocket, symbol)

    @app.websocket("/ws/signals")
    async def websocket_signals(websocket: WebSocket):
        """シグナルWebSocket

        Args:
            websocket: WebSocketインスタンス
        """
        await handle_signals_websocket(websocket)

    @app.websocket("/ws/dashboard")
    async def websocket_dashboard(websocket: WebSocket):
        """ダッシュボードWebSocket

        Args:
            websocket: WebSocketインスタンス
        """
        await handle_dashboard_websocket(websocket)

    @app.websocket("/ws/backtest")
    async def websocket_backtest(websocket: WebSocket):
        """バックテストWebSocket

        Args:
            websocket: WebSocketインスタンス
        """
        await handle_backtest_websocket(websocket)

    # 静的ファイル配信（フロントエンドビルド後）
    frontend_dist = Path(__file__).resolve().parent / "frontend" / "dist"
    if frontend_dist.exists() and (frontend_dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True))
    else:
        # フロントエンド未ビルド時のフォールバック
        @app.get("/", response_class=HTMLResponse)
        async def root_fallback() -> str:
            """フロントエンド未ビルド時のルート

            Returns:
                str: ビルド案内HTML
            """
            return """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>AutoTrader WebUI</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
        code { background: #f4f4f4; padding: 2px 6px; border-radius: 4px; }
        a { color: #0066cc; }
    </style>
</head>
<body>
    <h1>AutoTrader WebUI</h1>
    <p>フロントエンドがビルドされていません。</p>
    <h2>ビルド方法</h2>
    <pre><code>cd src/autotrader/web/frontend
npm install
npm run build</code></pre>
    <p>または:</p>
    <pre><code>./scripts/start_webui.sh build</code></pre>
    <h2>API</h2>
    <ul>
        <li><a href="/docs">Swagger UI</a></li>
        <li><a href="/api/v1/health">Health Check</a></li>
    </ul>
</body>
</html>"""

    return app


# uvicorn用
app = create_app()
