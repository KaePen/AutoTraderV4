"""セッションフィルター（時間帯フィルター）

低流動性時間帯でのトレードをスキップ。
Kill Zones（ロンドン/NYセッション）のみでトレード。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class FilterResult:
    """フィルター結果

    Attributes:
        skip: スキップするかどうか
        reason: スキップ理由（スキップしない場合は空文字）
    """

    skip: bool
    reason: str = ""


@dataclass
class SessionWindow:
    """取引セッションウィンドウ

    Attributes:
        name: セッション名
        start_hour: 開始時間（UTC）
        end_hour: 終了時間（UTC）
        liquidity: 流動性レベル（high/medium/low）
    """

    name: str
    start_hour: int
    end_hour: int
    liquidity: str = "high"


class SessionFilter:
    """セッションフィルター

    取引セッションに基づいてエントリーをフィルタリング。

    Args:
        allowed_sessions: 許可するセッション名リスト
        use_kill_zones: Kill Zonesのみでトレードするか
    """

    # 主要取引セッション（UTC）
    SESSIONS = {
        "tokyo": SessionWindow("Tokyo", 0, 9, "medium"),
        "london": SessionWindow("London", 7, 16, "high"),
        "new_york": SessionWindow("New York", 13, 22, "high"),
        "sydney": SessionWindow("Sydney", 21, 6, "low"),
    }

    # Kill Zones（高流動性時間帯、UTC）
    KILL_ZONES = [
        SessionWindow("London Open", 7, 10, "high"),
        SessionWindow("NY Open", 13, 16, "high"),
        SessionWindow("London/NY Overlap", 13, 17, "high"),
    ]

    # 低流動性時間帯（UTC）
    LOW_LIQUIDITY_HOURS = [
        (21, 23),  # NYクローズ後
        (0, 7),    # アジア深夜〜早朝
    ]

    def __init__(
        self,
        allowed_sessions: list[str] | None = None,
        use_kill_zones: bool = True,
        skip_low_liquidity: bool = True,
    ) -> None:
        self.allowed_sessions = allowed_sessions or ["london", "new_york"]
        self.use_kill_zones = use_kill_zones
        self.skip_low_liquidity = skip_low_liquidity

    def should_skip(
        self,
        timestamp: datetime,
    ) -> FilterResult:
        """トレードをスキップすべきか判定

        Args:
            timestamp: トレード時刻（UTC想定）

        Returns:
            FilterResult: フィルター結果
        """
        hour = timestamp.hour

        # 低流動性時間帯チェック
        if self.skip_low_liquidity:
            for start, end in self.LOW_LIQUIDITY_HOURS:
                if start <= end:
                    if start <= hour < end:
                        return FilterResult(
                            skip=True,
                            reason=f"低流動性時間帯({hour}時UTC)",
                        )
                else:
                    # 日をまたぐ場合（21-23, 0-7）
                    if hour >= start or hour < end:
                        return FilterResult(
                            skip=True,
                            reason=f"低流動性時間帯({hour}時UTC)",
                        )

        # Kill Zonesモード
        if self.use_kill_zones:
            in_kill_zone = False
            for kz in self.KILL_ZONES:
                if kz.start_hour <= hour < kz.end_hour:
                    in_kill_zone = True
                    break
            if not in_kill_zone:
                return FilterResult(
                    skip=True,
                    reason=f"Kill Zone外({hour}時UTC)",
                )
        else:
            # 許可セッションチェック
            in_allowed_session = False
            for session_name in self.allowed_sessions:
                session = self.SESSIONS.get(session_name)
                if session:
                    if session.start_hour <= session.end_hour:
                        if session.start_hour <= hour < session.end_hour:
                            in_allowed_session = True
                            break
                    else:
                        # 日をまたぐ場合
                        if hour >= session.start_hour or hour < session.end_hour:
                            in_allowed_session = True
                            break

            if not in_allowed_session:
                return FilterResult(
                    skip=True,
                    reason=f"許可セッション外({hour}時UTC)",
                )

        return FilterResult(skip=False)

    def get_current_session(self, timestamp: datetime) -> str | None:
        """現在のセッションを取得

        Args:
            timestamp: 時刻（UTC）

        Returns:
            str | None: セッション名（なければNone）
        """
        hour = timestamp.hour
        for name, session in self.SESSIONS.items():
            if session.start_hour <= session.end_hour:
                if session.start_hour <= hour < session.end_hour:
                    return name
            else:
                if hour >= session.start_hour or hour < session.end_hour:
                    return name
        return None

    def is_kill_zone(self, timestamp: datetime) -> bool:
        """Kill Zone内かどうか

        Args:
            timestamp: 時刻（UTC）

        Returns:
            bool: Kill Zone内ならTrue
        """
        hour = timestamp.hour
        for kz in self.KILL_ZONES:
            if kz.start_hour <= hour < kz.end_hour:
                return True
        return False
