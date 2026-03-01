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
)


class FundamentalDataCollector:
    """ファンダメンタルデータ収集スケジューラ

    定期的にMT5カレンダーとForexFactoryからデータを収集し、
    DBに保存する。エンジンのメインループとは独立して動作。

    Args:
        session_factory: SQLAlchemyセッションファクトリー
        fetch_interval_minutes: 取得間隔（分）
        use_mt5_calendar: MT5カレンダーを使用するか
        use_forex_factory: ForexFactoryを使用するか
        use_ff_holidays: FF休日データのみ取得するか
        currencies: 対象通貨リスト
    """

    def __init__(
        self,
        session_factory,
        fetch_interval_minutes: int = 60,
        use_mt5_calendar: bool = True,
        use_forex_factory: bool = False,
        use_ff_holidays: bool = True,
        currencies: list[str] | None = None,
        on_update: Callable | None = None,
    ) -> None:
        """初期化

        Args:
            session_factory: SQLAlchemyセッションファクトリー
            fetch_interval_minutes: 取得間隔（分）
            use_mt5_calendar: MT5カレンダー使用フラグ
            use_forex_factory: ForexFactory使用フラグ
            use_ff_holidays: FF休日のみ取得フラグ
            currencies: 対象通貨リスト
            on_update: 収集完了時コールバック
        """
        self._session_factory = session_factory
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
        """収集ループ（バックグラウンドタスク）"""
        # 起動直後に1回収集
        await self._collect_once()

        while self._running:
            try:
                await asyncio.sleep(self._interval.total_seconds())
                await self._collect_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Collector] 収集ループエラー: {e}")
                # エラー後は短めにリトライ
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
                logger.debug(f"[Collector] MT5から{len(mt5_events)}件取得")
            except Exception as e:
                logger.error(f"[Collector] MT5取得エラー: {e}")

        # ForexFactory取得（指標フォールバック）
        if self._use_ff and (not events or not self._use_mt5):
            try:
                ff_events = await self._ff_client.fetch_events_async(
                    currencies=self._currencies
                )
                events.extend(ff_events)
                logger.debug(
                    f"[Collector] ForexFactoryから"
                    f"{len(ff_events)}件取得"
                )
            except Exception as e:
                logger.error(
                    f"[Collector] ForexFactory取得エラー: {e}"
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
                        f"[Collector] FF休日"
                        f"{len(holiday_events)}件取得"
                    )
            except Exception as e:
                logger.debug(
                    f"[Collector] FF休日取得エラー: {e}"
                )

        # 重複排除
        events = self._normalizer.deduplicate(events)

        # メモリキャッシュ更新
        self._cached_events = events
        self._last_fetch = now

        logger.info(f"[Collector] {len(events)}件のイベントをキャッシュ更新")

        # コールバック呼び出し（WebSocket配信等）
        if self._on_update is not None:
            try:
                result = self._on_update(events)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.debug(f"[Collector] on_updateコールバックエラー: {e}")

