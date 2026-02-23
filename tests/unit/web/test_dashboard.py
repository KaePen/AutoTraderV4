"""ダッシュボードルーターのテスト"""

from __future__ import annotations

import pytest


class TestGetDashboard:
    """GET /api/v1/dashboard"""

    def test_エンジン接続時のダッシュボード(
        self, client, mock_engine, monkeypatch
    ):
        from autotrader.web.services import market_service
        from autotrader.web.schemas.responses import (
            DashboardResponse,
            AccountInfoResponse,
        )

        def _mock_get_dashboard(self, account_override=None):
            acct = account_override or AccountInfoResponse(
                balance=0, equity=0
            )
            return DashboardResponse(
                account=acct,
                daily_pnl=1000.0,
                open_positions=2,
            )

        monkeypatch.setattr(
            market_service.MarketService,
            "get_dashboard",
            _mock_get_dashboard,
        )
        resp = client.get("/api/v1/dashboard")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["account"]["balance"] == 1000000.0
        assert data["daily_pnl"] == 1000.0
        assert data["open_positions"] == 2

    def test_エンジンなし時(
        self, no_engine_client, monkeypatch
    ):
        from autotrader.web.services import market_service
        from autotrader.web.schemas.responses import (
            DashboardResponse,
            AccountInfoResponse,
        )

        def _mock_get_dashboard(self, account_override=None):
            return DashboardResponse(
                account=AccountInfoResponse(
                    balance=0, equity=0
                ),
            )

        monkeypatch.setattr(
            market_service.MarketService,
            "get_dashboard",
            _mock_get_dashboard,
        )
        resp = no_engine_client.get("/api/v1/dashboard")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["account"]["balance"] == 0
