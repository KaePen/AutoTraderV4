"""ForexFactoryClientのユニットテスト"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

import pytest

from autotrader.adapters.fundamental.forex_factory import (
    ForexFactoryClient,
    _EASTERN_TZ,
)
from autotrader.adapters.fundamental.schemas import ImpactLevel


@pytest.fixture
def client() -> ForexFactoryClient:
    """テスト用クライアント"""
    return ForexFactoryClient(timeout=10.0, rate_limit_hours=12.0)


@pytest.fixture
def now() -> datetime:
    """基準時刻（UTC）"""
    return datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


class TestCheckRateLimit:
    """レートリミットチェック"""

    def test_初回は常に取得可能(self, client: ForexFactoryClient) -> None:
        assert client._check_rate_limit() is True

    def test_レートリミット内は取得不可(
        self, client: ForexFactoryClient
    ) -> None:
        client._last_fetch = datetime.now(timezone.utc)
        assert client._check_rate_limit() is False

    def test_レートリミット超過後は取得可能(
        self, client: ForexFactoryClient
    ) -> None:
        client._last_fetch = datetime.now(timezone.utc) - timedelta(
            hours=13
        )
        assert client._check_rate_limit() is True


class TestParseDate:
    """_parse_date() のテスト"""

    def test_スペースあり形式(
        self, client: ForexFactoryClient, now: datetime
    ) -> None:
        """'Mon Jan 8' → 2024-01-08 Eastern"""
        result = client._parse_date("Mon Jan 8", now, year=2024)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 8
        assert result.tzinfo == _EASTERN_TZ

    def test_スペースなし形式(
        self, client: ForexFactoryClient, now: datetime
    ) -> None:
        """'MonJan 8' → 2024-01-08 Eastern"""
        result = client._parse_date("MonJan 8", now, year=2024)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 8

    def test_年パラメータを使用(
        self, client: ForexFactoryClient, now: datetime
    ) -> None:
        result = client._parse_date("Jan 5", now, year=2022)
        assert result.year == 2022

    def test_年未指定は現在年を使用(
        self, client: ForexFactoryClient, now: datetime
    ) -> None:
        result = client._parse_date("Jan 5", now)
        assert result.year == now.year

    def test_パース失敗はnowを返す(
        self, client: ForexFactoryClient, now: datetime
    ) -> None:
        result = client._parse_date("XYZ", now, year=2024)
        assert result == now

    def test_夏時間期間(
        self, client: ForexFactoryClient, now: datetime
    ) -> None:
        """EDT期間（6月）の日付をEasternで返す"""
        result = client._parse_date("Jun 15", now, year=2024)
        assert result.tzinfo == _EASTERN_TZ
        # UTC変換で正しいオフセット（EDT=UTC-4）
        utc_result = result.astimezone(timezone.utc)
        assert utc_result.hour == 4  # midnight EDT = 04:00 UTC

    def test_冬時間期間(
        self, client: ForexFactoryClient, now: datetime
    ) -> None:
        """EST期間（1月）の日付をEasternで返す"""
        result = client._parse_date("Jan 8", now, year=2024)
        utc_result = result.astimezone(timezone.utc)
        assert utc_result.hour == 5  # midnight EST = 05:00 UTC


class TestParseTime:
    """_parse_time() のテスト"""

    def _make_eastern_date(self, year: int, month: int, day: int) -> datetime:
        """Eastern aware datetimeを作成"""
        return datetime(year, month, day, tzinfo=_EASTERN_TZ)

    def test_am時刻(self, client: ForexFactoryClient) -> None:
        """8:30am EST → 13:30 UTC"""
        cell = MagicMock()
        cell.get_text.return_value = "8:30am"
        base = self._make_eastern_date(2024, 1, 8)  # EST (UTC-5)
        result = client._parse_time(cell, base)
        assert result.hour == 13
        assert result.minute == 30
        assert result.tzinfo == timezone.utc

    def test_pm時刻(self, client: ForexFactoryClient) -> None:
        """2:00pm EST → 19:00 UTC"""
        cell = MagicMock()
        cell.get_text.return_value = "2:00pm"
        base = self._make_eastern_date(2024, 1, 8)  # EST (UTC-5)
        result = client._parse_time(cell, base)
        assert result.hour == 19
        assert result.tzinfo == timezone.utc

    def test_夏時間期間(self, client: ForexFactoryClient) -> None:
        """8:30am EDT → 12:30 UTC（夏時間補正）"""
        cell = MagicMock()
        cell.get_text.return_value = "8:30am"
        base = self._make_eastern_date(2024, 6, 15)  # EDT (UTC-4)
        result = client._parse_time(cell, base)
        assert result.hour == 12  # 8:30 EDT = 12:30 UTC
        assert result.minute == 30

    def test_all_dayはUTC変換のみ(
        self, client: ForexFactoryClient
    ) -> None:
        cell = MagicMock()
        cell.get_text.return_value = "All Day"
        base = self._make_eastern_date(2024, 1, 8)
        result = client._parse_time(cell, base)
        assert result.tzinfo == timezone.utc

    def test_空文字はUTC変換のみ(
        self, client: ForexFactoryClient
    ) -> None:
        cell = MagicMock()
        cell.get_text.return_value = ""
        base = self._make_eastern_date(2024, 1, 8)
        result = client._parse_time(cell, base)
        assert result.tzinfo == timezone.utc

    def test_12pm_noon(self, client: ForexFactoryClient) -> None:
        """12:00pm EST → 17:00 UTC"""
        cell = MagicMock()
        cell.get_text.return_value = "12:00pm"
        base = self._make_eastern_date(2024, 1, 8)
        result = client._parse_time(cell, base)
        assert result.hour == 17

    def test_12am_midnight(self, client: ForexFactoryClient) -> None:
        """12:00am EST → 05:00 UTC"""
        cell = MagicMock()
        cell.get_text.return_value = "12:00am"
        base = self._make_eastern_date(2024, 1, 8)
        result = client._parse_time(cell, base)
        assert result.hour == 5


class TestParseImpact:
    """_parse_impact() のテスト"""

    def _make_impact_cell(self, span_class: str) -> MagicMock:
        """インパクトセルのモックを作成"""
        span = MagicMock()
        span.get.return_value = [span_class]
        cell = MagicMock()
        cell.find.return_value = span
        cell.get.return_value = []
        return cell

    def test_red_はHIGH(self, client: ForexFactoryClient) -> None:
        cell = self._make_impact_cell("icon icon--ff-impact-red")
        assert client._parse_impact(cell) == ImpactLevel.HIGH

    def test_ora_はMEDIUM(self, client: ForexFactoryClient) -> None:
        cell = self._make_impact_cell("icon icon--ff-impact-ora")
        assert client._parse_impact(cell) == ImpactLevel.MEDIUM

    def test_yel_はLOW(self, client: ForexFactoryClient) -> None:
        cell = self._make_impact_cell("icon icon--ff-impact-yel")
        assert client._parse_impact(cell) == ImpactLevel.LOW

    def test_gra_はLOW(self, client: ForexFactoryClient) -> None:
        cell = self._make_impact_cell("icon icon--ff-impact-gra")
        assert client._parse_impact(cell) == ImpactLevel.LOW

    def test_cell_none_はLOW(
        self, client: ForexFactoryClient
    ) -> None:
        assert client._parse_impact(None) == ImpactLevel.LOW

    def test_spanなしはLOW(self, client: ForexFactoryClient) -> None:
        cell = MagicMock()
        cell.find.return_value = None
        cell.get.return_value = []
        assert client._parse_impact(cell) == ImpactLevel.LOW


class TestParseValue:
    """_parse_value() のテスト"""

    def _make_cell(self, text: str) -> MagicMock:
        cell = MagicMock()
        cell.get_text.return_value = text
        return cell

    def test_通常の数値(self, client: ForexFactoryClient) -> None:
        assert client._parse_value(self._make_cell("3.5")) == 3.5

    def test_パーセント除去(self, client: ForexFactoryClient) -> None:
        assert client._parse_value(self._make_cell("2.3%")) == 2.3

    def test_カンマ除去(self, client: ForexFactoryClient) -> None:
        assert client._parse_value(self._make_cell("245,000")) == 245000.0

    def test_K単位除去(self, client: ForexFactoryClient) -> None:
        # Kは除去されるが乗算はしない（ForexFactoryは単位統一）
        assert client._parse_value(self._make_cell("245K")) == 245.0

    def test_空文字はNone(self, client: ForexFactoryClient) -> None:
        assert client._parse_value(self._make_cell("")) is None

    def test_cell_none_はNone(
        self, client: ForexFactoryClient
    ) -> None:
        assert client._parse_value(None) is None

    def test_変換不可文字列はNone(
        self, client: ForexFactoryClient
    ) -> None:
        assert client._parse_value(self._make_cell("N/A")) is None


class TestParseHtml:
    """_parse_html() の統合テスト（HTMLフィクスチャ使用）"""

    _SAMPLE_HTML = """
