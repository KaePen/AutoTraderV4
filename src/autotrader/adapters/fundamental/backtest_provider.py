"""バックテスト用ファンダメンタルプロバイダー

CSVファイルから過去の経済イベントを読み込み、
バックテスト時刻に合わせてFundamentalContextを提供する。
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

from loguru import logger

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    FundamentalContext,
    ImpactLevel,
)
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)

# CSVカラム定義
_CSV_COLUMNS = [
    "event_id", "event_time", "currency", "event_name",
    "impact", "actual", "forecast", "previous",
]


class BacktestFundamentalProvider:
    """バックテスト用ファンダメンタルプロバイダー

    MT5の過去データCSVを読み込み、バックテスト時刻に
    合わせてFundamentalContextを提供する。

    使用するCSVフォーマット（`data/fundamental/events_YYYY.csv`）:
    ```
    event_id,event_time,currency,event_name,impact,actual,forecast,previous
    mt5_12345,2024-01-15T08:30:00+00:00,USD,NFP,high,256000,180000,185000
    ```

    Args:
        event_guard_minutes: 重要指標前の取引停止分数
    """

    def __init__(self, event_guard_minutes: int = 30) -> None:
        """初期化

        Args:
            event_guard_minutes: 重要指標前の取引停止分数
        """
        self._guard_minutes = event_guard_minutes
        self._events: list[EconomicEvent] = []
        self._normalizer = EconomicEventNormalizer()
        self._loaded_files: list[str] = []

    def load_csv(self, csv_path: str | Path) -> int:
        """CSVファイルから経済イベントを読み込み

        Args:
            csv_path: CSVファイルパス

        Returns:
            int: 読み込んだイベント数
        """
        path = Path(csv_path)
        if not path.exists():
            logger.warning(
                f"[BacktestFundamental] CSVが見つかりません: {path}"
            )
            return 0

        loaded: list[EconomicEvent] = []
        fetched_at = datetime.now(timezone.utc)

        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        event = self._parse_row(row, fetched_at)
                        if event:
                            loaded.append(event)
                    except Exception as e:
                        logger.debug(
                            f"[BacktestFundamental] 行スキップ: {e}"
                        )
                        continue

            # 重複排除してマージ
            self._events.extend(loaded)
            self._events = self._normalizer.deduplicate(
                self._events
            )
            self._loaded_files.append(str(path))

            logger.info(
                f"[BacktestFundamental] {len(loaded)}件読込: "
                f"{path.name}"
            )
            return len(loaded)

        except Exception as e:
            logger.error(
                f"[BacktestFundamental] CSV読込エラー: {e}"
            )
            return 0

    def get_context(
        self, current_time: datetime, symbol: str
    ) -> FundamentalContext:
        """指定時刻のファンダメンタルコンテキストを取得

        Args:
            current_time: バックテスト現在時刻（UTC）
            symbol: トレード対象シンボル

        Returns:
            FundamentalContext: ファンダメンタルコンテキスト
        """
        if not self._events:
            return FundamentalContext.neutral()

        # シンボル関連イベントにフィルタリング
        symbol_events = self._normalizer.filter_by_symbol(
            self._events, symbol
        )

        # 直近1時間のイベントを取得
        upcoming = self._normalizer.get_upcoming_events(
            symbol_events, current_time, window_minutes=60
        )

        upcoming_dicts = [
            {
                "name": ev.event_name,
                "minutes_until": ev.minutes_until(current_time),
                "impact": ev.impact.value,
            }
            for ev in upcoming
        ]

        # 30分以内の高インパクト指標チェック
        high_impact_soon = any(
            ev.impact == ImpactLevel.HIGH
            and 0 <= ev.minutes_until(current_time) <= self._guard_minutes
            for ev in upcoming
        )

        return FundamentalContext(
            macro_bias_score=0.0,
            macro_bias_summary="バックテスト（マクロバイアスなし）",
            post_event_bias_score=0.0,
            post_event_summary="バックテスト（指標後バイアスなし）",
            sentiment_score=0.0,
            upcoming_events=upcoming_dicts,
            has_high_impact_within_30min=high_impact_soon,
        )

    def _parse_row(
        self, row: dict, fetched_at: datetime
    ) -> EconomicEvent | None:
        """CSVの1行をEconomicEventに変換

        Args:
            row: CSV行辞書
            fetched_at: 取得時刻

        Returns:
            EconomicEvent | None: 変換済みイベント
        """
        event_time_str = row.get("event_time", "")
        if not event_time_str:
            return None

        try:
            # ISO8601形式でパース
            event_time = datetime.fromisoformat(event_time_str)
            if event_time.tzinfo is None:
                event_time = event_time.replace(
                    tzinfo=timezone.utc
                )
        except ValueError:
            return None

        currency = row.get("currency", "").upper()
        if not currency:
            return None

        event_name = row.get("event_name", "")
        if not event_name:
            return None

        impact_str = row.get("impact", "low").lower()
        impact = {
            "high": ImpactLevel.HIGH,
            "medium": ImpactLevel.MEDIUM,
            "low": ImpactLevel.LOW,
        }.get(impact_str, ImpactLevel.LOW)

        def parse_float(val: str) -> float | None:
            """文字列をfloatに変換"""
            if not val or val.strip() == "":
                return None
            try:
                return float(val)
            except ValueError:
                return None

        return EconomicEvent(
            event_id=row.get("event_id", f"bt_{hash(event_name)}"),
            event_time=event_time,
            currency=currency,
            event_name=event_name,
            impact=impact,
            source=EventSource.MT5,
            fetched_at=fetched_at,
            actual=parse_float(row.get("actual", "")),
            forecast=parse_float(row.get("forecast", "")),
            previous=parse_float(row.get("previous", "")),
        )
