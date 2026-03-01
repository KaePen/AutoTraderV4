"""MT5CalendarClient テスト（CSV読み込み方式）"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autotrader.adapters.fundamental.mt5_calendar import (
    MT5CalendarClient,
)
from autotrader.adapters.fundamental.schemas import (
    EventSource,
    ImpactLevel,
)

# テスト用CSVコンテンツ
_CSV_HEADER = (
    "event_id,event_time,currency,event_name,"
    "impact,actual,forecast,previous"
)


def _make_csv(rows: list[str]) -> str:
    """テスト用CSVファイルを作成しパスを返す

    MQL5サービスはFILE_UNICODE（UTF-16LE）で書き出すため、
    テストでもUTF-16で書き出す。
    """
    content = _CSV_HEADER + "\n" + "\n".join(rows)
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-16") as f:
        f.write(content)
    return path


class TestMT5CalendarClient:
    """MT5CalendarClient CSV読み込みのテスト"""

    def test_fetch_events_csv_not_found(self):
        """CSVファイルが存在しない場合は空リスト"""
        client = MT5CalendarClient(
            csv_path="/nonexistent/path/calendar.csv"
        )
        result = client.fetch_events()
        assert result == []

    def test_fetch_events_success(self):
        """CSVからイベントが正常に読み込める"""
        csv_path = _make_csv([
            "12345,2026-02-21T14:30:00Z,USD,"
            "Non-Farm Payroll,high,256.000,180.000,185.000",
        ])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events(
                from_date=datetime(
                    2026, 2, 21, tzinfo=timezone.utc
                ),
                to_date=datetime(
                    2026, 2, 22, tzinfo=timezone.utc
                ),
            )
            assert len(result) == 1
            ev = result[0]
            assert ev.event_id == "mt5_12345"
            assert ev.currency == "USD"
            assert ev.event_name == "Non-Farm Payroll"
            assert ev.impact == ImpactLevel.HIGH
            assert ev.source == EventSource.MT5
            assert ev.actual == pytest.approx(256.0)
            assert ev.forecast == pytest.approx(180.0)
            assert ev.previous == pytest.approx(185.0)
        finally:
            os.unlink(csv_path)

    def test_fetch_events_currency_filter(self):
        """通貨フィルタリングが機能する"""
        csv_path = _make_csv([
            "1,2026-02-21T14:30:00Z,USD,NFP,high,,,",
            "2,2026-02-21T14:30:00Z,EUR,ECB Rate,high,,,",
        ])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events(
                from_date=datetime(
                    2026, 2, 21, tzinfo=timezone.utc
                ),
                to_date=datetime(
                    2026, 2, 22, tzinfo=timezone.utc
                ),
                currencies=["USD"],
            )
            assert len(result) == 1
            assert result[0].currency == "USD"
        finally:
            os.unlink(csv_path)

    def test_fetch_events_date_filter(self):
        """日付フィルタリングが機能する"""
        csv_path = _make_csv([
            "1,2026-02-20T10:00:00Z,USD,Old Event,low,,,",
            "2,2026-02-21T14:30:00Z,USD,Target Event,high,,,",
            "3,2026-02-25T10:00:00Z,USD,Future Event,low,,,",
        ])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events(
                from_date=datetime(
                    2026, 2, 21, tzinfo=timezone.utc
                ),
                to_date=datetime(
                    2026, 2, 22, tzinfo=timezone.utc
                ),
            )
            assert len(result) == 1
            assert result[0].event_name == "Target Event"
        finally:
            os.unlink(csv_path)

    def test_fetch_events_empty_values(self):
        """actual/forecast/previousが空の場合はNone"""
        csv_path = _make_csv([
            "100,2026-02-21T14:30:00Z,JPY,"
            "BOJ Rate,medium,,,",
        ])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events(
                from_date=datetime(
                    2026, 2, 21, tzinfo=timezone.utc
                ),
                to_date=datetime(
                    2026, 2, 22, tzinfo=timezone.utc
                ),
            )
            assert len(result) == 1
            ev = result[0]
            assert ev.actual is None
            assert ev.forecast is None
            assert ev.previous is None
            assert ev.impact == ImpactLevel.MEDIUM
        finally:
            os.unlink(csv_path)

    def test_parse_row_missing_currency(self):
        """通貨なしの行はスキップされる"""
        csv_path = _make_csv([
            "1,2026-02-21T14:30:00Z,,No Currency,high,,,",
        ])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events(
                from_date=datetime(
                    2026, 2, 21, tzinfo=timezone.utc
                ),
                to_date=datetime(
                    2026, 2, 22, tzinfo=timezone.utc
                ),
            )
            assert result == []
        finally:
            os.unlink(csv_path)

    def test_parse_row_missing_event_name(self):
        """イベント名なしの行はスキップされる"""
        csv_path = _make_csv([
            "1,2026-02-21T14:30:00Z,USD,,high,,,",
        ])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events(
                from_date=datetime(
                    2026, 2, 21, tzinfo=timezone.utc
                ),
                to_date=datetime(
                    2026, 2, 22, tzinfo=timezone.utc
                ),
            )
            assert result == []
        finally:
            os.unlink(csv_path)

    def test_parse_utc_time_formats(self):
        """複数の時刻フォーマットに対応"""
        client = MT5CalendarClient()
        # ISO 8601 with Z
        dt = client._parse_utc_time("2026-01-15T08:30:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.hour == 8
        assert dt.tzinfo == timezone.utc

        # ISO 8601 without Z
        dt2 = client._parse_utc_time("2026-01-15T08:30:00")
        assert dt2 is not None
        assert dt2.tzinfo == timezone.utc

        # Space-separated
        dt3 = client._parse_utc_time("2026-01-15 08:30:00")
        assert dt3 is not None

        # Invalid
        assert client._parse_utc_time("invalid") is None

    def test_parse_float(self):
        """浮動小数点パース"""
        assert MT5CalendarClient._parse_float("1.5") == 1.5
        assert MT5CalendarClient._parse_float("") is None
        assert MT5CalendarClient._parse_float("  ") is None
        assert MT5CalendarClient._parse_float("abc") is None
        assert MT5CalendarClient._parse_float(
            "-3.14"
        ) == pytest.approx(-3.14)

    def test_csv_with_quoted_event_name(self):
        """カンマ含むイベント名（引用符付き）"""
        csv_path = _make_csv([
            '1,2026-02-21T14:30:00Z,USD,'
            '"CPI, Core",high,3.200,3.100,3.000',
        ])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events(
                from_date=datetime(
                    2026, 2, 21, tzinfo=timezone.utc
                ),
                to_date=datetime(
                    2026, 2, 22, tzinfo=timezone.utc
                ),
            )
            assert len(result) == 1
            assert result[0].event_name == "CPI, Core"
        finally:
            os.unlink(csv_path)

    def test_resolve_csv_path_override(self):
        """csv_pathパラメータが優先される"""
        client = MT5CalendarClient(
            csv_path="/custom/path.csv"
        )
        assert client._resolve_csv_path() == Path(
            "/custom/path.csv"
        )

    def test_resolve_csv_path_env_fallback(self, monkeypatch):
        """環境変数MT5_DATA_PATHフォールバック"""
        monkeypatch.setenv(
            "MT5_DATA_PATH", "/mt5/data"
        )
        client = MT5CalendarClient()
        path = client._resolve_csv_path()
        assert path is not None
        assert "calendar_events.csv" in str(path)
        assert "mt5" in str(path).lower()

    def test_fetch_events_empty_csv(self):
        """ヘッダーのみCSV（データ行なし）は空リスト"""
        csv_path = _make_csv([])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events()
            assert result == []
        finally:
            os.unlink(csv_path)

    def test_unknown_impact_defaults_to_low(self):
        """不明なインパクト値はLOWにフォールバック"""
        csv_path = _make_csv([
            "1,2026-02-21T14:30:00Z,USD,Test,critical,,,",
        ])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events(
                from_date=datetime(
                    2026, 2, 21, tzinfo=timezone.utc
                ),
                to_date=datetime(
                    2026, 2, 22, tzinfo=timezone.utc
                ),
            )
            assert len(result) == 1
            assert result[0].impact == ImpactLevel.LOW
        finally:
            os.unlink(csv_path)

    def test_invalid_time_row_skipped(self):
        """不正な時刻の行はスキップ、他は正常取得"""
        csv_path = _make_csv([
            "1,INVALID_TIME,USD,Bad Event,high,,,",
            "2,2026-02-21T14:30:00Z,USD,Good Event,high,,,",
        ])
        try:
            client = MT5CalendarClient(csv_path=csv_path)
            result = client.fetch_events(
                from_date=datetime(
                    2026, 2, 21, tzinfo=timezone.utc
                ),
                to_date=datetime(
                    2026, 2, 22, tzinfo=timezone.utc
                ),
            )
            assert len(result) == 1
            assert result[0].event_name == "Good Event"
        finally:
            os.unlink(csv_path)

    def test_no_path_no_env_returns_empty(self, monkeypatch):
        """CSVパス解決不可時は空リスト"""
        monkeypatch.delenv("MT5_DATA_PATH", raising=False)
        client = MT5CalendarClient()
        # MT5未インストール環境ではNoneが返る
        result = client.fetch_events()
        assert result == []