<html><body>
<table>
<tr class="calendar__row calendar__row--day-breaker"><td></td></tr>
<tr class="calendar__row calendar__row--new-day calendar__row--grey">
  <td class="calendar__cell calendar__date">MonJan 8</td>
  <td class="calendar__cell calendar__currency">USD</td>
  <td class="calendar__cell calendar__event">Core CPI m/m</td>
  <td class="calendar__cell calendar__time">8:30am</td>
  <td class="calendar__cell calendar__impact">
    <span class="icon icon--ff-impact-red"></span>
  </td>
  <td class="calendar__cell calendar__actual">0.3</td>
  <td class="calendar__cell calendar__forecast">0.2</td>
  <td class="calendar__cell calendar__previous">0.3</td>
</tr>
<tr class="calendar__row">
  <td class="calendar__cell calendar__date"></td>
  <td class="calendar__cell calendar__currency">USD</td>
  <td class="calendar__cell calendar__event">CPI m/m</td>
  <td class="calendar__cell calendar__time">8:30am</td>
  <td class="calendar__cell calendar__impact">
    <span class="icon icon--ff-impact-ora"></span>
  </td>
  <td class="calendar__cell calendar__actual">0.3</td>
  <td class="calendar__cell calendar__forecast"></td>
  <td class="calendar__cell calendar__previous">0.1</td>
