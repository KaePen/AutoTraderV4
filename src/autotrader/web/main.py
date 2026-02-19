"""FastAPIアプリケーション"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from autotrader.web.config import get_web_settings
from autotrader.web.routers import dashboard, signals, positions, trades
from autotrader.web.routers import indicators, candles
from autotrader.web.routers import settings as settings_router
from autotrader.web.routers import trading
from autotrader.web.schemas import ApiResponse, HealthResponse
from autotrader.web.websocket.handlers import (
    handle_market_websocket,
    handle_signals_websocket,
    handle_dashboard_websocket,
)

# 起動時刻
_start_time: float = 0.0

# パス
_WEB_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"

logger = logging.getLogger(__name__)

# Jinja2テンプレート
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _create_live_engine():
    """LiveTradingEngineを環境変数+YAML設定から生成

    ConfigLoaderでYAML設定を読み込み、環境変数のMT5設定
    とマージしてLiveTradingConfigを構築する。

    Returns:
        LiveTradingEngine: エンジンインスタンス
    """
    from autotrader.adapters.mt5.config import MT5Config
    from autotrader.config.config_loader import ConfigLoader
    from autotrader.live.config import LiveTradingConfig
    from autotrader.live.engine import LiveTradingEngine
    from autotrader.web.services.settings_service import (
        get_settings_service,
    )

    # YAML設定はSettingsServiceから取得（シングルトン）
    svc = get_settings_service()
    bot_config = svc.bot_config
    pm_config = svc.pm_config

    mt5_config = MT5Config(
        login=int(os.environ.get("MT5_LOGIN", "0")),
        password=os.environ.get("MT5_PASSWORD", ""),
        server=os.environ.get("MT5_SERVER", ""),
        terminal_path=os.environ.get(
            "MT5_TERMINAL_PATH", ""
        ),
    )
    live_config = LiveTradingConfig(
        symbol=os.environ.get(
            "AUTOTRADER_SYMBOL", "USDJPY"
        ),
        bot_config=bot_config,
        mt5_config=mt5_config,
        pm_config=pm_config,
        enable_auto_trade=os.environ.get(
            "AUTOTRADER_AUTO_TRADE", ""
        ).lower() in ("1", "true", "yes"),
    )
    return LiveTradingEngine(live_config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーションライフサイクル

    Args:
        app: FastAPIアプリケーション
    """
    global _start_time
    _start_time = time.time()

    # DB初期化
    try:
        from autotrader.adapters.database.connection import (
            init_db,
        )
        from autotrader.config.settings import get_settings
        init_db(get_settings().database_url)
    except Exception as e:
        logger.warning("DB初期化スキップ: %s", e)

    # MT5ライブエンジン初期化（常に作成、自動接続）
    try:
        engine = _create_live_engine()
        app.state.live_engine = engine

        # SettingsServiceにエンジン参照を設定
        from autotrader.web.services.settings_service import (
            get_settings_service,
        )
        svc = get_settings_service()
        svc.set_engine(engine)

        # MT5自動接続
        try:
            await engine.start()
            acct = engine.account_info
            if acct:
                logger.info(
                    "MT5接続成功: balance=%.0f "
                    "equity=%.0f",
                    acct.balance, acct.equity,
                )
            else:
                logger.info("MT5接続成功 (Direct)")
        except Exception as e:
            logger.warning("MT5自動接続失敗: %s", e)
            logger.info(
                "後からAPI経由で接続可能: "
                "POST /api/v1/trading/mt5/connect"
            )
    except Exception as e:
        logger.error("エンジン初期化失敗: %s", e)
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

    # 静的ファイルマウント
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    # ルーター登録
    app.include_router(
        dashboard.router, prefix="/api/v1", tags=["dashboard"]
    )
    app.include_router(
        signals.router, prefix="/api/v1", tags=["signals"]
    )
    app.include_router(
        positions.router, prefix="/api/v1", tags=["positions"]
    )
    app.include_router(
        trades.router, prefix="/api/v1", tags=["trades"]
    )
    app.include_router(
        indicators.router, prefix="/api/v1", tags=["indicators"]
    )
    app.include_router(
        candles.router, prefix="/api/v1", tags=["candles"]
    )
    app.include_router(
        settings_router.router,
        prefix="/api/v1",
        tags=["settings"],
    )
    app.include_router(
        trading.router, prefix="/api/v1", tags=["trading"]
    )

    # ヘルスチェック
    @app.get(
        "/api/v1/health",
        response_model=ApiResponse[HealthResponse],
    )
    async def health_check() -> ApiResponse[HealthResponse]:
        """ヘルスチェック

        Returns:
            ApiResponse[HealthResponse]: ヘルスチェック結果
        """
        uptime = (
            time.time() - _start_time if _start_time else 0.0
        )
        return ApiResponse(
            data=HealthResponse(
                status="ok",
                version="1.0.0",
                uptime=uptime,
            )
        )

    # WebSocketエンドポイント
    @app.websocket("/ws/market/{symbol}")
    async def websocket_market(
        websocket: WebSocket, symbol: str
    ):
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

    # HTMLページルート
    @app.get("/", response_class=HTMLResponse)
    async def page_dashboard(request: Request):
        """ダッシュボードページ

        Args:
            request: リクエストオブジェクト

        Returns:
            HTMLResponse: ダッシュボードHTML
        """
        return templates.TemplateResponse(
            "dashboard.html", {"request": request}
        )

    return app


# uvicorn用
app = create_app()
