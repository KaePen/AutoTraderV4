"""セッション切替待機フィルタ

セッション切替直後のエントリー抑制を行うフィルタ。
TOKYO→LONDON、LONDON→NY、NY→TOKYO切替後30分はエントリー抑制。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SessionTransitionResult:
    """セッション切替フィルタ結果

    Attributes:
        should_filter: エントリー抑制すべきかどうか
        reason: 抑制理由（抑制時のみ）
        transition_name: 切替名（抑制時のみ）
        minutes_remaining: 待機残り時間（分）
    """

    should_filter: bool
    reason: str = ""
    transition_name: str = ""
    minutes_remaining: int = 0


class SessionTransitionFilter:
    """セッション切替直後の待機フィルタ

    セッション切替直後は流動性変動・スプレッド拡大のリスクがあるため、
    エントリーを抑制する。

    セッション時間（UTC）:
    - TOKYO: 00:00-07:00
    - LONDON: 07:00-13:00
    - LONDON/NY overlap: 13:00-17:00
    - NEW YORK: 17:00-22:00
    - OFF HOURS: 22:00-00:00

    切替タイミング（UTC）:
    - TOKYO→LONDON: 07:00
    - LONDON→NEWYORK: 13:00
    - NEWYORK→TOKYO: 22:00（実質オフ時間経由）

    Attributes:
        TRANSITIONS: 切替タイミングの定義（名前: 開始時間）
        wait_minutes: 切替後の待機時間（分）
    """

    # 切替タイミング（UTC時間）: {名前: (切替開始時間, 待機終了時間)}
    # 待機終了時間 = 開始時間 + wait_minutes/60（時間単位）
    TRANSITIONS: dict[str, int] = {
        "TOKYO_TO_LONDON": 7,      # UTC 07:00
        "LONDON_TO_NEWYORK": 13,   # UTC 13:00
        "NEWYORK_TO_TOKYO": 22,    # UTC 22:00
    }

    def __init__(
        self,
        wait_minutes: int = 30,
        enabled: bool = True,
    ) -> None:
        """初期化

        Args:
            wait_minutes: 切替後の待機時間（分）
            enabled: フィルタ有効フラグ
        """
        self.wait_minutes = wait_minutes
        self.enabled = enabled

    def check(
        self,
        current_time: datetime,
    ) -> SessionTransitionResult:
        """セッション切替待機チェック

        Args:
            current_time: 現在時刻（UTCを想定）

        Returns:
            SessionTransitionResult: フィルタ結果
        """
        if not self.enabled:
            return SessionTransitionResult(should_filter=False)

        hour = current_time.hour
        minute = current_time.minute

        for name, start_hour in self.TRANSITIONS.items():
            # 切替開始時間かつ待機時間内
            if hour == start_hour and minute < self.wait_minutes:
                remaining = self.wait_minutes - minute
                return SessionTransitionResult(
                    should_filter=True,
                    reason=f"セッション切替待機中({name}): "
                           f"残り{remaining}分",
                    transition_name=name,
                    minutes_remaining=remaining,
                )

        return SessionTransitionResult(should_filter=False)

    def should_filter(
        self,
        current_time: datetime,
    ) -> tuple[bool, str]:
        """セッション切替待機チェック（簡易版）

        HardGuardとの互換性のためのシンプルなインターフェース。

        Args:
            current_time: 現在時刻（UTCを想定）

        Returns:
            tuple[bool, str]: (フィルタすべきか, 理由)
        """
        result = self.check(current_time)
        return result.should_filter, result.reason
