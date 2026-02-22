"""ForexFactoryスクレイパー

ForexFactoryの経済カレンダーをHTMLスクレイピングで取得。
レートリミット: 1日1〜2回のみ呼び出す。

依存: pip install curl-cffi beautifulsoup4 lxml tzdata
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)

# ForexFactoryのタイムゾーン（EST/EDT 自動切替）
_EASTERN_TZ = ZoneInfo("America/New_York")

# ForexFactory URL
_FF_URL = "https://www.forexfactory.com/calendar"
_FF_HOME = "https://www.forexfactory.com/"

# リクエストヘッダー（ブラウザ偽装）
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/110.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


class ForexFactoryClient:
    """ForexFactoryスクレイパー

    curl-cffi と BeautifulSoup4 を使用してForexFactoryの経済カレンダーを取得。
    タイムゾーンは America/New_York（EST/EDT 自動切替）で処理。
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
            hours_left = int(remaining.total_seconds() // 3600)
            logger.info(
                f"[ForexFactory] レートリミット中。"
                f"あと{hours_left}時間後に再取得可能"
            )
            return []

        try:
            from curl_cffi import requests as cffi_requests
            from bs4 import BeautifulSoup  # noqa: F811
        except ImportError:
            logger.warning(
                "[ForexFactory] curl-cffi/beautifulsoup4未インストール。"
                "スキップします: pip install curl-cffi beautifulsoup4 lxml"
            )
            return []

        fetched_at = datetime.now(timezone.utc)
        try:
            with cffi_requests.Session(
                impersonate="chrome110"
            ) as session:
                resp = session.get(
                    _FF_URL,
                    headers=_HEADERS,
                    timeout=self._timeout,
                )
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
        year: int | None = None,
    ) -> list[EconomicEvent]:
        """HTMLをパースして経済イベントリストを生成

        Args:
            html: ForexFactory HTMLコンテンツ
            fetched_at: 取得時刻
            currencies: フィルタリング対象通貨
            year: 対象年（Noneの場合は現在年を使用）

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
                # 日付セルを確認（存在する場合はcurrent_dateを更新）
                # ※同一行にイベントが含まれる場合があるため continue しない
                date_cell = row.select_one(
                    "td.calendar__cell.calendar__date"
                )
                if date_cell and date_cell.get_text(strip=True):
                    date_str = date_cell.get_text(strip=True)
                    current_date = self._parse_date(
                        date_str, now, year
                    )

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

                # 時刻（Eastern→UTC変換は _parse_time() 内で実施）
                time_cell = row.select_one(
                    "td.calendar__cell.calendar__time"
                )
                if time_cell:
                    event_time = self._parse_time(
                        time_cell, current_date
                    )
                else:
                    # 時刻なし: 日付のみ（Eastern midnight → UTC）
                    event_time = current_date.astimezone(timezone.utc)

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

    def _parse_date(
        self,
        date_str: str,
        now: datetime,
        year: int | None = None,
    ) -> datetime:
        """日付文字列をEasternタイムゾーン付きdatetimeに変換

        ForexFactoryは "Mon Jan 1" または "MonJan 1"（スペースなし）
        のいずれかのフォーマットで日付を返す。月名を直接抽出して対応。
        DST（夏時間）は _EASTERN_TZ が自動的に処理する。

        Args:
            date_str: 日付文字列（例: "MonJan 1" or "Mon Jan 1"）
            now: 現在時刻（フォールバック用）
            year: 使用する年（Noneの場合は現在年）

        Returns:
            datetime: Eastern時間のaware datetime（UTC変換前）
        """
        target_year = year if year is not None else now.year
        try:
            # 月名（Jan〜Dec）と日数を直接抽出
            # "MonJan 1"（スペースなし）と "Mon Jan 1" 両方に対応
            match = re.search(
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                r"\s*(\d{1,2})",
                date_str,
                re.IGNORECASE,
            )
            if not match:
                logger.warning(
                    f"[ForexFactory] 日付パース失敗: '{date_str}'"
                )
                return now
            month_str = match.group(1).capitalize()
            day_str = match.group(2)
            # midnight Eastern Time（DST自動適用）
            naive = datetime.strptime(
                f"{month_str} {day_str} {target_year}", "%b %d %Y"
            )
            return naive.replace(tzinfo=_EASTERN_TZ)
        except ValueError:
            logger.warning(
                f"[ForexFactory] 日付パース失敗: '{date_str}'"
            )
            return now

    def _parse_time(
        self, time_cell, current_date: datetime
    ) -> datetime:
        """時刻セルをUTC datetimeに変換

        current_date は Eastern-aware datetime（_parse_date の戻り値）。
        時刻をEasternで設定後、UTCに変換する。

        Args:
            time_cell: BeautifulSoupの時刻セル
            current_date: Eastern-aware基準日付

        Returns:
            datetime: UTC aware datetime
        """
        time_str = time_cell.get_text(strip=True) if time_cell else ""
        if not time_str or time_str.lower() == "all day":
            return current_date.astimezone(timezone.utc)

        try:
            match = re.match(
                r'(\d{1,2}):(\d{2})(am|pm)', time_str.lower()
            )
            if not match:
                return current_date.astimezone(timezone.utc)

            hour = int(match.group(1))
            minute = int(match.group(2))
            ampm = match.group(3)

            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0

            # Eastern時間で時刻を設定し、UTCに変換（DST自動適用）
            eastern_dt = current_date.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            return eastern_dt.astimezone(timezone.utc)
        except (ValueError, AttributeError):
            return current_date.astimezone(timezone.utc)

    def _parse_impact(self, impact_cell) -> ImpactLevel:
        """インパクトセルをImpactLevelに変換

        ForexFactoryのインパクトは内部 span の icon クラスで判定:
        - icon--ff-impact-red  → HIGH（赤）
        - icon--ff-impact-ora  → MEDIUM（オレンジ）
        - icon--ff-impact-yel  → LOW（黄）
        - icon--ff-impact-gra  → LOW（グレー/非経済）

        Args:
            impact_cell: BeautifulSoupのインパクトセル

        Returns:
            ImpactLevel: 影響度レベル
        """
        if not impact_cell:
            return ImpactLevel.LOW

        # span のクラス名からインパクトを判定
        span = impact_cell.find("span")
        if span:
            classes = span.get("class", [])
            for cls in classes:
                cls_lower = cls.lower()
                if "red" in cls_lower:
                    return ImpactLevel.HIGH
                if "ora" in cls_lower:
                    return ImpactLevel.MEDIUM
                if "yel" in cls_lower or "low" in cls_lower:
                    return ImpactLevel.LOW

        # フォールバック: td 自身のクラスを確認（旧フォーマット対応）
        for cls in impact_cell.get("class", []):
            cls_lower = cls.lower()
            if "high" in cls_lower:
                return ImpactLevel.HIGH
            if "medium" in cls_lower or "med" in cls_lower:
                return ImpactLevel.MEDIUM

        return ImpactLevel.LOW

    def fetch_historical_year(
        self,
        year: int,
        currencies: list[str] | None = None,
    ) -> list[EconomicEvent]:
        """指定年の経済イベントを週ごとにスクレイピング

        ?week=jan01.YYYY 形式で全52週を取得。
        各週の間に1.5秒のウェイトを挟みサーバー負荷を軽減する。

        Args:
            year: 対象年
            currencies: 対象通貨リスト（Noneで全通貨）

        Returns:
            list[EconomicEvent]: 年間全イベント（重複排除済み）
        """
        try:
            import time
            from curl_cffi import requests as cffi_requests
        except ImportError:
            logger.warning(
                "[ForexFactory] curl-cffi未インストール。スキップします: "
                "pip install curl-cffi"
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

        # curl-cffiセッション（Chrome110 TLSフィンガープリントでCloudflare回避）
        with cffi_requests.Session(impersonate="chrome110") as session:
            # トップページにアクセスしてCookieを確立
            try:
                init_resp = session.get(
                    _FF_HOME,
                    headers=_HEADERS,
                    timeout=self._timeout,
                )
                logger.debug(
                    f"[ForexFactory] セッション初期化: "
                    f"status={init_resp.status_code}"
                )
                time.sleep(2.0)
            except Exception as e:
                logger.warning(
                    f"[ForexFactory] セッション初期化失敗: {e}"
                )
                return []

            week_headers = {**_HEADERS, "Referer": _FF_URL}

            while current.year == year and week_count < 53:
                mon = month_abbr[current.month - 1]
                day = f"{current.day:02d}"
                week_param = f"{mon}{day}.{year}"
                url = f"{_FF_URL}?week={week_param}"

                try:
                    resp = session.get(
                        url,
                        headers=week_headers,
                        timeout=self._timeout,
                    )
                    resp.raise_for_status()
                    events = self._parse_html(
                        resp.text, fetched_at, currencies, year
                    )

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

                # 次の週へ（1.5秒ウェイト）
                current += timedelta(weeks=1)
                week_count += 1
                time.sleep(1.5)

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
