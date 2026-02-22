"""経済イベント正規化モジュール

複数ソースからの経済イベントの重複排除・シンボルマッピング。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    ImpactLevel,
)

# 通貨→シンボルマッピング（主要ペア）
_CURRENCY_TO_SYMBOLS: dict[str, list[str]] = {
    "USD": ["USDJPY", "EURUSD", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY"],
    "EUR": ["EURUSD", "EURJPY", "EURGBP", "EURCHF"],
    "GBP": ["GBPUSD", "GBPJPY", "EURGBP"],
    "AUD": ["AUDUSD", "AUDJPY", "AUDNZD"],
    "CAD": ["USDCAD", "CADCHF", "CADJPY"],
    "CHF": ["USDCHF", "EURCHF", "GBPCHF"],
    "NZD": ["NZDUSD", "NZDJPY", "AUDNZD"],
}

# MT5インパクト→ImpactLevelマッピング
_MT5_IMPACT_MAP: dict[int, ImpactLevel] = {
    3: ImpactLevel.HIGH,
    2: ImpactLevel.MEDIUM,
    1: ImpactLevel.LOW,
    0: ImpactLevel.LOW,
}


class EconomicEventNormalizer:
    """経済イベント正規化クラス

    複数ソースからのイベントを統一フォーマットに変換し、
    重複を排除する。

    Args:
        dedup_window_minutes: 重複判定ウィンドウ（分）
    """

    def __init__(self, dedup_window_minutes: int = 5) -> None:
        """初期化

        Args:
            dedup_window_minutes: 重複判定ウィンドウ（分）
        """
        self._dedup_window = timedelta(minutes=dedup_window_minutes)

    def normalize_mt5_impact(self, mt5_impact: int) -> ImpactLevel:
        """MT5インパクト値をImpactLevelに変換

        Args:
            mt5_impact: MT5のインパクト値（0-3）

        Returns:
            ImpactLevel: 影響度レベル
        """
        return _MT5_IMPACT_MAP.get(mt5_impact, ImpactLevel.LOW)

    def map_currency_to_symbol(
        self, currency: str, target_symbol: str
    ) -> bool:
        """通貨がシンボルに関連するか判定

        Args:
            currency: 通貨コード
            target_symbol: 対象シンボル

        Returns:
            bool: 関連していればTrue
        """
        symbols = _CURRENCY_TO_SYMBOLS.get(currency, [])
        return target_symbol in symbols

    def get_related_symbols(self, currency: str) -> list[str]:
        """通貨に関連するシンボルリストを取得

        Args:
            currency: 通貨コード

        Returns:
            list[str]: 関連シンボルリスト
        """
        return _CURRENCY_TO_SYMBOLS.get(currency, [])

    def filter_by_symbol(
        self, events: list[EconomicEvent], symbol: str
    ) -> list[EconomicEvent]:
        """シンボルに関連するイベントのみフィルタリング

        Args:
            events: 全イベントリスト
            symbol: 対象シンボル

        Returns:
            list[EconomicEvent]: フィルタリング済みイベント
        """
        # シンボルから通貨を抽出（6文字の場合）
        target_currencies: set[str] = set()
        if len(symbol) >= 6:
            target_currencies.add(symbol[:3].upper())
            target_currencies.add(symbol[3:6].upper())

        return [
            ev for ev in events
            if ev.currency in target_currencies
            or (ev.symbol and ev.symbol == symbol)
        ]

    def filter_by_impact(
        self,
        events: list[EconomicEvent],
        min_impact: ImpactLevel,
    ) -> list[EconomicEvent]:
        """影響度でイベントをフィルタリング

        Args:
            events: 全イベントリスト
            min_impact: 最小影響度

        Returns:
            list[EconomicEvent]: フィルタリング済みイベント
        """
        order = {
            ImpactLevel.LOW: 0,
            ImpactLevel.MEDIUM: 1,
            ImpactLevel.HIGH: 2,
        }
        min_order = order[min_impact]
        return [
            ev for ev in events
            if order.get(ev.impact, 0) >= min_order
        ]

    def deduplicate(
        self, events: list[EconomicEvent]
    ) -> list[EconomicEvent]:
        """重複イベントを排除

        同一通貨・同一名称・近接時刻のイベントを重複とみなす。
        MT5ソースを優先する。

        Args:
            events: イベントリスト

        Returns:
            list[EconomicEvent]: 重複排除済みリスト
        """
        if not events:
            return []

        # MT5ソースを優先してソート
        sorted_events = sorted(
            events,
            key=lambda e: (
                0 if e.source.value == "MT5" else 1,
                e.event_time,
            ),
        )

        result: list[EconomicEvent] = []
        for event in sorted_events:
            is_dup = False
            for existing in result:
                if self._is_duplicate(event, existing):
                    is_dup = True
                    break
            if not is_dup:
                result.append(event)

        dedup_count = len(events) - len(result)
        if dedup_count > 0:
            logger.debug(
                f"[Normalizer] {dedup_count}件の重複イベントを排除"
            )
        return result

    def _is_duplicate(
        self, a: EconomicEvent, b: EconomicEvent
    ) -> bool:
        """2つのイベントが重複かどうか判定

        Args:
            a: イベントA
            b: イベントB

        Returns:
            bool: 重複していればTrue
        """
        # 通貨が違う場合は重複でない
        if a.currency != b.currency:
            return False

        # 時刻差がウィンドウ外なら重複でない
        time_diff = abs((a.event_time - b.event_time).total_seconds())
        if time_diff > self._dedup_window.total_seconds():
            return False

        # 名称が一致（大文字小文字無視、前方一致）
        name_a = a.event_name.lower().strip()
        name_b = b.event_name.lower().strip()
        if name_a == name_b:
            return True

        # 名前の共通プレフィックス（10文字以上）
        common_len = 10
        if (
            len(name_a) >= common_len
            and len(name_b) >= common_len
            and name_a[:common_len] == name_b[:common_len]
        ):
            return True

        return False

    def get_upcoming_events(
        self,
        events: list[EconomicEvent],
        now: datetime,
        window_minutes: int = 60,
    ) -> list[EconomicEvent]:
        """直近の予定イベントを取得

        Args:
            events: 全イベントリスト
            now: 現在時刻（UTC）
            window_minutes: 検索ウィンドウ（分）

        Returns:
            list[EconomicEvent]: 直近のイベントリスト（時刻順）
        """
        upcoming = [
            ev for ev in events
            if 0 <= ev.minutes_until(now) <= window_minutes
        ]
        return sorted(upcoming, key=lambda e: e.event_time)
