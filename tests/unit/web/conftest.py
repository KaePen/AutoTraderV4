"""Web APIテスト用フィクスチャ"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autotrader.core.enums import (
    ConfidenceLevel,
    SignalType,
    Timeframe,
)
from autotrader.web.auth import router as auth_router
from autotrader.web.auth.dependencies import (
    get_current_user,
    require_admin,
)
from autotrader.web.dependencies import (
    get_db,
    get_engine_manager,
    get_live_engine,
)
from autotrader.web.routers import (
    candles,
    dashboard,
    indicators,
    positions,
    signals,
    trading,
    trades,
)
from autotrader.web.routers import settings as settings_router


# テスト用ダミーユーザー
_TEST_ADMIN_USER = {
    "username": "test_admin",
    "role": "admin",
}

_TEST_USER = {
    "username": "test_user",
    "role": "user",
}


def _mock_get_current_user():
    """テスト用 get_current_user モック（管理者）"""
    async def _inner():
        return _TEST_ADMIN_USER
    return _inner


def _mock_require_admin():
    """テスト用 require_admin モック"""
    async def _inner():
        return _TEST_ADMIN_USER
    return _inner


def _create_test_app() -> FastAPI:
    """テスト用FastAPIアプリ（lifespanなし）

    認証依存関係をモックで置き換え、
    テスト時は常に認証済みとして扱う。
    """
    app = FastAPI()
    # 認証ルーター
    app.include_router(
        auth_router.router, prefix="/api/v1"
    )
    app.include_router(
        dashboard.router, prefix="/api/v1"
    )
    app.include_router(
        signals.router, prefix="/api/v1"
    )
    app.include_router(
        positions.router, prefix="/api/v1"
    )
    app.include_router(
        trades.router, prefix="/api/v1"
    )
    app.include_router(
        indicators.router, prefix="/api/v1"
    )
    app.include_router(
        candles.router, prefix="/api/v1"
    )
    app.include_router(
        settings_router.router, prefix="/api/v1"
    )
    app.include_router(
        trading.router, prefix="/api/v1"
    )
    # DBセッションはモックで置き換え
    app.dependency_overrides[get_db] = (
        lambda: MagicMock()
    )
    # EngineManagerはデフォルトNone（後方互換テスト）
    app.dependency_overrides[get_engine_manager] = (
        lambda: None
    )
    # 認証をモックで置き換え（テスト時は常に認証済み）
    app.dependency_overrides[get_current_user] = (
        _mock_get_current_user()
    )
    app.dependency_overrides[require_admin] = (
        _mock_require_admin()
    )
    return app


def _make_mock_engine(
    connected: bool = True,
    running: bool = True,
) -> MagicMock:
    """モックエンジンを生成"""
    engine = MagicMock()
    engine.connected = connected
    engine.running = running
    engine.enable_auto_trade = False
    engine.demo_mode_enabled = False
    engine.symbol_auto_trade_states = {}
    engine.symbol_demo_mode_states = {}
    engine._config = SimpleNamespace(symbol="USDJPY")
    engine.last_analysis = None
    engine.last_tick_time = None
    engine.signal_history = []
    engine.cached_positions = []
    engine.active_symbol = "USDJPY"
    engine.set_symbol_auto_trade = AsyncMock()
    engine.change_symbol = AsyncMock()
    engine.sync_positions_on_toggle = AsyncMock()
    engine.account_info = SimpleNamespace(
        balance=1000000.0,
        equity=1000000.0,
        margin=0.0,
        free_margin=1000000.0,
        margin_level=0.0,
        profit=0.0,
        login=12345,
        server="TestServer",
        name="TestAccount",
        currency="JPY",
        leverage=25,
    )
    return engine


def _make_signal():
    """テスト用Signalオブジェクト"""
    return SimpleNamespace(
        signal_id="sig-001",
        symbol="USDJPY",
        timeframe=Timeframe.M15,
        signal_type=SignalType.BUY,
        confidence=0.85,
        confidence_level=ConfidenceLevel.HIGH,
        stop_loss=149.0,
        take_profit=151.0,
        reasoning="テスト",
        created_at=datetime(
            2026, 1, 1, tzinfo=timezone.utc
        ),
        indicators_snapshot={"rsi": 55.0},
        regime="TREND",
        mode="UNIVERSAL",
        consensus_score=7.5,
        lot=0.1,
    )


@pytest.fixture()
def mock_engine():
    """接続済みモックエンジン"""
    return _make_mock_engine()


@pytest.fixture()
def app(mock_engine):
    """テスト用FastAPIアプリ"""
    test_app = _create_test_app()

    test_app.dependency_overrides[get_live_engine] = (
        lambda: mock_engine
    )
    return test_app


@pytest.fixture()
def client(app):
    """テスト用HTTPクライアント"""
    return TestClient(app)


@pytest.fixture()
def no_engine_app():
    """エンジンなしアプリ"""
    test_app = _create_test_app()

    test_app.dependency_overrides[get_live_engine] = (
        lambda: None
    )
    return test_app


@pytest.fixture()
def no_engine_client(no_engine_app):
    """エンジンなしクライアント"""
    return TestClient(no_engine_app)


@pytest.fixture()
def temp_auth_file(tmp_path) -> Generator[Path, None, None]:
    """テスト用一時auth.yamlファイル

    テスト終了後に自動削除される。
    """
    auth_file = tmp_path / "auth.yaml"
    yield auth_file
    # クリーンアップは tmp_path が自動で行う


@pytest.fixture()
def auth_disabled_env(monkeypatch):
    """認証無効化環境変数を設定"""
    monkeypatch.setenv("AUTOTRADER_AUTH_AUTH_DISABLED", "true")
    yield
    # monkeypatch は自動でクリーンアップ
