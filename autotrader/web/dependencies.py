"""依存性注入モジュール"""

from __future__ import annotations

from functools import lru_cache
from typing import Generator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from autotrader.config.settings import Settings, get_settings
from autotrader.web.config import WebSettings, get_web_settings


@lru_cache
def get_engine():
    """SQLAlchemyエンジンを取得

    Returns:
        Engine: SQLAlchemyエンジン
    """
    settings = get_settings()
    return create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False}
        if "sqlite" in settings.database_url
        else {},
    )


@lru_cache
def get_session_factory():
    """セッションファクトリを取得

    Returns:
        sessionmaker: セッションファクトリ
    """
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    """DBセッションを取得

    Yields:
        Session: DBセッション
    """
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
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
