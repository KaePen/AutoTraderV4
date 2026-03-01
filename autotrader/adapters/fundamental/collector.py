"""ファンダメンタルデータ収集スケジューラ

エンジンと独立したasyncioタスクとして動作し、
経済イベントを定期収集してDBに保存する。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from loguru import logger

from autotrader.adapters.fundamental.forex_factory import (
    ForexFactoryClient,
)
from autotrader.adapters.fundamental.mt5_calendar import (
    MT5CalendarClient,
)
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    ImpactLevel,
)


class FundamentalDataCollector:
    """ファンダメンタルデータ収集スケジューラ

    定期的にMT5カレンダーとForexFactoryからデータを収集し、
    メモリキャッシュに保存する。エンジンのメインループとは独立して動作。

    Args:
        fetch_interval_minutes: 取得間隔（分）
        use_mt5_calendar: MT5カレンダーを使用するか
        use_forex_factory: ForexFactoryを使用するか
        use_ff_holidays: FF休日データのみ取得するか
        currencies: 対象通貨リスト
    """

    def __init__(
        self,
        fetch_interval_minutes: int = 60,
        use_mt5_calendar: bool = True,
        use_forex_factory: bool = False,
        use_ff_holidays: bool = True,
        currencies: list[str] | None = None,
        on_update: Callable | None = None,
    ) -> None:
        """初期化

        Args:
            fetch_interval_minutes: 取得間隔（分）
            use_mt5_calendar: MT5カレンダー使用フラグ
            use_forex_factory: ForexFactory使用フラグ
            use_ff_holidays: FF休日のみ取得フラグ
            currencies: 対象通貨リスト
            on_update: 収集完了時コールバック
        """
        self._interval = timedelta(minutes=fetch_interval_minutes)
        self._use_mt5 = use_mt5_calendar
        self._use_ff = use_forex_factory
        self._use_ff_holidays = use_ff_holidays
        self._currencies = currencies or [
            "USD",
            "JPY",
            "EUR",
            "GBP",
            "AUD",
            "CAD",
            "CHF",
            "NZD",
        ]
        self._normalizer = EconomicEventNormalizer()
        self._mt5_client = MT5CalendarClient(self._normalizer)
        self._ff_client = ForexFactoryClient()
        self._running = False
        self._task: asyncio.Task | None = None
        # メモリキャッシュ（DB不要時の参照用）
        self._cached_events: list[EconomicEvent] = []
        self._last_fetch: datetime | None = None
        # 収集完了時コールバック（WebSocket配信等）
        self._on_update = on_update

    async def start(self) -> None:
        """収集タスクを開始"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._collect_loop())
        logger.info("[Collector] ファンダメンタル収集タスク開始")

    async def stop(self) -> None:
        """収集タスクを停止"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[Collector] ファンダメンタル収集タスク停止")

    def get_cached_events(self) -> list[EconomicEvent]:
        """メモリキャッシュのイベントを取得

        Returns:
            list[EconomicEvent]: キャッシュ済みイベントリスト
        """
        return list(self._cached_events)

    async def _collect_loop(self) -> None:
        """アダプティブ収集ループ

        HIGHインパクトイベントの前後は3秒間隔で更新。
        通常時はデフォルト間隔。ForexFactoryは12時間ごと。
        """
        # 起動直後に全ソースから1回収集
        await self._collect_once()

        last_ff_fetch = datetime.now(UTC)
        _FF_INTERVAL = timedelta(hours=12)

        while self._running:
            try:
                # 次のHIGHイベントまでの秒数で間隔を決定
                sec = self._get_seconds_to_next_high()
                if sec <= 300:       # 5分以内
                    interval = 3     # 3秒
                elif sec <= 1800:    # 30分以内
                    interval = 30    # 30秒
                else:
                    interval = self._interval.total_seconds()

                await asyncio.sleep(interval)

                # MT5は毎回取得（ローカルCSV、軽い）
                mt5_events = await self._fetch_mt5_events()

                # FFは12時間ごと
                now = datetime.now(UTC)
                ff_events: list[EconomicEvent] = []
                if now - last_ff_fetch >= _FF_INTERVAL:
                    ff_events = (
                        await self._fetch_ff_events()
                    )
                    last_ff_fetch = now

                # マージ・重複排除・キャッシュ更新
                all_events = mt5_events + ff_events
                if ff_events:
                    all_events = (
                        self._normalizer.deduplicate(
                            all_events,
                        )
                    )
                self._cached_events = all_events
                self._last_fetch = now

                # 3秒モード時はDEBUG、通常時はINFO
                if interval <= 3:
                    logger.debug(
                        "[Collector] 高速更新: "
                        "%d件 (次HIGH: %.0f秒後)",
                        len(all_events), sec,
                    )
                else:
                    logger.info(
                        "[Collector] %d件のイベントを"
                        "キャッシュ更新",
                        len(all_events),
                    )

                # コールバック
                if self._on_update is not None:
                    try:
                        result = self._on_update(
                            all_events,
                        )
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(
                            "[Collector] コールバック"
                            "エラー: %s", e,
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    "[Collector] 収集ループエラー: %s",
                    e,
                )
                await asyncio.sleep(60)

    async def _collect_once(self) -> None:
        """1回の収集処理"""
        events: list[EconomicEvent] = []
        now = datetime.now(UTC)
        from_date = now - timedelta(hours=1)
        to_date = now + timedelta(days=7)

        # MT5カレンダー取得
        if self._use_mt5:
            try:
                mt5_events = await self._mt5_client.fetch_events_async(
                    from_date=from_date,
                    to_date=to_date,
                    currencies=self._currencies,
                )
                events.extend(mt5_events)
                logger.debug(
                    "[Collector] MT5から%d件取得",
                    len(mt5_events),
                )
            except Exception as e:
                logger.error(
                    "[Collector] MT5取得エラー: %s", e,
                )

        # ForexFactory取得（指標フォールバック）
        if self._use_ff and (not events or not self._use_mt5):
            try:
                ff_events = await self._ff_client.fetch_events_async(
                    currencies=self._currencies
                )
                events.extend(ff_events)
                logger.debug(
                    "[Collector] ForexFactoryから"
                    "%d件取得",
                    len(ff_events),
                )
            except Exception as e:
                logger.error(
                    "[Collector] ForexFactory"
                    "取得エラー: %s", e,
                )

        # ForexFactory休日取得（MT5では取れない休日データ）
        if self._use_ff_holidays and not self._use_ff:
            try:
                holiday_events = (
                    await self._ff_client
                    .fetch_holidays_only_async(
                        currencies=self._currencies
                    )
                )
                events.extend(holiday_events)
                if holiday_events:
                    logger.info(
                        "[Collector] FF休日%d件取得",
                        len(holiday_events),
                    )
            except Exception as e:
                logger.debug(
                    "[Collector] FF休日取得エラー: %s",
                    e,
                )

        # 重複排除
        events = self._normalizer.deduplicate(events)

        # メモリキャッシュ更新
        self._cached_events = events
        self._last_fetch = now

        logger.info(
            "[Collector] %d件のイベントをキャッシュ更新",
            len(events),
        )

        # コールバック呼び出し（WebSocket配信等）
        if self._on_update is not None:
            try:
                result = self._on_update(events)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.debug(
                    "[Collector] on_update"
                    "コールバックエラー: %s", e,
                )

    async def _fetch_mt5_events(self) -> list[EconomicEvent]:
        """MT5カレンダーCSVのみ取得（軽量・ローカルI/O）"""
        if not self._use_mt5:
            return []
        now = datetime.now(UTC)
        from_date = now - timedelta(hours=1)
        to_date = now + timedelta(days=7)
        try:
            events = (
                await self._mt5_client.fetch_events_async(
                    from_date=from_date,
                    to_date=to_date,
                    currencies=self._currencies,
                )
            )
            logger.debug(
                "[Collector] MT5から%d件取得",
                len(events),
            )
            return events
        except Exception as e:
            logger.error(
                "[Collector] MT5取得エラー: %s", e,
            )
            return []

    async def _fetch_ff_events(
        self,
    ) -> list[EconomicEvent]:
        """ForexFactory取得（重量・HTTPスクレイピング）"""
        events: list[EconomicEvent] = []
        if self._use_ff:
            try:
                ff_events = (
                    await self._ff_client.fetch_events_async(
                        currencies=self._currencies,
                    )
                )
                events.extend(ff_events)
                logger.debug(
                    "[Collector] ForexFactoryから%d件取得",
                    len(ff_events),
                )
            except Exception as e:
                logger.error(
                    "[Collector] ForexFactory"
                    "取得エラー: %s",
                    e,
                )
        # FF休日取得（MT5では取れない休日データ）
        if self._use_ff_holidays and not self._use_ff:
            try:
                holiday_events = (
                    await self._ff_client
                    .fetch_holidays_only_async(
                        currencies=self._currencies,
                    )
                )
                events.extend(holiday_events)
                if holiday_events:
                    logger.info(
                        "[Collector] FF休日%d件取得",
                        len(holiday_events),
                    )
            except Exception as e:
                logger.debug(
                    "[Collector] FF休日取得エラー: %s",
                    e,
                )
        return events

    def _get_seconds_to_next_high(self) -> float:
        """キャッシュから次のHIGHイベントまでの秒数を算出

        Returns:
            float: 秒数（HIGHなければ float('inf')）
        """
        now = datetime.now(UTC)
        min_sec = float("inf")
        for ev in self._cached_events:
            if ev.impact != ImpactLevel.HIGH:
                continue
            diff = (ev.event_time - now).total_seconds()
            # 未発表で未来 → 発表前カウントダウン
            if ev.actual is None and diff >= 0:
                min_sec = min(min_sec, diff)
            # 発表済みで過去2分以内 → 直後キャッチ
            elif (
                ev.actual is not None
                and -120 <= diff <= 0
            ):
                min_sec = 0
                break
        return min_sec

