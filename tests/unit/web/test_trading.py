"""トレーディングルーターのテスト"""

from __future__ import annotations

import pytest


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
