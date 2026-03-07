"""フィルターマネージャー

複数のフィルターを統合管理し、LLMフィルターをシミュレート。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from autotrader.constraint.filters.event_filter import EventFilter
from autotrader.constraint.filters.filter_result import (
    ManagerFilterResult as FilterResult,
)
from autotrader.constraint.filters.session_filter import (
    SessionFilter,
)
from autotrader.constraint.filters.volatility_filter import (
    VolatilityFilter,
)


class FilterManager:
    """フィルターマネージャー

    複数のフィルターを統合し、LLMによるエントリーフィルターを
    ルールベースでシミュレートする。

    Args:
        calendar_path: 経済カレンダーCSVパス（オプション）
        use_event_filter: イベントフィルターを使用するか
        use_volatility_filter: ボラティリティフィルターを使用するか
        use_session_filter: セッションフィルターを使用するか
    """

    def __init__(
        self,
        calendar_path: Path | str | None = None,
        use_event_filter: bool = True,
        use_volatility_filter: bool = True,
        use_session_filter: bool = True,
        pip_unit: float = 0.01,
    ) -> None:
        self.filters: list[tuple[str, any]] = []

        # イベントフィルター
        if use_event_filter:
            event_filter = EventFilter()
            if calendar_path:
                event_filter.load_calendar(calendar_path)
            self.filters.append(("event", event_filter))

        # ボラティリティフィルター
        if use_volatility_filter:
            volatility_filter = VolatilityFilter(
                high_threshold_percentile=95.0,
                low_threshold_percentile=10.0,
                pip_unit=pip_unit,
            )
            self.filters.append(("volatility", volatility_filter))

        # セッションフィルター
        if use_session_filter:
            session_filter = SessionFilter(
                use_kill_zones=True,
                skip_low_liquidity=True,
            )
            self.filters.append(("session", session_filter))

    def should_skip(
        self,
        timestamp: datetime,
        row: pd.Series,
        symbol: str = "USDJPY",
        df_history: pd.DataFrame | None = None,
    ) -> FilterResult:
        """トレードをスキップすべきか判定

        いずれかのフィルターがスキップを返したらスキップ。

        Args:
            timestamp: トレード時刻
            row: 現在のデータ行
            symbol: 通貨ペア
            df_history: 過去データ（ボラティリティ計算用）

        Returns:
            FilterResult: フィルター結果
        """
        for filter_name, filter_instance in self.filters:
            result = self._apply_filter(
                filter_name,
                filter_instance,
                timestamp,
                row,
                symbol,
                df_history,
            )
            if result.skip:
                return result

        return FilterResult(skip=False)

    def _apply_filter(
        self,
        filter_name: str,
        filter_instance: any,
        timestamp: datetime,
        row: pd.Series,
        symbol: str,
        df_history: pd.DataFrame | None,
    ) -> FilterResult:
        """個別フィルターを適用"""
        if filter_name == "event":
            result = filter_instance.should_skip(
                timestamp, symbol
            )
            if result.skip:
                return FilterResult(
                    skip=True,
                    reason=result.reason,
                    filter_name="event",
                    confidence_adjustment=-0.3,
                )

        elif filter_name == "volatility":
            result = filter_instance.should_skip(
                row, df_history
            )
            if result.skip:
                return FilterResult(
                    skip=True,
                    reason=result.reason,
                    filter_name="volatility",
                    confidence_adjustment=-0.2,
                )

        elif filter_name == "session":
            result = filter_instance.should_skip(timestamp)
            if result.skip:
                return FilterResult(
                    skip=True,
                    reason=result.reason,
                    filter_name="session",
                    confidence_adjustment=-0.1,
                )

        return FilterResult(skip=False)

    def get_filter_stats(self) -> dict[str, int]:
        """フィルター統計を取得"""
        return {name: 0 for name, _ in self.filters}

    def adjust_confidence(
        self,
        base_confidence: float,
        timestamp: datetime,
        row: pd.Series,
        symbol: str = "USDJPY",
        df_history: pd.DataFrame | None = None,
    ) -> float:
        """確度を調整（スキップせずに減点のみ）"""
        adjustment = 0.0

        for filter_name, filter_instance in self.filters:
            if filter_name == "session" and hasattr(
                filter_instance, "is_kill_zone"
            ):
                if not filter_instance.is_kill_zone(
                    timestamp
                ):
                    adjustment -= 0.1

        for filter_name, filter_instance in self.filters:
            if filter_name == "volatility":
                regime = (
                    filter_instance.calculate_atr_regime(
                        row, df_history
                    )
                )
                if regime == "extreme":
                    adjustment -= 0.2
                elif regime == "high":
                    adjustment -= 0.1

        adjusted = max(
            0.0, min(1.0, base_confidence + adjustment)
        )
        return adjusted


# 後方互換エイリアス
BacktestFilterManager = FilterManager
