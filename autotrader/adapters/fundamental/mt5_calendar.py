"""MT5経済カレンダークライアント

MetaTrader5のcalendar_event_get/calendar_value_get APIを使用して
経済イベントを取得する。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from loguru import logger

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
)
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)


class MT5CalendarClient:
    """MT5経済カレンダークライアント

    MT5のcalendar APIを使用して経済イベントを取得。
    MT5への接続は既存のConnectionオブジェクトを通じて行う。

    Args:
        normalizer: イベント正規化クラス
    """

    def __init__(
        self, normalizer: EconomicEventNormalizer | None = None
    ) -> None:
        """初期化

        Args:
            normalizer: イベント正規化クラス（Noneで自動生成）
        """
        self._normalizer = normalizer or EconomicEventNormalizer()

    def _get_mt5_module(self):
        """MT5モジュールを取得

        Returns:
            module: MetaTrader5モジュール

        Raises:
            ImportError: MT5パッケージ未インストール
        """
        try:
            import MetaTrader5 as mt5
            return mt5
        except ImportError as e:
            raise ImportError(
                "MetaTrader5パッケージが未インストールです"
            ) from e

    def fetch_events(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        currencies: list[str] | None = None,
    ) -> list[EconomicEvent]:
        """経済イベントを同期取得

        Args:
            from_date: 取得開始日時（UTC、Noneで現在-1時間）
            to_date: 取得終了日時（UTC、Noneで現在+7日）
            currencies: 対象通貨リスト（Noneで全通貨）

        Returns:
            list[EconomicEvent]: 取得済みイベントリスト
        """
        now = datetime.now(timezone.utc)
        if from_date is None:
            from_date = now - timedelta(hours=1)
        if to_date is None:
            to_date = now + timedelta(days=7)

        try:
            mt5 = self._get_mt5_module()
        except ImportError:
            logger.warning(
                "[MT5Calendar] MT5未インストール。空リストを返します"
            )
            return []

        events: list[EconomicEvent] = []
        fetched_at = datetime.now(timezone.utc)

        try:
            # calendar_value_getは過去・未来両方取得可能
            values = mt5.calendar_value_get(
                from_date.replace(tzinfo=None),
                to_date.replace(tzinfo=None),
            )
            if values is None:
                logger.warning(
                    "[MT5Calendar] calendar_value_get返値がNone"
                )
                return []

            for val in values:
                try:
                    event = self._convert_value(val, fetched_at)
                    if event is None:
                        continue
                    # 通貨フィルタリング
                    if currencies and event.currency not in currencies:
                        continue
                    events.append(event)
                except Exception as e:
                    logger.debug(
                        f"[MT5Calendar] イベント変換失敗: {e}"
                    )
                    continue

            logger.info(
                f"[MT5Calendar] {len(events)}件のイベントを取得"
                f" ({from_date.date()} 〜 {to_date.date()})"
            )
            return events

        except Exception as e:
            logger.error(f"[MT5Calendar] 取得エラー: {e}")
            return []

    async def fetch_events_async(
        self,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        currencies: list[str] | None = None,
    ) -> list[EconomicEvent]:
        """経済イベントを非同期取得

        MT5 APIは同期のみなのでasyncio.to_threadでラップ。

        Args:
            from_date: 取得開始日時（UTC）
            to_date: 取得終了日時（UTC）
            currencies: 対象通貨リスト

        Returns:
            list[EconomicEvent]: 取得済みイベントリスト
        """
        return await asyncio.to_thread(
            self.fetch_events,
            from_date=from_date,
            to_date=to_date,
            currencies=currencies,
        )

    def _convert_value(
        self, val, fetched_at: datetime
    ) -> EconomicEvent | None:
        """MT5カレンダー値をEconomicEventに変換

        Args:
            val: MT5カレンダー値オブジェクト
            fetched_at: 取得時刻

        Returns:
            EconomicEvent | None: 変換済みイベント（変換不可はNone）
        """
        try:
            # MT5のtimeはUNIXタイムスタンプ
            event_time = datetime.fromtimestamp(
                val.time, tz=timezone.utc
            )
            currency = getattr(val, "currency", None)
            if not currency:
                return None

            impact_int = getattr(val, "importance", 0)
            impact = self._normalizer.normalize_mt5_impact(impact_int)

            # 実績・予測・前回値（0の場合はNoneとして扱う）
            actual = getattr(val, "actual_value", None)
            forecast = getattr(val, "forecast_value", None)
            previous = getattr(val, "prev_value", None)

            event_name = getattr(val, "event_name", "")
            if not event_name:
                event_name = f"MT5_EVENT_{val.id}"

            return EconomicEvent(
                event_id=f"mt5_{val.id}",
                event_time=event_time,
                currency=currency,
                event_name=event_name,
                impact=impact,
                source=EventSource.MT5,
                fetched_at=fetched_at,
                actual=actual if actual else None,
                forecast=forecast if forecast else None,
                previous=previous if previous else None,
            )
        except AttributeError as e:
            logger.debug(f"[MT5Calendar] 属性エラー: {e}")
            return None
