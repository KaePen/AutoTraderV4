"""ポジションルーターのテスト"""

from __future__ import annotations

import pytest


class TestGetPositions:
    """GET /api/v1/positions"""

    def test_エンジンキャッシュから取得(
        self, client, mock_engine
    ):
        mock_engine.cached_positions = [
            {
                "position_id": "pos-001",
                "ticket": 100,
                "trade_id": "t-001",
                "symbol": "USDJPY",
                "signal_type": "BUY",
                "volume": 0.1,
                "entry_price": 150.0,
                "current_price": 150.5,
                "stop_loss": 149.0,
                "take_profit": 151.0,
                "opened_at": "2026-01-01T00:00:00+00:00",
                "unrealized_pnl": 500.0,
                "unrealized_pnl_pips": 50.0,
                "signal_id": "sig-001",
                "regime": "TREND",
                "mode": "UNIVERSAL",
                "consensus_score": 7.5,
            },
        ]
        resp = client.get("/api/v1/positions")
        assert resp.status_code == 200
        positions = resp.json()["data"]
        assert len(positions) == 1
        p = positions[0]
        assert p["position_id"] == "pos-001"
        assert p["signal_id"] == "sig-001"
        assert p["regime"] == "TREND"
        assert p["consensus_score"] == 7.5

    def test_シンボルフィルタ(
        self, client, mock_engine
    ):
        mock_engine.cached_positions = [
            {
                "position_id": "p1",
                "symbol": "USDJPY",
                "signal_type": "BUY",
                "volume": 0.1,
                "entry_price": 150.0,
                "opened_at": "2026-01-01T00:00:00+00:00",
            },
            {
                "position_id": "p2",
                "symbol": "EURUSD",
                "signal_type": "SELL",
                "volume": 0.1,
                "entry_price": 1.1,
                "opened_at": "2026-01-01T00:00:00+00:00",
            },
        ]
        resp = client.get(
            "/api/v1/positions?symbol=USDJPY"
        )
        positions = resp.json()["data"]
        assert len(positions) == 1
        assert positions[0]["symbol"] == "USDJPY"

    def test_エンジンなし時はDB参照(
        self, no_engine_client, monkeypatch
    ):
        """エンジン未接続時はMarketServiceにフォールバック"""
        from autotrader.web.services import market_service

        monkeypatch.setattr(
            market_service.MarketService,
            "get_positions",
            lambda self, sym: [],
        )
        resp = no_engine_client.get("/api/v1/positions")
        assert resp.status_code == 200
        assert resp.json()["data"] == []
