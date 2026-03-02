"""時刻抽象化 -- テスト時の時刻固定を可能にする

本番では SystemClock、テストでは FixedClock を注入する。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """時刻提供プロトコル"""

    def now(self) -> datetime: ...


class SystemClock:
    """本番用: システム時刻を返す"""

    def now(self) -> datetime:
        """現在のシステム時刻を返す"""
        return datetime.now()


class FixedClock:
    """テスト用: 固定時刻を返す

    Args:
        fixed_time: 返す固定時刻
    """

    def __init__(self, fixed_time: datetime) -> None:
        self._time = fixed_time

    def now(self) -> datetime:
        """固定時刻を返す"""
        return self._time

    def advance(self, **kwargs: float) -> None:
        """時刻を進める（テスト用）

        Args:
            **kwargs: timedelta に渡す引数（hours, minutes, seconds 等）
        """
        self._time += timedelta(**kwargs)
