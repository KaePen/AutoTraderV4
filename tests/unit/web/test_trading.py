"""トレーディングルーターのテスト"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autotrader.web.auth.dependencies import (
    get_current_user,
    require_admin,
)
from autotrader.web.dependencies import (
    get_db,
    get_engine_manager,
    get_live_engine,
)
from autotrader.web.routers import trading


# テスト用ダミーユーザー
_TEST_ADMIN_USER = {
    "username": "test_admin",
    "role": "admin",
}


def _mock_get_current_user():
    """テスト用 get_current_user モック"""
    async def _inner():
        return _TEST_ADMIN_USER
    return _inner


def _mock_require_admin():
    """テスト用 require_admin モック"""
    async def _inner():
        return _TEST_ADMIN_USER
    return _inner


def _create_test_app_with_mgr(
    mock_mgr=None, mock_engine=None,
):
    """EngineManager付きテストアプリ"""
    app = FastAPI()
    app.include_router(
        trading.router, prefix="/api/v1"
    )
    app.dependency_overrides[get_db] = (
        lambda: MagicMock()
    )
    app.dependency_overrides[get_engine_manager] = (
        lambda: mock_mgr
    )
    app.dependency_overrides[get_live_engine] = (
        lambda: mock_engine
    )
    # 認証モック
    app.dependency_overrides[get_current_user] = (
        _mock_get_current_user()
    )
    app.dependency_overrides[require_admin] = (
        _mock_require_admin()
    )
    return app


class TestGetTradingMode:
    """GET /api/v1/trading/mode"""

    def test_エンジン接続時のモード取得(self, client):
        resp = client.get("/api/v1/trading/mode")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mode = data["data"]
        assert mode["mode"] == "live"
        assert mode["connected"] is True
        assert mode["engine_running"] is True

    def test_エンジンなし時はoffline(
        self, no_engine_client
    ):
        resp = no_engine_client.get(
            "/api/v1/trading/mode"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["mode"] == "offline"


class TestGetMT5Status:
    """GET /api/v1/trading/mt5/status"""

    def test_接続中の口座情報取得(self, client):
        resp = client.get("/api/v1/trading/mt5/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["connected"] is True
        acct = data["data"]["account"]
        assert acct["balance"] == 1000000.0
        assert acct["login"] == 12345

    def test_エンジンなし時(self, no_engine_client):
        resp = no_engine_client.get(
            "/api/v1/trading/mt5/status"
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["connected"] is False


class TestToggleAutoTrade:
    """POST /api/v1/trading/auto-trade"""

    def test_自動取引ON(self, client, mock_engine):
        resp = client.post(
            "/api/v1/trading/auto-trade?enable=true"
        )
        assert resp.status_code == 200
        assert mock_engine.enable_auto_trade is True
        mock_engine.sync_positions_on_toggle.assert_called_once()

    def test_エンジンなし時はエラー(
        self, no_engine_client
    ):
        resp = no_engine_client.post(
            "/api/v1/trading/auto-trade?enable=true"
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is False


class TestToggleSymbolAutoTrade:
    """POST /api/v1/trading/symbol-auto-trade"""

    def test_シンボル自動取引ON(
        self, client, mock_engine
    ):
        resp = client.post(
            "/api/v1/trading/symbol-auto-trade"
            "?symbol=USDJPY&enable=true"
        )
        assert resp.status_code == 200
        mock_engine.set_symbol_auto_trade.assert_called_once_with(
            "USDJPY", True
        )
        mock_engine.sync_positions_on_toggle.assert_called_once()

    def test_エンジン未起動時は自動起動(
        self, client, mock_engine
    ):
        mock_engine.running = False
        resp = client.post(
            "/api/v1/trading/symbol-auto-trade"
            "?symbol=USDJPY&enable=true"
        )
        assert resp.status_code == 200
        mock_engine.start.assert_called_once()


class TestSwitchSymbol:
    """POST /api/v1/trading/switch-symbol"""

    def test_シンボル切替成功(
        self, client, mock_engine
    ):
        mock_engine.change_symbol = AsyncMock()
        resp = client.post(
            "/api/v1/trading/switch-symbol"
            "?symbol=EURUSD"
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_engine.change_symbol.assert_called_once_with(
            "EURUSD"
        )

    def test_エンジンなし時はエラー(
        self, no_engine_client
    ):
        resp = no_engine_client.post(
            "/api/v1/trading/switch-symbol"
            "?symbol=EURUSD"
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is False


class TestSymbolManagement:
    """シンボル管理APIテスト"""

    def test_シンボル一覧_mgr未設定(
        self, no_engine_client
    ):
        """EngineManagerなし時は空リスト"""
        resp = no_engine_client.get(
            "/api/v1/trading/symbols"
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_シンボル一覧_mgr設定済(self):
        """EngineManagerありで一覧取得"""
        from autotrader.web.dependencies import (
            get_engine_manager,
        )

        mock_mgr = MagicMock()
        mock_mgr.symbols = ["USDJPY", "EURUSD"]

        test_app = _create_test_app_with_mgr(mock_mgr)
        with TestClient(test_app) as c:
            resp = c.get("/api/v1/trading/symbols")
            assert resp.status_code == 200
            assert resp.json()["data"] == [
                "USDJPY", "EURUSD"
            ]

    def test_シンボル追加(self):
        """POST /symbols/add でシンボル追加"""
        mock_mgr = MagicMock()
        mock_mgr.symbols = ["USDJPY", "EURUSD"]
        mock_mgr.add_symbol = AsyncMock()

        test_app = _create_test_app_with_mgr(mock_mgr)
        with TestClient(test_app) as c:
            resp = c.post(
                "/api/v1/trading/symbols/add"
                "?symbol=EURUSD"
            )
            assert resp.status_code == 200
            mock_mgr.add_symbol.assert_called_once()

    def test_シンボル除去(self):
        """POST /symbols/remove でシンボル除去"""
        mock_mgr = MagicMock()
        mock_mgr.symbols = ["USDJPY"]
        mock_mgr.remove_symbol = AsyncMock()

        test_app = _create_test_app_with_mgr(mock_mgr)
        with TestClient(test_app) as c:
            resp = c.post(
                "/api/v1/trading/symbols/remove"
                "?symbol=EURUSD"
            )
            assert resp.status_code == 200
            mock_mgr.remove_symbol.assert_called_once_with(
                "EURUSD"
            )


class TestSymbolAutoTradeMultiEngine:
    """マルチエンジン対応 symbol-auto-trade テスト"""

    def test_mgr経由でエンジン取得(self):
        """EngineManager経由でシンボル別エンジンを取得"""
        mock_engine = MagicMock()
        mock_engine.enable_auto_trade = False
        mock_engine.running = True
        mock_engine.connected = True
        mock_engine.demo_mode_enabled = False
        mock_engine.sync_positions_on_toggle = AsyncMock()
        mock_engine.reset_data_update_timer = MagicMock()

        mock_mgr = MagicMock()
        mock_mgr.engines = {"USDJPY": mock_engine}
        mock_mgr.get_engine.return_value = mock_engine
        mock_mgr.connected = True
        mock_mgr.symbol_auto_trade_states = {
            "USDJPY": True
        }
        mock_mgr.symbol_demo_mode_states = {}

        test_app = _create_test_app_with_mgr(
            mock_mgr, mock_engine
        )
        with TestClient(test_app) as c:
            resp = c.post(
                "/api/v1/trading/symbol-auto-trade"
                "?symbol=USDJPY&enable=true"
            )
            assert resp.status_code == 200
            mock_mgr.get_engine.assert_called_with(
                "USDJPY"
            )


class TestAccountPresets:
    """口座プリセットAPIテスト"""

    def test_プリセット一覧取得(self, client, monkeypatch):
        from autotrader.web.routers import trading

        monkeypatch.setattr(
            trading._accounts_loader,
            "load",
            lambda: [
                {
                    "login": 12345,
                    "server": "Demo",
                    "name": "Test",
                }
            ],
        )
        resp = client.get("/api/v1/trading/accounts")
        assert resp.status_code == 200
        accounts = resp.json()["data"]["accounts"]
        assert len(accounts) == 1
        assert accounts[0]["login"] == 12345
