"""経済イベントフィルター

高インパクトの経済指標発表前後はトレードをスキップ。
NFP、FOMC、CPI等の重要イベント時の高ボラティリティを回避。
"""

from __future__ import annotations

from autotrader.constraint.filters.filter_result import FilterResult

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd




class EventFilter:
    """経済イベントフィルター

    経済カレンダーデータを使用して、高インパクトイベント前後の
    トレードをフィルタリングする。

    Args:
        calendar_data: 経済カレンダーDataFrame
        window_minutes: イベント前後の除外時間（分）
        min_impact: 最低インパクトレベル（high/medium/low）
    """

    def __init__(
        self,
        calendar_data: pd.DataFrame | None = None,
        window_minutes: int = 30,
        min_impact: str = "high",
    ) -> None:
        self.calendar = calendar_data
        self.window_minutes = window_minutes
        self.min_impact = min_impact

        # 通貨マッピング（シンボルから通貨を抽出）
        self._currency_map = {
            "USDJPY": ["USD", "JPY"],
            "EURUSD": ["EUR", "USD"],
            "GBPUSD": ["GBP", "USD"],
            "AUDUSD": ["AUD", "USD"],
            "USDCAD": ["USD", "CAD"],
            "NZDUSD": ["NZD", "USD"],
            "USDCHF": ["USD", "CHF"],
            "EURJPY": ["EUR", "JPY"],
            "GBPJPY": ["GBP", "JPY"],
            "AUDJPY": ["AUD", "JPY"],
            "CADJPY": ["CAD", "JPY"],
            "CHFJPY": ["CHF", "JPY"],
        }

    def load_calendar(self, file_path: Path | str) -> None:
        """経済カレンダーを読み込み

        Args:
            file_path: CSVファイルパス

        Expected CSV format:
            datetime,event_name,currency,impact,actual,forecast,previous
        """
        file_path = Path(file_path)
        if file_path.exists():
            self.calendar = pd.read_csv(file_path, parse_dates=["datetime"])
        else:
            self.calendar = pd.DataFrame(
                columns=["datetime", "event_name", "currency", "impact"]
            )

    def should_skip(
        self,
        timestamp: datetime,
        symbol: str = "USDJPY",
    ) -> FilterResult:
        """トレードをスキップすべきか判定

        Args:
            timestamp: トレード時刻
            symbol: 通貨ペア

        Returns:
            FilterResult: フィルター結果
        """
        if self.calendar is None or self.calendar.empty:
            return FilterResult(skip=False)

        # 対象通貨
        currencies = self._currency_map.get(symbol, [symbol[:3], symbol[3:]])

        # 時間ウィンドウ
        window_start = timestamp - timedelta(minutes=self.window_minutes)
        window_end = timestamp + timedelta(minutes=self.window_minutes)

        # 該当イベントを検索
        mask = (
            (self.calendar["datetime"] >= window_start)
            & (self.calendar["datetime"] <= window_end)
            & (self.calendar["currency"].isin(currencies))
        )

        # インパクトフィルター
        if self.min_impact == "high":
            mask &= self.calendar["impact"] == "high"
        elif self.min_impact == "medium":
            mask &= self.calendar["impact"].isin(["high", "medium"])

        matching_events = self.calendar[mask]

        if len(matching_events) > 0:
            event = matching_events.iloc[0]
            return FilterResult(
                skip=True,
                reason=f"高インパクトイベント: {event['event_name']}",
            )

        return FilterResult(skip=False)

    @staticmethod
    def get_known_high_impact_times() -> list[tuple[int, int]]:
        """既知の高インパクト時間帯（UTC）

        主要な定例発表時間を返す。

        Returns:
            list[tuple[int, int]]: (hour, minute) のリスト
        """
        return [
            (13, 30),  # US経済指標（NFP, CPI等）
            (14, 0),   # ISM等
            (18, 0),   # FOMC
            (9, 0),    # UK GDP等
            (8, 30),   # EU指標
            (23, 50),  # 日銀
        ]
