"""バックテスト用ファンダメンタルメモリシミュレーター

バックテスト時はDBなしでインメモリ動作する
FundamentalMemoryの代替実装。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from uuid import uuid4

from loguru import logger

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    FundamentalContext,
    ImpactLevel,
)
from autotrader.adapters.fundamental.memory import (
    MEMORY_TYPE_MACRO_BIAS,
    MEMORY_TYPE_POST_EVENT_BIAS,
    MEMORY_TYPE_SENTIMENT_SCORE,
    _TTL_MAP,
)
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)


class _MemoryRecord:
    """インメモリ記憶レコード"""

    __slots__ = [
        "symbol", "memory_type", "direction_score",
        "confidence", "summary", "valid_until",
    ]

    def __init__(
        self,
        symbol: str,
        memory_type: str,
        direction_score: float,
        confidence: float,
        summary: str,
        valid_until: datetime,
    ) -> None:
        self.symbol = symbol
        self.memory_type = memory_type
        self.direction_score = direction_score
        self.confidence = confidence
        self.summary = summary
        self.valid_until = valid_until


class FundamentalMemorySimulator:
    """バックテスト用ファンダメンタルメモリシミュレーター

    FundamentalMemoryServiceと同じインターフェースを提供するが、
    DBを使わずインメモリで動作する。

    バックテストでは通常LLMによるマクロバイアス書き込みを行わないため、
    get_context_for_llm()は常にニュートラルを返す
    （ただし高インパクト指標前のスキップ判定は有効）。

    Args:
        event_guard_minutes: 重要指標前の取引停止分数
        cached_events_getter: イベントリスト取得関数
    """

    def __init__(
        self,
        event_guard_minutes: int = 30,
        cached_events_getter=None,
    ) -> None:
        """初期化

        Args:
            event_guard_minutes: 重要指標前の取引停止分数
            cached_events_getter: イベントリスト取得関数
        """
        self._guard_minutes = event_guard_minutes
        self._get_cached_events = cached_events_getter or (
            lambda: []
        )
        self._normalizer = EconomicEventNormalizer()
        self._records: list[_MemoryRecord] = []

    def get_context_for_llm(
        self, symbol: str, now: datetime
    ) -> FundamentalContext:
        """ファンダメンタルコンテキストを取得

        バックテスト時はイベントベースのスキップ判定のみ有効。

        Args:
            symbol: シンボル
            now: バックテスト現在時刻

        Returns:
            FundamentalContext: コンテキスト
        """
        cached_events = self._get_cached_events()
        symbol_events = self._normalizer.filter_by_symbol(
            cached_events, symbol
        )
        upcoming = self._normalizer.get_upcoming_events(
            symbol_events, now, window_minutes=60
        )

        upcoming_dicts = [
            {
                "name": ev.event_name,
                "minutes_until": ev.minutes_until(now),
                "impact": ev.impact.value,
            }
            for ev in upcoming
        ]

        high_impact_soon = any(
            ev.impact == ImpactLevel.HIGH
            and 0 <= ev.minutes_until(now) <= self._guard_minutes
            for ev in upcoming
        )

        # インメモリ記憶から最新スコアを取得
        macro_score, macro_summary = self._get_score(
            symbol, MEMORY_TYPE_MACRO_BIAS, now
        )
        post_score, post_summary = self._get_score(
            symbol, MEMORY_TYPE_POST_EVENT_BIAS, now
        )
        sentiment_score, _ = self._get_score(
            symbol, MEMORY_TYPE_SENTIMENT_SCORE, now
        )

        return FundamentalContext(
            macro_bias_score=macro_score,
            macro_bias_summary=macro_summary,
            post_event_bias_score=post_score,
            post_event_summary=post_summary,
            sentiment_score=sentiment_score,
            upcoming_events=upcoming_dicts,
            has_high_impact_within_30min=high_impact_soon,
        )

    def write_macro_bias(
        self,
        symbol: str,
        direction_score: float,
        confidence: float,
        summary: str,
        **kwargs,
    ) -> None:
        """マクロバイアスを記録（インメモリ）

        Args:
            symbol: シンボル
            direction_score: 方向性スコア
            confidence: 確信度
            summary: 要約
        """
        self._write(
            symbol, MEMORY_TYPE_MACRO_BIAS,
            direction_score, confidence, summary
        )

    def write_post_event_bias(
        self,
        symbol: str,
        direction_score: float,
        confidence: float,
        summary: str,
        **kwargs,
    ) -> None:
        """指標後バイアスを記録（インメモリ）

        Args:
            symbol: シンボル
            direction_score: 方向性スコア
            confidence: 確信度
            summary: 要約
        """
        self._write(
            symbol, MEMORY_TYPE_POST_EVENT_BIAS,
            direction_score, confidence, summary
        )

    def _write(
        self,
        symbol: str,
        memory_type: str,
        direction_score: float,
        confidence: float,
        summary: str,
    ) -> None:
        """インメモリ記憶を書き込み

        Args:
            symbol: シンボル
            memory_type: 記憶タイプ
            direction_score: 方向性スコア
            confidence: 確信度
            summary: 要約
        """
        ttl = _TTL_MAP.get(memory_type, timedelta(days=1))
        now = datetime.now(timezone.utc)
        self._records.append(_MemoryRecord(
            symbol=symbol,
            memory_type=memory_type,
            direction_score=direction_score,
            confidence=confidence,
            summary=summary,
            valid_until=now + ttl,
        ))

    def _get_score(
        self,
        symbol: str,
        memory_type: str,
        now: datetime,
    ) -> tuple[float, str]:
        """最新の有効な記憶スコアを取得

        Args:
            symbol: シンボル
            memory_type: 記憶タイプ
            now: 現在時刻

        Returns:
            tuple[float, str]: (スコア, 要約)
        """
        active = [
            r for r in self._records
            if (
                r.symbol == symbol
                and r.memory_type == memory_type
                and r.valid_until > now
            )
        ]
        if not active:
            return 0.0, "記憶なし"

        latest = active[-1]
        return latest.direction_score, latest.summary
