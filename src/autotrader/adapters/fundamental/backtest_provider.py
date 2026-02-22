"""バックテスト用ファンダメンタルプロバイダー

CSVファイルから過去の経済イベントを読み込み、
バックテスト時刻に合わせてFundamentalContextを提供する。
"""

from __future__ import annotations

import bisect
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

# シンボル→通貨ペア（先行・後続）のマッピング
_SYMBOL_CURRENCIES: dict[str, tuple[str, str]] = {
    "USDJPY": ("USD", "JPY"),
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
    "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"),
    "EURJPY": ("EUR", "JPY"),
    "GBPJPY": ("GBP", "JPY"),
    "AUDJPY": ("AUD", "JPY"),
    "CADJPY": ("CAD", "JPY"),
    "CHFJPY": ("CHF", "JPY"),
    "EURGBP": ("EUR", "GBP"),
    "GBPCHF": ("GBP", "CHF"),
}

# 高インパクト指標のバイアス乗数
_HIGH_IMPACT_MULTIPLIER = 3.0


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

            # 重複排除してマージし、時刻順ソート（bisect検索用）
            self._events.extend(loaded)
            self._events = self._normalizer.deduplicate(
                self._events
            )
            self._events.sort(key=lambda e: e.event_time)
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

        # 直前24時間の発表済みイベントからマクロバイアスを計算
        released_24h = self._get_released_events(
            symbol_events, current_time, hours=24
        )
        macro_bias, macro_summary = self._estimate_bias_from_events(
            released_24h, symbol
        )

        # 直前4時間の発表済みイベントから指標後バイアスを計算
        released_4h = self._get_released_events(
            symbol_events, current_time, hours=4
        )
        post_bias, post_summary = self._estimate_bias_from_events(
            released_4h, symbol
        )

        return FundamentalContext(
            macro_bias_score=macro_bias,
            macro_bias_summary=macro_summary,
            post_event_bias_score=post_bias,
            post_event_summary=post_summary,
            sentiment_score=0.0,
            upcoming_events=upcoming_dicts,
            has_high_impact_within_30min=high_impact_soon,
        )

    def _get_released_events(
        self,
        events: list[EconomicEvent],
        current_time: datetime,
        hours: int,
    ) -> list[EconomicEvent]:
        """指定時間内の発表済みイベントを取得

        イベントリストは event_time 昇順ソート済みを前提として
        bisect による O(log n) 検索を使用する。

        Args:
            events: 時刻昇順ソート済みイベントリスト
            current_time: 現在時刻（UTC）
            hours: 過去何時間を対象とするか

        Returns:
            list[EconomicEvent]: 発表済みイベントリスト
        """
        if not events:
            return []
        cutoff = current_time - timedelta(hours=hours)
        # bisect で検索範囲を絞る
        times = [ev.event_time for ev in events]
        lo = bisect.bisect_left(times, cutoff)
        hi = bisect.bisect_left(times, current_time)
        return [
            ev for ev in events[lo:hi]
            if ev.actual is not None
        ]

    def _estimate_bias_from_events(
        self,
        released_events: list[EconomicEvent],
        symbol: str,
    ) -> tuple[float, str]:
        """発表済みイベントからバイアスを計算

        surprise_magnitude（実績/予測乖離）から
        symbol の通貨方向バイアスを計算する。

        実績 > 予測 → 発表通貨にポジティブバイアス（先行通貨なら+、後続通貨なら-）
        実績 < 予測 → 発表通貨にネガティブバイアス
        高インパクト指標は乗数3倍で適用。
        バイアスは -1.0〜+1.0 にクリップ。

        Args:
            released_events: 発表済みイベントリスト
            symbol: 対象シンボル

        Returns:
            tuple[float, str]: (bias_score, summary_text)
        """
        if not released_events:
            return 0.0, "発表済み指標なし"

        # シンボル→通貨ペア取得
        sym_upper = symbol.upper()
        base_cur, quote_cur = _SYMBOL_CURRENCIES.get(
            sym_upper, (sym_upper[:3], sym_upper[3:])
        )

        total_bias = 0.0
        event_summaries: list[str] = []

        for ev in released_events:
            if ev.actual is None:
                continue
            if ev.forecast is None:
                # 予測なしの場合は前回値を参照
                if ev.previous is None:
                    continue
                reference = ev.previous
            else:
                reference = ev.forecast

            if reference == 0.0:
                continue

            # サプライズ幅（実績 - 予測）の正規化
            surprise = (ev.actual - reference) / abs(reference)

            # インパクト乗数
            multiplier = (
                _HIGH_IMPACT_MULTIPLIER
                if ev.impact == ImpactLevel.HIGH
                else 1.0
            )

            # 通貨方向でバイアス符号を決定
            if ev.currency == base_cur:
                # 先行通貨の強さ → ペアにとってポジティブ
                bias = surprise * multiplier
            elif ev.currency == quote_cur:
                # 後続通貨の強さ → ペアにとってネガティブ
                bias = -surprise * multiplier
            else:
                continue

            total_bias += bias
            direction = "↑" if bias > 0 else "↓"
            event_summaries.append(
                f"{ev.currency}/{ev.event_name}{direction}"
            )

        # -1.0〜+1.0 にクリップ
        clipped = max(-1.0, min(1.0, total_bias))

        if event_summaries:
            summary = f"バイアス{clipped:+.2f}: " + ", ".join(
                event_summaries[:3]
            )
        else:
            summary = "バイアス計算対象指標なし"

        return clipped, summary

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
