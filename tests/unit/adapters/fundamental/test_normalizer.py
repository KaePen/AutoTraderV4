"""EconomicEventNormalizer テスト"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)


def make_event(
    currency: str = "USD",
    event_name: str = "NFP",
    impact: ImpactLevel = ImpactLevel.HIGH,
    event_time: datetime | None = None,
    source: EventSource = EventSource.MT5,
) -> EconomicEvent:
    """テスト用イベント生成"""
    if event_time is None:
        event_time = datetime(2026, 2, 21, 14, 30, tzinfo=timezone.utc)
    return EconomicEvent(
        event_id=f"test_{uuid4().hex[:8]}",
        event_time=event_time,
        currency=currency,
        event_name=event_name,
        impact=impact,
        source=source,
        fetched_at=datetime.now(timezone.utc),
    )


class TestEconomicEventNormalizer:
    """EconomicEventNormalizerのテスト"""

    def setup_method(self):
        """テストセットアップ"""
        self.normalizer = EconomicEventNormalizer(
            dedup_window_minutes=5
        )

    def test_normalize_mt5_impact_high(self):
        """MT5インパクト3→HIGHに変換"""
        result = self.normalizer.normalize_mt5_impact(3)
        assert result == ImpactLevel.HIGH

    def test_normalize_mt5_impact_medium(self):
        """MT5インパクト2→MEDIUMに変換"""
        result = self.normalizer.normalize_mt5_impact(2)
        assert result == ImpactLevel.MEDIUM

    def test_normalize_mt5_impact_low(self):
        """MT5インパクト1→LOWに変換"""
        result = self.normalizer.normalize_mt5_impact(1)
        assert result == ImpactLevel.LOW

    def test_normalize_mt5_impact_unknown(self):
        """未知のインパクト値→LOWに変換"""
        result = self.normalizer.normalize_mt5_impact(99)
        assert result == ImpactLevel.LOW

    def test_get_related_symbols_usd(self):
        """USDの関連シンボル取得"""
        symbols = self.normalizer.get_related_symbols("USD")
        assert "USDJPY" in symbols
        assert "EURUSD" in symbols

    def test_filter_by_symbol_usdjpy(self):
        """USDJPYでフィルタリング：USD・JPYイベントのみ残る"""
        events = [
            make_event("USD", "NFP"),
            make_event("JPY", "BOJ"),
            make_event("EUR", "ECB"),
        ]
        result = self.normalizer.filter_by_symbol(events, "USDJPY")
        currencies = {ev.currency for ev in result}
        assert "USD" in currencies
        assert "JPY" in currencies
        assert "EUR" not in currencies

    def test_filter_by_impact_high_only(self):
        """HIGHインパクトのみフィルタリング"""
        events = [
            make_event(impact=ImpactLevel.HIGH),
            make_event(impact=ImpactLevel.MEDIUM),
            make_event(impact=ImpactLevel.LOW),
        ]
        result = self.normalizer.filter_by_impact(
            events, ImpactLevel.HIGH
        )
        assert len(result) == 1
        assert result[0].impact == ImpactLevel.HIGH

    def test_filter_by_impact_medium_and_above(self):
        """MEDIUMインパクト以上をフィルタリング"""
        events = [
            make_event(impact=ImpactLevel.HIGH),
            make_event(impact=ImpactLevel.MEDIUM),
            make_event(impact=ImpactLevel.LOW),
        ]
        result = self.normalizer.filter_by_impact(
            events, ImpactLevel.MEDIUM
        )
        assert len(result) == 2

    def test_deduplicate_same_events(self):
        """同一イベントの重複排除"""
        base_time = datetime(2026, 2, 21, 14, 30, tzinfo=timezone.utc)
        events = [
            make_event(
                currency="USD", event_name="NFP",
                event_time=base_time, source=EventSource.MT5
            ),
            make_event(
                currency="USD", event_name="NFP",
                event_time=base_time + timedelta(minutes=1),
                source=EventSource.FOREX_FACTORY
            ),
        ]
        result = self.normalizer.deduplicate(events)
        assert len(result) == 1
        # MT5を優先
        assert result[0].source == EventSource.MT5

    def test_deduplicate_different_currencies(self):
        """異なる通貨のイベントは重複と判定しない"""
        base_time = datetime(2026, 2, 21, 14, 30, tzinfo=timezone.utc)
        events = [
            make_event(currency="USD", event_name="Rate"),
            make_event(currency="EUR", event_name="Rate"),
        ]
        result = self.normalizer.deduplicate(events)
        assert len(result) == 2

    def test_get_upcoming_events(self):
        """直近イベント取得（60分以内）"""
        now = datetime(2026, 2, 21, 14, 0, tzinfo=timezone.utc)
        events = [
            make_event(  # 30分後
                event_time=now + timedelta(minutes=30)
            ),
            make_event(  # 90分後（範囲外）
                event_time=now + timedelta(minutes=90)
            ),
            make_event(  # 過去（範囲外）
                event_time=now - timedelta(minutes=10)
            ),
        ]
        result = self.normalizer.get_upcoming_events(
            events, now, window_minutes=60
        )
        assert len(result) == 1

    def test_event_minutes_until(self):
        """minutes_until計算テスト"""
        now = datetime(2026, 2, 21, 14, 0, tzinfo=timezone.utc)
        event = make_event(
            event_time=now + timedelta(minutes=25)
        )
        assert abs(event.minutes_until(now) - 25.0) < 0.1

    def test_event_surprise_magnitude(self):
        """surprise_magnitude計算テスト"""
        event = EconomicEvent(
            event_id="test",
            event_time=datetime.now(timezone.utc),
            currency="USD",
            event_name="NFP",
            impact=ImpactLevel.HIGH,
            source=EventSource.MT5,
            fetched_at=datetime.now(timezone.utc),
            actual=200000.0,
            forecast=180000.0,
            previous=170000.0,
        )
        surprise = event.surprise_magnitude
        assert surprise is not None
        assert abs(surprise - (200000.0 - 180000.0) / 180000.0) < 0.001

    def test_event_surprise_magnitude_no_actual(self):
        """実績なしのsurprise_magnitude"""
        event = make_event()
        assert event.surprise_magnitude is None
