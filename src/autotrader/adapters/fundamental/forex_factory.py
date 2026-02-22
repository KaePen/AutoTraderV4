"""ForexFactoryスクレイパー

ForexFactoryの経済カレンダーをHTMLスクレイピングで取得。
レートリミット: 1日1〜2回のみ呼び出す。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from typing import Any

from loguru import logger

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)

# インパクト文字列→ImpactLevelマッピング
_IMPACT_MAP: dict[str, ImpactLevel] = {
    "high": ImpactLevel.HIGH,
    "medium": ImpactLevel.MEDIUM,
    "low": ImpactLevel.LOW,
    "holiday": ImpactLevel.LOW,
    "non-economic": ImpactLevel.LOW,
}

# ForexFactory URL
_FF_URL = "https://www.forexfactory.com/calendar"

# リクエストヘッダー（ブロック回避）
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
}


class ForexFactoryClient:
    """ForexFactoryスクレイパー

    BeautifulSoup4を使用してForexFactoryの経済カレンダーを取得。
    1日1〜2回のみ呼び出すこと（サーバー負荷配慮）。

    Args:
        timeout: リクエストタイムアウト（秒）
        rate_limit_hours: 最小呼び出し間隔（時間）
    """

    def __init__(
        self,
        timeout: float = 30.0,
        rate_limit_hours: float = 12.0,
    ) -> None:
        """初期化

        Args:
            timeout: リクエストタイムアウト（秒）
            rate_limit_hours: 最小呼び出し間隔（時間）
        """
        self._timeout = timeout
        self._rate_limit = timedelta(hours=rate_limit_hours)
        self._last_fetch: datetime | None = None

    def _check_rate_limit(self) -> bool:
        """レートリミットチェック

        Returns:
            bool: 取得可能ならTrue
        """
        if self._last_fetch is None:
            return True
        elapsed = datetime.now(timezone.utc) - self._last_fetch
        return elapsed >= self._rate_limit

    def fetch_events(
        self, currencies: list[str] | None = None
    ) -> list[EconomicEvent]:
        """経済イベントを同期取得

        Args:
            currencies: 対象通貨リスト（Noneで全通貨）

        Returns:
            list[EconomicEvent]: 取得済みイベントリスト
        """
        if not self._check_rate_limit():
            remaining = (
                self._last_fetch + self._rate_limit
                - datetime.now(timezone.utc)
            )
            logger.info(
                f"[ForexFactory] レートリミット中。"
                f"あと{remaining.seconds // 3600}時間後に再取得可能"
            )
            return []

        try:
            import httpx
            from bs4 import BeautifulSoup
        except ImportError as e:
            logger.warning(
                "[ForexFactory] httpx/beautifulsoup4未インストール。"
                "スキップします"
            )
            return []

        fetched_at = datetime.now(timezone.utc)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(_FF_URL, headers=_HEADERS)
                resp.raise_for_status()
                html = resp.text

            events = self._parse_html(html, fetched_at, currencies)
            self._last_fetch = fetched_at
            logger.info(
                f"[ForexFactory] {len(events)}件のイベントを取得"
            )
            return events

        except Exception as e:
            logger.error(f"[ForexFactory] 取得エラー: {e}")
            return []

    async def fetch_events_async(
        self, currencies: list[str] | None = None
    ) -> list[EconomicEvent]:
        """経済イベントを非同期取得

        Args:
            currencies: 対象通貨リスト

        Returns:
            list[EconomicEvent]: 取得済みイベントリスト
        """
        return await asyncio.to_thread(
            self.fetch_events, currencies=currencies
        )

    def _parse_html(
        self,
        html: str,
        fetched_at: datetime,
        currencies: list[str] | None,
    ) -> list[EconomicEvent]:
        """HTMLをパースして経済イベントリストを生成

        Args:
            html: ForexFactory HTMLコンテンツ
            fetched_at: 取得時刻
            currencies: フィルタリング対象通貨

        Returns:
            list[EconomicEvent]: パース済みイベントリスト
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return []

        soup = BeautifulSoup(html, "lxml")
        events: list[EconomicEvent] = []

        # カレンダーテーブル行を検索
        rows = soup.select("tr.calendar__row")
        current_date: datetime | None = None
        now = datetime.now(timezone.utc)

        for row in rows:
            try:
                # 日付行かどうか確認
                date_cell = row.select_one("td.calendar__cell.calendar__date")
                if date_cell and date_cell.get_text(strip=True):
                    date_str = date_cell.get_text(strip=True)
                    current_date = self._parse_date(date_str, now)
                    continue

                if current_date is None:
                    continue

                # 通貨
                currency_cell = row.select_one(
                    "td.calendar__cell.calendar__currency"
                )
                if not currency_cell:
                    continue
                currency = currency_cell.get_text(strip=True).upper()
                if not currency:
                    continue

                # 通貨フィルタリング
                if currencies and currency not in currencies:
                    continue

                # イベント名
                event_cell = row.select_one(
                    "td.calendar__cell.calendar__event"
                )
                if not event_cell:
                    continue
                event_name = event_cell.get_text(strip=True)
                if not event_name:
                    continue

                # 時刻
                time_cell = row.select_one(
                    "td.calendar__cell.calendar__time"
                )
                event_time = self._parse_time(
                    time_cell, current_date
                ) if time_cell else current_date

                # インパクト
                impact_cell = row.select_one(
                    "td.calendar__cell.calendar__impact"
                )
                impact = self._parse_impact(impact_cell)

                # 実績・予測・前回値
                actual = self._parse_value(
                    row.select_one(
                        "td.calendar__cell.calendar__actual"
                    )
                )
                forecast = self._parse_value(
                    row.select_one(
                        "td.calendar__cell.calendar__forecast"
                    )
                )
                previous = self._parse_value(
                    row.select_one(
                        "td.calendar__cell.calendar__previous"
                    )
                )

                events.append(EconomicEvent(
                    event_id=f"ff_{uuid4().hex[:8]}",
                    event_time=event_time,
                    currency=currency,
                    event_name=event_name,
                    impact=impact,
                    source=EventSource.FOREX_FACTORY,
                    fetched_at=fetched_at,
                    actual=actual,
                    forecast=forecast,
                    previous=previous,
                ))

            except Exception as e:
                logger.debug(f"[ForexFactory] 行パース失敗: {e}")
                continue

        return events

    def _parse_date(self, date_str: str, now: datetime) -> datetime:
        """日付文字列をdatetimeに変換

        Args:
            date_str: 日付文字列（例: "Mon Feb 21"）
            now: 現在時刻

        Returns:
            datetime: パース済み日付（UTC）
        """
        # ForexFactoryはESTで表示（UTC-5）
        import re
        # 年がない場合は現在年を補完
        current_year = now.year
        try:
            # "Mon Feb 21" → "Feb 21 2026"
            clean = re.sub(r'^[A-Za-z]+\s+', '', date_str)
            parsed = datetime.strptime(
                f"{clean} {current_year}", "%b %d %Y"
            )
            # EST → UTC (UTC+5)
            return parsed.replace(
                tzinfo=timezone.utc
            ) + timedelta(hours=5)
        except ValueError:
            return now

    def _parse_time(
        self, time_cell, current_date: datetime
    ) -> datetime:
        """時刻セルをdatetimeに変換

        Args:
            time_cell: BeautifulSoupの時刻セル
            current_date: 基準日付

        Returns:
            datetime: パース済み日時（UTC）
        """
        import re
        time_str = time_cell.get_text(strip=True) if time_cell else ""
        if not time_str or time_str.lower() == "all day":
            return current_date

        try:
            match = re.match(
                r'(\d{1,2}):(\d{2})(am|pm)', time_str.lower()
            )
            if not match:
                return current_date

            hour = int(match.group(1))
            minute = int(match.group(2))
            ampm = match.group(3)

            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0

            # EST → UTC (+5)
            result = current_date.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            ) + timedelta(hours=5)
            return result
        except (ValueError, AttributeError):
            return current_date

    def _parse_impact(self, impact_cell) -> ImpactLevel:
        """インパクトセルをImpactLevelに変換

        Args:
            impact_cell: BeautifulSoupのインパクトセル

        Returns:
            ImpactLevel: 影響度レベル
        """
        if not impact_cell:
            return ImpactLevel.LOW

        # クラス名からインパクトを判定
        classes = impact_cell.get("class", [])
        for cls in classes:
            cls_lower = cls.lower()
            if "high" in cls_lower:
                return ImpactLevel.HIGH
            if "medium" in cls_lower or "med" in cls_lower:
                return ImpactLevel.MEDIUM
            if "low" in cls_lower:
                return ImpactLevel.LOW

        return ImpactLevel.LOW

    def fetch_historical_year(
        self,
        year: int,
        currencies: list[str] | None = None,
    ) -> list[EconomicEvent]:
        """指定年の経済イベントを週ごとにスクレイピング

        ?week=jan01.YYYY 形式で全52週を取得。
        各週の間に1秒のウェイトを挟みサーバー負荷を軽減する。

        Args:
            year: 対象年
            currencies: 対象通貨リスト（Noneで全通貨）

        Returns:
            list[EconomicEvent]: 年間全イベント（重複排除済み）
        """
        try:
            import httpx
            import time
        except ImportError:
            logger.warning(
                "[ForexFactory] httpx未インストール。スキップします"
            )
            return []

        all_events: list[EconomicEvent] = []
        # UUIDはランダムなので event_time+currency+name で重複排除
        seen_keys: set[tuple[str, str, str]] = set()
        fetched_at = datetime.now(timezone.utc)

        # 1月1日から52週分を生成
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        month_abbr = [
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        ]

        current = start
        week_count = 0

        # httpx.Clientはセッション全体で1つ使い回す（接続再利用）
        with httpx.Client(timeout=self._timeout) as client:
            while current.year == year and week_count < 53:
                mon = month_abbr[current.month - 1]
                day = f"{current.day:02d}"
                week_param = f"{mon}{day}.{year}"
                url = f"{_FF_URL}?week={week_param}"

                try:
                    resp = client.get(url, headers=_HEADERS)
                    resp.raise_for_status()
                    html = resp.text

                    events = self._parse_html(
                        html, fetched_at, currencies
                    )

                    # (event_time, currency, event_name) で重複排除
                    new_count = 0
                    for ev in events:
                        dedup_key = (
                            ev.event_time.isoformat(),
                            ev.currency,
                            ev.event_name,
                        )
                        if dedup_key not in seen_keys:
                            seen_keys.add(dedup_key)
                            all_events.append(ev)
                            new_count += 1

                    logger.debug(
                        f"[ForexFactory] {week_param}: "
                        f"{len(events)}件取得 (新規{new_count}件)"
                    )

                except Exception as e:
                    logger.warning(
                        f"[ForexFactory] {week_param} 取得エラー: {e}"
                    )

                # 次の週へ
                current += timedelta(weeks=1)
                week_count += 1
                time.sleep(1.0)

        # _last_fetch を更新してレートリミットと整合性を保つ
        self._last_fetch = datetime.now(timezone.utc)

        logger.info(
            f"[ForexFactory] {year}年: {len(all_events)}件取得完了"
        )
        return all_events

    def _parse_value(self, cell) -> float | None:
        """数値セルをfloatに変換

        Args:
            cell: BeautifulSoupのセル

        Returns:
            float | None: 変換済み数値（変換不可はNone）
        """
        import re
        if not cell:
            return None
        text = cell.get_text(strip=True)
        if not text:
            return None

        # パーセント・カンマ・単位を除去
        clean = re.sub(r'[%,KBMTkbmt]', '', text)
        clean = clean.strip()

        try:
            return float(clean)
        except ValueError:
            return None
