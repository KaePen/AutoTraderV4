"""シグナルルーターのテスト"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.unit.web.conftest import _make_signal


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
            rationale="テスト理由",
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
