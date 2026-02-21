"""ファンダメンタルデータスキーマ定義

経済イベント・ファンダメンタルコンテキストのデータクラス。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


class ImpactLevel(str, Enum):
    """経済イベントの影響度レベル"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EventSource(str, Enum):
    """経済イベントのデータソース"""

    MT5 = "MT5"
    FOREX_FACTORY = "forex_factory"


@dataclass(frozen=True)
class EconomicEvent:
    """経済イベントデータ

    Args:
        event_id: イベントUUID
        event_time: イベント予定時刻（UTC）
        currency: 通貨コード（USD, JPY等）
        symbol: 関連シンボル（USDJPY等、Noneの場合あり）
        event_name: イベント名称
        impact: 影響度レベル
        actual: 実績値（発表後のみ）
        forecast: 予測値
        previous: 前回値
        source: データソース
        fetched_at: データ取得時刻
    """

    event_id: str
    event_time: datetime
    currency: str
    event_name: str
    impact: ImpactLevel
    source: EventSource
    fetched_at: datetime
    symbol: str | None = None
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None

    @property
    def is_released(self) -> bool:
        """実績値が発表済みかどうか

        Returns:
            bool: 発表済みならTrue
        """
        return self.actual is not None

    @property
    def surprise_magnitude(self) -> float | None:
        """実績と予測の乖離度を計算

        Returns:
            float | None: 乖離度（実績なし・予測なしはNone）
        """
        if self.actual is None or self.forecast is None:
            return None
        if self.forecast == 0:
            return None
        return (self.actual - self.forecast) / abs(self.forecast)

    def minutes_until(self, now: datetime) -> float:
        """現在時刻からイベントまでの分数

        Args:
            now: 現在時刻（UTC）

        Returns:
            float: イベントまでの分数（過去は負）
        """
        delta = self.event_time - now
        return delta.total_seconds() / 60


@dataclass(frozen=True)
class FundamentalContext:
    """ファンダメンタルコンテキスト

    _tick()で毎回取得する軽量オブジェクト。
    LLM Veto判定・エントリーフィルターに使用。

    Args:
        macro_bias_score: マクロバイアス (-1.0〜1.0)
        macro_bias_summary: マクロバイアスの要約
        post_event_bias_score: 指標後バイアス (-1.0〜1.0)
        post_event_summary: 指標後バイアスの要約
        sentiment_score: センチメントスコア (-1.0〜1.0)
        upcoming_events: 直近の予定イベント
        has_high_impact_within_30min: 30分以内の高インパクト指標
    """

    macro_bias_score: float
    macro_bias_summary: str
    post_event_bias_score: float
    post_event_summary: str
    sentiment_score: float
    upcoming_events: list[dict]
    has_high_impact_within_30min: bool

    @classmethod
    def neutral(cls) -> FundamentalContext:
        """ニュートラルなコンテキストを生成

        データがない場合のデフォルト値。

        Returns:
            FundamentalContext: ニュートラルコンテキスト
        """
        return cls(
            macro_bias_score=0.0,
            macro_bias_summary="データなし",
            post_event_bias_score=0.0,
            post_event_summary="データなし",
            sentiment_score=0.0,
            upcoming_events=[],
            has_high_impact_within_30min=False,
        )

    def to_prompt_section(self) -> str:
        """プロンプト用のコンテキストセクションを生成

        Returns:
            str: プロンプト用文字列
        """
        lines = [
            "## ファンダメンタルコンテキスト",
            f"- マクロバイアス: {self.macro_bias_score:+.2f}",
            f"  {self.macro_bias_summary}",
            f"- 指標後バイアス: {self.post_event_bias_score:+.2f}",
            f"  {self.post_event_summary}",
            f"- センチメント: {self.sentiment_score:+.2f}",
        ]
        if self.upcoming_events:
            lines.append("- 直近の予定イベント:")
            for ev in self.upcoming_events[:3]:
                lines.append(
                    f"  - {ev.get('name', '不明')} "
                    f"({ev.get('minutes_until', 0):.0f}分後, "
                    f"インパクト: {ev.get('impact', '不明')})"
                )
        if self.has_high_impact_within_30min:
            lines.append("⚠️ 30分以内に高インパクト指標あり")
        return "\n".join(lines)
