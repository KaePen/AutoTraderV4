"""シグナルルーターのテスト"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autotrader.web.dependencies import get_engine_manager
from tests.unit.web.conftest import (
    _make_mock_engine,
    _make_signal,
)


class TestGetAnalysis:
    """GET /api/v1/signals/analysis"""

    def test_分析データなし時(self, client):
        resp = client.get("/api/v1/signals/analysis")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["direction"] == "HOLD"
        assert data["engine_running"] is True

    def test_分析データあり(self, client, mock_engine):
        mock_engine.last_analysis = SimpleNamespace(
            direction=SimpleNamespace(value="BUY"),
            confidence=0.85,
            consensus_score=7.5,
            entry_threshold=6.0,
            regime="TREND",
            mode="trend",
            rationale="テスト理由",
            buy_score=7.5,
            sell_score=2.0,
            htf_alignment=0.8,
            penalty_total=0.0,
            penalty_breakdown={},
            trend_strength=0.7,
            aligned_tfs=["M15", "H1"],
            scores={"M15": 7.0, "H1": 8.0},
            tf_score_breakdowns={
                "M15": {"rsi": 3.0},
            },
            tf_directions={"M15": "BUY"},
        )
        mock_engine.get_current_entry_threshold = (
            lambda: 6.5
        )
        resp = client.get("/api/v1/signals/analysis")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["direction"] == "BUY"
        assert data["consensus_score"] == 7.5
        assert data["entry_threshold"] == 6.5

    def test_エンジンなし時(self, no_engine_client):
        resp = no_engine_client.get(
            "/api/v1/signals/analysis"
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["rationale"] == "エンジン停止中"

    def test_シンボル一致時(self, client, mock_engine):
        """エンジンのシンボルと一致するクエリは通常レスポンス"""
        resp = client.get(
            "/api/v1/signals/analysis?symbol=USDJPY"
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["symbol"] == "USDJPY"
        assert data["engine_running"] is True

    def test_シンボル不一致時_mgr無し(self, client):
        """EngineManagerなしでシンボル不一致の場合"""
        resp = client.get(
            "/api/v1/signals/analysis?symbol=EURUSD"
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["symbol"] == "EURUSD"
        assert data["engine_running"] is False
        assert "MT5" in data["rationale"]

    def test_シンボル不一致時_mgr有り_エンジン自動作成(
        self, app, mock_engine,
    ):
        """EngineManagerありでシンボル不一致→エンジン自動作成"""
        # EURUSD用エンジンのモック
        eurusd_engine = _make_mock_engine()
        eurusd_engine._config = SimpleNamespace(
            symbol="EURUSD",
        )

        mock_mgr = MagicMock()
        mock_mgr.connected = True
        # 初回はNone（未登録）、add_symbol後はEURUSDエンジン
        mock_mgr.get_engine = MagicMock(
            side_effect=[None, eurusd_engine],
        )
        mock_mgr.add_symbol = AsyncMock(
            return_value=eurusd_engine,
        )

        app.dependency_overrides[
            get_engine_manager
        ] = lambda: mock_mgr

        from fastapi.testclient import TestClient
        client = TestClient(app)

        with patch(
            "autotrader.web.main.build_engine_config",
        ) as mock_build:
            mock_build.return_value = MagicMock()
            resp = client.get(
                "/api/v1/signals/analysis?symbol=EURUSD"
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["symbol"] == "EURUSD"
        # 自動作成でエンジンが見つかる
        assert data["engine_running"] is True

    def test_レスポンスにsymbol含まれる(
        self, client, mock_engine
    ):
        """分析データありの場合にsymbolフィールドが返る"""
        mock_engine.last_analysis = SimpleNamespace(
            direction=SimpleNamespace(value="BUY"),
            confidence=0.85,
            consensus_score=7.5,
            entry_threshold=6.0,
            regime="TREND",
            mode="trend",
            rationale="テスト理由",
            buy_score=7.5,
            sell_score=2.0,
            htf_alignment=0.8,
            penalty_total=0.0,
            penalty_breakdown={},
            trend_strength=0.7,
            aligned_tfs=["M15"],
            scores={"M15": 7.0},
            tf_score_breakdowns={"M15": {"rsi": 3.0}},
            tf_directions={"M15": "BUY"},
        )
        mock_engine.get_current_entry_threshold = (
            lambda: 6.5
        )
        resp = client.get("/api/v1/signals/analysis")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["symbol"] == "USDJPY"
        assert data["direction"] == "BUY"
        assert data["buy_score"] == 7.5
        assert data["sell_score"] == 2.0


class TestGetCurrentSignals:
    """GET /api/v1/signals/current"""

    def test_シグナルあり(self, client, mock_engine):
        mock_engine.signal_history = [_make_signal()]
        resp = client.get(
            "/api/v1/signals/current?symbol=USDJPY"
        )
        assert resp.status_code == 200
        signals = resp.json()["data"]
        assert len(signals) == 1
        s = signals[0]
        assert s["signal_id"] == "sig-001"
        assert s["regime"] == "TREND"
        assert s["mode"] == "UNIVERSAL"
        assert s["consensus_score"] == 7.5
        assert s["lot"] == 0.1

    def test_シグナルなし(self, client):
        resp = client.get(
            "/api/v1/signals/current?symbol=USDJPY"
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestGetSignalHistory:
    """GET /api/v1/signals/history"""

    def test_ページネーション(self, client, mock_engine):
        mock_engine.signal_history = [
            _make_signal() for _ in range(5)
        ]
        resp = client.get(
            "/api/v1/signals/history"
            "?symbol=USDJPY&limit=2&offset=1"
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2
