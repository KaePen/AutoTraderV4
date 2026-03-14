"""exchange_calendarsを使った市場休日プロバイダー

ForexFactoryスクレイピングの代替として、
exchange_calendarsライブラリから信頼性の高い休日データを提供する。
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import exchange_calendars as xcals

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)

logger = logging.getLogger(__name__)

# FX市場の休日判定に使用するカレンダー
# NYSE: 元旦・クリスマス・Good Friday・Thanksgiving等を網羅
_CALENDAR_NAME = "NYSE"


class ExchangeCalendarHolidayProvider:
    """exchange_calendarsベースの市場休日プロバイダー

    NYSEカレンダーを基準に、FX市場が実質的に閉まる
    主要祝日（元旦・クリスマス・Good Friday等）を検出する。

    Args:
        calendar_name: 使用するカレンダー名（デフォルト: NYSE）
    """

    def __init__(
        self, calendar_name: str = _CALENDAR_NAME
    ) -> None:
        """初期化

        Args:
            calendar_name: exchange_calendarsのカレンダー名
        """
        self._calendar = xcals.get_calendar(calendar_name)
        self._calendar_name = calendar_name

    def get_holiday_events(
        self,
        start: datetime,
        end: datetime,
    ) -> list[EconomicEvent]:
        """指定期間の休日をEconomicEventリストとして返す

        Args:
            start: 取得開始日時（UTC）
            end: 取得終了日時（UTC）

        Returns:
            list[EconomicEvent]: 休日イベントリスト
        """
        events: list[EconomicEvent] = []
        current = start.date()
        end_date = end.date()

        while current <= end_date:
            # 土日は曜日チェックで別途除外されるので平日のみ判定
            if current.weekday() < 5:
                if not self._calendar.is_session(
                    current.strftime("%Y-%m-%d")
                ):
                    event_dt = datetime(
                        current.year,
                        current.month,
                        current.day,
                        0, 0, 0,
                        tzinfo=UTC,
                    )
                    events.append(
                        EconomicEvent(
                            event_id=f"holiday_{current.isoformat()}",
                            event_time=event_dt,
                            currency="ALL",
                            event_name="Market Holiday",
                            impact=ImpactLevel.HIGH,
                            source=EventSource.FOREX_FACTORY,
                            fetched_at=datetime.now(UTC),
                            is_holiday=True,
                        )
                    )
                    logger.debug(
                        "[ExchangeCalendar] 休日検出: %s",
                        current,
                    )
            current += timedelta(days=1)

        return events

    def is_holiday(self, dt: datetime) -> bool:
        """指定日時が市場休日かどうかを返す

        Args:
            dt: 判定する日時（UTC）

        Returns:
            bool: 市場休日であれば True
        """
        # 土日は曜日チェック側（HardGuard）で処理
        if dt.weekday() >= 5:
            return False
        return not self._calendar.is_session(
            dt.strftime("%Y-%m-%d")
        )
