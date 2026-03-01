"""依存性注入モジュール"""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from autotrader.adapters.database.connection import (
    get_engine,
    get_session_factory,
)
from autotrader.config.settings import Settings, get_settings
from autotrader.web.config import WebSettings, get_web_settings


def get_db() -> Generator[Session, None, None]:
    """DBセッションを取得（commit/rollback付き）

    connection.pyのエンジン・ファクトリを使用し、
    正常終了時にcommit、例外時にrollbackする。

    Yields:
        Session: DBセッション
    """
    settings = get_settings()
    factory = get_session_factory(
        get_engine(settings.database_url)
    )
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_engine_manager(request: Request):
    """EngineManagerを取得

    Args:
        request: FastAPIリクエスト

    Returns:
        EngineManager | None: エンジンマネージャー
    """
    return getattr(
        request.app.state, "engine_manager", None
    )


def get_live_engine(request: Request):
    """LiveTradingEngineを取得（後方互換）

    EngineManager経由で最初のエンジンを返す。
    EngineManagerがなければ直接参照にフォールバック。

    Args:
        request: FastAPIリクエスト

    Returns:
        LiveTradingEngine | None: エンジン
    """
    mgr = getattr(
        request.app.state, "engine_manager", None
    )
    if mgr and mgr.engines:
        return next(iter(mgr.engines.values()))
    return getattr(
        request.app.state, "live_engine", None
    )


def get_app_settings() -> Settings:
    """アプリケーション設定を取得

    Returns:
        Settings: アプリケーション設定
    """
    return get_settings()


def get_web_config() -> WebSettings:
    """Web設定を取得

    Returns:
        WebSettings: Web設定
    """
    return get_web_settings()
