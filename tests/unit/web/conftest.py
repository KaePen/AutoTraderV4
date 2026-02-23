"""Web APIテスト用フィクスチャ"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autotrader.core.enums import (
    ConfidenceLevel,
    SignalType,
    Timeframe,
)
from autotrader.web.dependencies import (
    get_db,
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


def _create_test_app() -> FastAPI:
    """テスト用FastAPIアプリ（lifespanなし）"""
    app = FastAPI()
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
