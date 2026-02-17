"""ポジションイベントログ

ポジションのライフサイクルイベントをCSV出力する。
"""

from __future__ import annotations

import csv
from datetime import datetime
from enum import Enum
from pathlib import Path


class PositionEventType(str, Enum):
    """ポジションイベント種別"""

    OPEN = "OPEN"
    PARTIAL_CLOSE_1R = "PARTIAL_CLOSE_1R"
    PARTIAL_CLOSE_2R = "PARTIAL_CLOSE_2R"
    SL_MOVE_BE = "SL_MOVE_BE"
    SL_MOVE_1R = "SL_MOVE_1R"
    TRAILING_UPDATE = "TRAILING_UPDATE"
    FULL_CLOSE = "FULL_CLOSE"


POSITION_EVENT_COLUMNS = [
    "timestamp",
    "position_id",
    "event_type",
    "price",
    "volume_before",
    "volume_after",
    "sl_before",
    "sl_after",
    "current_r",
    "reason",
]


class PositionEventLogger:
    """ポジションイベントロガー

    Attributes:
        _events: イベントリスト
    """

    def __init__(self) -> None:
        """初期化"""
        self._events: list[dict[str, object]] = []

    def log(
        self,
        timestamp: datetime,
        position_id: str,
        event_type: PositionEventType,
        price: float,
        volume_before: float,
        volume_after: float,
        sl_before: float,
        sl_after: float,
        current_r: float = 0.0,
        reason: str = "",
    ) -> None:
        """イベントを記録

        Args:
            timestamp: 発生時刻
            position_id: ポジションID
            event_type: イベント種別
            price: 現在価格
            volume_before: 変更前ボリューム
            volume_after: 変更後ボリューム
            sl_before: 変更前SL
            sl_after: 変更後SL
            current_r: 現在のR値
            reason: 理由
        """
        self._events.append({
            "timestamp": timestamp.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "position_id": position_id,
            "event_type": event_type.value,
            "price": f"{price:.5f}",
            "volume_before": f"{volume_before:.2f}",
            "volume_after": f"{volume_after:.2f}",
            "sl_before": f"{sl_before:.5f}",
            "sl_after": f"{sl_after:.5f}",
            "current_r": f"{current_r:.2f}",
            "reason": reason,
        })

    def write_csv(self, output_path: Path) -> None:
        """CSVファイルに書き出し

        Args:
            output_path: 出力パス
        """
        if not self._events:
            return

        with open(
            output_path, "w",
            newline="", encoding="utf-8",
        ) as f:
            writer = csv.DictWriter(
                f, fieldnames=POSITION_EVENT_COLUMNS,
            )
            writer.writeheader()
            writer.writerows(self._events)

    def reset(self) -> None:
        """リセット"""
        self._events.clear()

    @property
    def event_count(self) -> int:
        """イベント数"""
        return len(self._events)