</tr>
</table>
</body></html>
"""

    def test_イベントを正しく取得(
        self, client: ForexFactoryClient
    ) -> None:
        fetched_at = datetime.now(timezone.utc)
        events = client._parse_html(
            self._SAMPLE_HTML, fetched_at, None, year=2024
        )
        assert len(events) == 2

    def test_最初のイベントが正しい年(
        self, client: ForexFactoryClient
    ) -> None:
        """日付セル同行のイベントが欠落しないことを確認"""
        fetched_at = datetime.now(timezone.utc)
        events = client._parse_html(
            self._SAMPLE_HTML, fetched_at, None, year=2024
        )
        assert events[0].event_time.year == 2024

    def test_インパクトHIGHを検出(
        self, client: ForexFactoryClient
    ) -> None:
        fetched_at = datetime.now(timezone.utc)
        events = client._parse_html(
            self._SAMPLE_HTML, fetched_at, None, year=2024
        )
        assert events[0].impact == ImpactLevel.HIGH

    def test_インパクトMEDIUMを検出(
        self, client: ForexFactoryClient
    ) -> None:
        fetched_at = datetime.now(timezone.utc)
        events = client._parse_html(
            self._SAMPLE_HTML, fetched_at, None, year=2024
        )
        assert events[1].impact == ImpactLevel.MEDIUM

    def test_通貨フィルタリング(
        self, client: ForexFactoryClient
    ) -> None:
        fetched_at = datetime.now(timezone.utc)
        events = client._parse_html(
            self._SAMPLE_HTML, fetched_at, ["JPY"], year=2024
        )
        assert len(events) == 0

    def test_event_timeはUTC(
        self, client: ForexFactoryClient
    ) -> None:
        fetched_at = datetime.now(timezone.utc)
        events = client._parse_html(
            self._SAMPLE_HTML, fetched_at, None, year=2024
        )
        for ev in events:
            assert ev.event_time.tzinfo == timezone.utc

    def test_8時30分ESTは13時30分UTC(
        self, client: ForexFactoryClient
    ) -> None:
        """1月8日 8:30am EST = 13:30 UTC"""
        fetched_at = datetime.now(timezone.utc)
        events = client._parse_html(
            self._SAMPLE_HTML, fetched_at, None, year=2024
        )
        assert events[0].event_time.hour == 13
        assert events[0].event_time.minute == 30
