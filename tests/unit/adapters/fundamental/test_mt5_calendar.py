"""MT5CalendarClient テスト（MT5 APIモック）"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from autotrader.adapters.fundamental.mt5_calendar import MT5CalendarClient
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)
from autotrader.adapters.fundamental.schemas import (
    EventSource,
    ImpactLevel,
)


class FakeMT5Value:
    """MT5カレンダー値のモッククラス"""

    def __init__(
        self,
        id: int,
        time: float,
        currency: str,
        event_name: str,
        importance: int,
        actual_value: float | None = None,
        forecast_value: float | None = None,
        prev_value: float | None = None,
    ):
        self.id = id
        self.time = time
        self.currency = currency
        self.event_name = event_name
        self.importance = importance
        self.actual_value = actual_value
        self.forecast_value = forecast_value
        self.prev_value = prev_value


class TestMT5CalendarClient:
    """MT5CalendarClientのテスト"""

    @pytest.fixture
    def client(self):
        return MT5CalendarClient(
            normalizer=EconomicEventNormalizer()
        )

    def test_fetch_events_no_mt5(self, client):
        """MT5未インストール時は空リスト"""
        with patch.object(
            client, "_get_mt5_module",
            side_effect=ImportError("MT5なし")
        ):
            result = client.fetch_events()
        assert result == []

    def test_fetch_events_mt5_none_response(self, client):
        """MT5がNoneを返す場合は空リスト"""
        mock_mt5 = MagicMock()
        mock_mt5.calendar_value_get.return_value = None
        with patch.object(client, "_get_mt5_module", return_value=mock_mt5):
            result = client.fetch_events()
        assert result == []

    def test_fetch_events_success(self, client):
        """MT5からイベントが正常取得できる"""
        base_ts = datetime(2026, 2, 21, 14, 30, tzinfo=timezone.utc)
        fake_val = FakeMT5Value(
            id=12345,
            time=base_ts.timestamp(),
            currency="USD",
            event_name="Non-Farm Payroll",
            importance=3,
            actual_value=256000.0,
            forecast_value=180000.0,
            prev_value=185000.0,
        )
        mock_mt5 = MagicMock()
        mock_mt5.calendar_value_get.return_value = [fake_val]
        with patch.object(client, "_get_mt5_module", return_value=mock_mt5):
            result = client.fetch_events()
        assert len(result) == 1
        ev = result[0]
        assert ev.event_id == "mt5_12345"
        assert ev.currency == "USD"
        assert ev.event_name == "Non-Farm Payroll"
        assert ev.impact == ImpactLevel.HIGH
        assert ev.source == EventSource.MT5
        assert ev.actual == pytest.approx(256000.0)
        assert ev.forecast == pytest.approx(180000.0)

    def test_fetch_events_currency_filter(self, client):
        """通貨フィルタリングが機能する"""
        base_ts = datetime(2026, 2, 21, 14, 30, tzinfo=timezone.utc)
        events = [
            FakeMT5Value(1, base_ts.timestamp(), "USD", "NFP", 3),
            FakeMT5Value(2, base_ts.timestamp(), "EUR", "ECB", 3),
        ]
        mock_mt5 = MagicMock()
        mock_mt5.calendar_value_get.return_value = events
        with patch.object(client, "_get_mt5_module", return_value=mock_mt5):
            result = client.fetch_events(currencies=["USD"])
        assert len(result) == 1
        assert result[0].currency == "USD"

    def test_convert_value_missing_currency(self, client):
        """通貨なしのイベントはNoneを返す"""
        fake_val = MagicMock()
        fake_val.time = datetime.now(timezone.utc).timestamp()
        fake_val.currency = ""
        fake_val.importance = 3
        fake_val.event_name = "test"
        result = client._convert_value(
            fake_val, datetime.now(timezone.utc)
        )
        assert result is None

    def test_convert_value_medium_impact(self, client):
        """MEDIUMインパクトの変換"""
        base_ts = datetime(2026, 2, 21, 14, 30, tzinfo=timezone.utc)
        fake_val = FakeMT5Value(
            id=99, time=base_ts.timestamp(),
            currency="JPY", event_name="BOJ",
            importance=2,
        )
        result = client._convert_value(
            fake_val, datetime.now(timezone.utc)
        )
        assert result is not None
        assert result.impact == ImpactLevel.MEDIUM
