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

from autotrader.web.auth import router as auth_router
from autotrader.web.config import get_web_settings
from autotrader.web.middleware import (
    HTTPSRedirectMiddleware,
    SecurityHeadersMiddleware,
    configure_rate_limit,
)
from autotrader.web.routers import (
    candles,
    dashboard,
    fundamental,
    indicators,
    positions,
    signals,
    trades,
    trading,
)
from autotrader.web.routers import settings as settings_router
from autotrader.web.schemas import ApiResponse, HealthResponse
from autotrader.web.websocket.handlers import (
    handle_dashboard_websocket,
    handle_market_websocket,
    handle_signals_websocket,
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


def _get_mt5_config():
    """環境変数からMT5設定を構築

    Returns:
        MT5Config: MT5接続設定
    """
    from autotrader.adapters.mt5.config import MT5Config

    hide_window = os.environ.get(
        "MT5_SHOW_WINDOW", ""
    ).lower() not in ("1", "true", "yes")

    return MT5Config(
        login=int(os.environ.get("MT5_LOGIN", "0")),
        password=os.environ.get("MT5_PASSWORD", ""),
        server=os.environ.get("MT5_SERVER", ""),
        terminal_path=os.environ.get("MT5_TERMINAL_PATH", ""),
        hide_window=hide_window,
    )


def build_engine_config(symbol: str) -> object:
    """シンボル用のLiveTradingConfigを構築

    ルーターからも呼べるモジュールレベル関数。

    Args:
        symbol: 通貨ペアシンボル

    Returns:
        LiveTradingConfig: エンジン設定
    """
    from autotrader.live.config import LiveTradingConfig
    from autotrader.web.services.settings_service import (
        get_settings_service,
    )

    svc = get_settings_service()
    bot_config = svc.bot_config
    pm_config = svc.pm_config
    mt5_config = _get_mt5_config()

    return LiveTradingConfig(
        symbol=symbol,
        bot_config=bot_config,
        mt5_config=mt5_config,
        pm_config=pm_config,
        enable_auto_trade=os.environ.get("AUTOTRADER_AUTO_TRADE", "").lower()
        in ("1", "true", "yes"),
    )


def _create_engine_manager():
    """EngineManagerを環境変数+YAML設定から生成

    Returns:
        EngineManager: エンジンマネージャー
    """
    from autotrader.live.engine_manager import (
        EngineManager,
    )

    mt5_config = _get_mt5_config()
    return EngineManager(mt5_config)


def _create_live_engine():
    """LiveTradingEngineを環境変数+YAML設定から生成

    後方互換用。単体テストなど EngineManager を
    使わない場合のフォールバック。

    Returns:
        LiveTradingEngine: エンジンインスタンス
    """
    from autotrader.live.engine import LiveTradingEngine

    config = build_engine_config(os.environ.get("AUTOTRADER_SYMBOL", "USDJPY"))
    return LiveTradingEngine(config)


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
            init_local_db,
        )
        from autotrader.config.settings import get_settings

        db_url = get_settings().database_url
        logger.info(
            "DB初期化: %s", db_url.split("@")[-1] if "@" in db_url else db_url
        )
        init_db(db_url)
        init_local_db()
        logger.info("DB初期化完了")
    except Exception as e:
        logger.error("DB初期化失敗（テーブルが存在しない可能性）: %s", e)
        logger.error("scripts/init_db.py を実行してテーブルを作成してください")

    # EngineManager初期化（マルチシンボル対応）
    try:
        mgr = _create_engine_manager()
        app.state.engine_manager = mgr

        # SettingsServiceにEngineManager参照を設定
        from autotrader.web.services.settings_service import (
            get_settings_service,
        )

        svc = get_settings_service()
        svc.set_engine_manager(mgr)

        # MT5自動接続
        try:
            await mgr.connect()

            # デフォルトシンボルのエンジンを追加
            default_symbol = os.environ.get("AUTOTRADER_SYMBOL", "USDJPY")
            config = build_engine_config(default_symbol)
            engine = await mgr.add_symbol(config)

            # 後方互換: app.state.live_engine も設定
            app.state.live_engine = engine
            svc.set_engine(engine)

            acct = mgr.account_info
            if acct:
                logger.info(
                    "MT5接続成功: balance=%.0f equity=%.0f",
                    acct.balance,
                    acct.equity,
                )
            else:
                logger.info("MT5接続成功 (Direct)")
        except Exception as e:
            logger.warning("MT5自動接続失敗: %s", e)
            logger.info(
                "後からAPI経由で接続可能: POST /api/v1/trading/mt5/connect"
            )
    except Exception as e:
        logger.error("エンジン初期化失敗: %s", e)
        app.state.engine_manager = None
        app.state.live_engine = None

    # EventBus → WebSocketブリッジ登録
    from autotrader.web.websocket.event_bridge import (
        setup_event_bridge,
    )
    setup_event_bridge()
    logger.info("EventBus → WebSocketブリッジ登録完了")

    yield

    # シャットダウン
    mgr = getattr(app.state, "engine_manager", None)
    if mgr:
        await mgr.disconnect()


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

    # CORS設定（LAN内プライベートIPからのアクセスも許可）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=web_settings.cors_origins,
        allow_origin_regex=web_settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # HTTPSリダイレクト（本番環境用）
    app.add_middleware(HTTPSRedirectMiddleware)

    # セキュリティヘッダー
    app.add_middleware(SecurityHeadersMiddleware)

    # Rate Limiting
    configure_rate_limit(app)

    # 静的ファイルマウント
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    # ルーター登録
    app.include_router(auth_router.router, prefix="/api/v1", tags=["auth"])
    app.include_router(dashboard.router, prefix="/api/v1", tags=["dashboard"])
    app.include_router(signals.router, prefix="/api/v1", tags=["signals"])
    app.include_router(positions.router, prefix="/api/v1", tags=["positions"])
    app.include_router(trades.router, prefix="/api/v1", tags=["trades"])
    app.include_router(
        indicators.router, prefix="/api/v1", tags=["indicators"]
    )
    app.include_router(candles.router, prefix="/api/v1", tags=["candles"])
    app.include_router(
        settings_router.router,
        prefix="/api/v1",
        tags=["settings"],
    )
    app.include_router(trading.router, prefix="/api/v1", tags=["trading"])
    app.include_router(
        fundamental.router,
        prefix="/api/v1",
        tags=["fundamental"],
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
