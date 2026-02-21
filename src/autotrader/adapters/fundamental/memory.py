"""ファンダメンタルメモリサービス

LLMが生成した方向性の記憶をDBに蓄積・取得する。
マクロバイアス・指標後バイアス・センチメントを管理。
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
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)

# 記憶タイプ定数
MEMORY_TYPE_MACRO_BIAS = "MACRO_BIAS"
MEMORY_TYPE_POST_EVENT_BIAS = "POST_EVENT_BIAS"
MEMORY_TYPE_SENTIMENT_SCORE = "SENTIMENT_SCORE"

# TTL設定
_TTL_MAP: dict[str, timedelta] = {
    MEMORY_TYPE_MACRO_BIAS: timedelta(days=7),
    MEMORY_TYPE_POST_EVENT_BIAS: timedelta(days=3),
    MEMORY_TYPE_SENTIMENT_SCORE: timedelta(hours=4),
}


class FundamentalMemoryService:
    """ファンダメンタルメモリサービス

    LLM生成の方向性記憶をDB管理し、_tick()で使用する
    FundamentalContextを提供する。

    Args:
        session_factory: SQLAlchemyセッションファクトリー
        event_guard_minutes: 重要指標前の取引停止分数
        cached_events_getter: 最新イベントリストの取得関数
    """

    def __init__(
        self,
        session_factory,
        event_guard_minutes: int = 30,
        cached_events_getter=None,
    ) -> None:
        """初期化

        Args:
            session_factory: SQLAlchemyセッションファクトリー
            event_guard_minutes: 重要指標前の取引停止分数
            cached_events_getter: イベントリスト取得関数
        """
        self._session_factory = session_factory
        self._guard_minutes = event_guard_minutes
        self._get_cached_events = cached_events_getter or (
            lambda: []
        )
        self._normalizer = EconomicEventNormalizer()

    def get_context_for_llm(
        self, symbol: str, now: datetime
    ) -> FundamentalContext:
        """LLM用のファンダメンタルコンテキストを取得

        毎tick呼ばれる軽量な取得処理。

        Args:
            symbol: トレード対象シンボル
            now: 現在時刻（UTC）

        Returns:
            FundamentalContext: ファンダメンタルコンテキスト
        """
        try:
            macro_score, macro_summary = self._get_memory_score(
                symbol, MEMORY_TYPE_MACRO_BIAS, now
            )
            post_score, post_summary = self._get_memory_score(
                symbol, MEMORY_TYPE_POST_EVENT_BIAS, now
            )
            sentiment_score, _ = self._get_memory_score(
                symbol, MEMORY_TYPE_SENTIMENT_SCORE, now
            )

            # 直近イベント取得
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

            # 30分以内の高インパクト指標チェック
            high_impact_soon = any(
                ev.impact == ImpactLevel.HIGH
                and 0 <= ev.minutes_until(now) <= self._guard_minutes
                for ev in upcoming
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

        except Exception as e:
            logger.warning(
                f"[FundamentalMemory] コンテキスト取得エラー: {e}"
            )
            return FundamentalContext.neutral()

    def write_macro_bias(
        self,
        symbol: str,
        direction_score: float,
        confidence: float,
        summary: str,
        llm_reasoning: str | None = None,
    ) -> None:
        """マクロバイアスを記録

        Args:
            symbol: シンボル
            direction_score: 方向性スコア (-1.0〜+1.0)
            confidence: 確信度 (0.0〜1.0)
            summary: 要約（日本語50文字以内推奨）
            llm_reasoning: LLM推論根拠
        """
        self._write_memory(
            symbol=symbol,
            memory_type=MEMORY_TYPE_MACRO_BIAS,
            direction_score=direction_score,
            confidence=confidence,
            summary=summary,
            llm_reasoning=llm_reasoning,
        )

    def write_post_event_bias(
        self,
        symbol: str,
        direction_score: float,
        confidence: float,
        summary: str,
        source_event: str,
        llm_reasoning: str | None = None,
    ) -> None:
        """指標後バイアスを記録

        Args:
            symbol: シンボル
            direction_score: 方向性スコア (-1.0〜+1.0)
            confidence: 確信度 (0.0〜1.0)
            summary: 要約
            source_event: ソースイベント名
            llm_reasoning: LLM推論根拠
        """
        self._write_memory(
            symbol=symbol,
            memory_type=MEMORY_TYPE_POST_EVENT_BIAS,
            direction_score=direction_score,
            confidence=confidence,
            summary=summary,
            source_event=source_event,
            llm_reasoning=llm_reasoning,
        )

    def write_sentiment_score(
        self,
        symbol: str,
        sentiment_score: float,
        confidence: float,
        summary: str,
    ) -> None:
        """センチメントスコアを記録

        Args:
            symbol: シンボル
            sentiment_score: センチメントスコア (-1.0〜+1.0)
            confidence: 確信度 (0.0〜1.0)
            summary: 要約
        """
        self._write_memory(
            symbol=symbol,
            memory_type=MEMORY_TYPE_SENTIMENT_SCORE,
            direction_score=sentiment_score,
            confidence=confidence,
            summary=summary,
        )

    def get_upcoming_events(
        self, symbol: str, now: datetime, window_minutes: int = 60
    ) -> list[EconomicEvent]:
        """直近の予定イベントを取得（Veto判定用）

        Args:
            symbol: シンボル
            now: 現在時刻（UTC）
            window_minutes: 検索ウィンドウ（分）

        Returns:
            list[EconomicEvent]: 直近のイベントリスト
        """
        cached_events = self._get_cached_events()
        symbol_events = self._normalizer.filter_by_symbol(
            cached_events, symbol
        )
        return self._normalizer.get_upcoming_events(
            symbol_events, now, window_minutes
        )

    def _get_memory_score(
        self,
        symbol: str,
        memory_type: str,
        now: datetime,
    ) -> tuple[float, str]:
        """DBから最新の有効な記憶スコアを取得

        Args:
            symbol: シンボル
            memory_type: 記憶タイプ
            now: 現在時刻

        Returns:
            tuple[float, str]: (スコア, 要約)
        """
        try:
            from autotrader.adapters.database.repositories import (
                MarketMemoryRepository,
            )
            with self._session_factory() as session:
                repo = MarketMemoryRepository(session)
                records = repo.get_active(symbol, memory_type, now)
                if not records:
                    return 0.0, "記憶なし"

                # 最新レコードを取得
                latest = records[0]
                score = latest.direction_score
                summary = latest.summary or "データなし"
                return score, summary

        except Exception as e:
            logger.debug(
                f"[FundamentalMemory] スコア取得エラー: {e}"
            )
            return 0.0, "エラー"

    def _write_memory(
        self,
        symbol: str,
        memory_type: str,
        direction_score: float,
        confidence: float,
        summary: str,
        source_event: str | None = None,
        llm_reasoning: str | None = None,
    ) -> None:
        """市場記憶をDBに書き込み

        Args:
            symbol: シンボル
            memory_type: 記憶タイプ
            direction_score: 方向性スコア
            confidence: 確信度
            summary: 要約
            source_event: ソースイベント名
            llm_reasoning: LLM推論根拠
        """
        ttl = _TTL_MAP.get(memory_type, timedelta(days=1))
        now = datetime.now(timezone.utc)
        valid_until = now + ttl

        try:
            from autotrader.adapters.database.repositories import (
                MarketMemoryRepository,
            )
            with self._session_factory() as session:
                repo = MarketMemoryRepository(session)
                repo.create(
                    memory_id=str(uuid4()),
                    symbol=symbol,
                    memory_type=memory_type,
                    direction_score=direction_score,
                    confidence=confidence,
                    valid_until=valid_until,
                    summary=summary,
                    source_event=source_event,
                    llm_reasoning=llm_reasoning,
                )
                session.commit()
                logger.info(
                    f"[FundamentalMemory] {memory_type}記録: "
                    f"{symbol} score={direction_score:+.2f} "
                    f"TTL={ttl}"
                )
        except Exception as e:
            logger.error(
                f"[FundamentalMemory] DB書き込みエラー: {e}"
            )
