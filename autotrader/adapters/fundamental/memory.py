"""ファンダメンタルメモリサービス

LLMが生成した方向性の記憶をDBに蓄積・取得する。
マクロバイアス・指標後バイアス・センチメントを管理。

Phase 2 統合:
DeterministicEventAnalyzer を使ったリアルタイムイベント分析と、
FundamentalContext合成もサポートする。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from loguru import logger

from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)
from autotrader.adapters.fundamental.schemas import (
    IMPACT_WEIGHT,
    INFLUENCE_THRESHOLD,
    MAX_LOOKBACK_HOURS,
    EconomicEvent,
    EventLLMRecord,
    FundamentalContext,
    ImpactLevel,
    compute_influence,
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

    Phase 2 統合:
    analyzer が設定されている場合、発表済みイベントを
    DeterministicEventAnalyzer でリアルタイム分析し、
    バックテストと同等のPhase 2フィールド付きコンテキストを返す。

    Args:
        session_factory: SQLAlchemyセッションファクトリー
        event_guard_minutes: 重要指標前の取引停止分数
        cached_events_getter: 最新イベントリストの取得関数
        analyzer: DeterministicEventAnalyzer（Phase 2統合用）
    """

    def __init__(
        self,
        session_factory,
        event_guard_minutes: int = 30,
        cached_events_getter=None,
        analyzer=None,
    ) -> None:
        """初期化

        Args:
            session_factory: SQLAlchemyセッションファクトリー
            event_guard_minutes: 重要指標前の取引停止分数
            cached_events_getter: イベントリスト取得関数
            analyzer: DeterministicEventAnalyzer インスタンス
        """
        self._session_factory = session_factory
        self._guard_minutes = event_guard_minutes
        self._get_cached_events = cached_events_getter or (
            lambda: []
        )
        self._normalizer = EconomicEventNormalizer()
        self._analyzer = analyzer

        # 分析済みイベントのキャッシュ
        # symbol → EventLLMRecord リスト（時刻昇順）
        self._event_records: dict[
            str, list[EventLLMRecord]
        ] = {}
        # 分析済みイベントID（重複分析防止）
        self._analyzed_event_ids: set[str] = set()

    def get_context_for_llm(
        self, symbol: str, now: datetime
    ) -> FundamentalContext:
        """ファンダメンタルコンテキストを取得

        analyzer が設定されている場合、発表済みイベントを
        リアルタイム分析してPhase 2フィールド付きで返す。
        設定されていない場合は従来のDB記憶ベースで返す。

        Args:
            symbol: トレード対象シンボル
            now: 現在時刻（UTC）

        Returns:
            FundamentalContext: ファンダメンタルコンテキスト
        """
        try:
            # 直近イベント取得
            cached_events = self._get_cached_events()
            symbol_events = (
                self._normalizer.filter_by_symbol(
                    cached_events, symbol
                )
            )
            upcoming = (
                self._normalizer.get_upcoming_events(
                    symbol_events, now, window_minutes=60
                )
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
                and 0
                <= ev.minutes_until(now)
                <= self._guard_minutes
                for ev in upcoming
            )

            # Phase 2: analyzer があれば合成コンテキスト
            if self._analyzer is not None:
                self._analyze_released_events(
                    symbol, symbol_events, now
                )
                return self._synthesize_event_context(
                    symbol,
                    now,
                    upcoming_dicts,
                    high_impact_soon,
                )

            # 従来: DB記憶ベース
            macro_score, macro_summary = (
                self._get_memory_score(
                    symbol, MEMORY_TYPE_MACRO_BIAS, now
                )
            )
            post_score, post_summary = (
                self._get_memory_score(
                    symbol,
                    MEMORY_TYPE_POST_EVENT_BIAS,
                    now,
                )
            )
            sentiment_score, _ = self._get_memory_score(
                symbol,
                MEMORY_TYPE_SENTIMENT_SCORE,
                now,
            )

            return FundamentalContext(
                macro_bias_score=macro_score,
                macro_bias_summary=macro_summary,
                post_event_bias_score=post_score,
                post_event_summary=post_summary,
                sentiment_score=sentiment_score,
                upcoming_events=upcoming_dicts,
                has_high_impact_within_30min=(
                    high_impact_soon
                ),
            )

        except Exception as e:
            logger.warning(
                "[FundamentalMemory] "
                "コンテキスト取得エラー: %s",
                e,
            )
            return FundamentalContext.neutral()

    # --------------------------------------------------
    # Phase 2: リアルタイムイベント分析・合成
    # --------------------------------------------------

    def _analyze_released_events(
        self,
        symbol: str,
        events: list[EconomicEvent],
        now: datetime,
    ) -> None:
        """発表済みイベントを分析し _event_records に蓄積

        未分析の発表済みイベントを DeterministicEventAnalyzer で
        分析し、結果を内部キャッシュに蓄積する。

        Args:
            symbol: 対象シンボル
            events: シンボル関連イベントリスト
            now: 現在時刻
        """
        if self._analyzer is None:
            return

        for ev in events:
            # 発表済みかつ未分析のみ
            if not ev.is_released:
                continue
            if ev.event_id in self._analyzed_event_ids:
                continue
            # 未来イベントはスキップ
            if ev.event_time > now:
                continue

            try:
                record = self._analyzer.analyze_single_event(
                    symbol, ev,
                )
                records = self._event_records.setdefault(
                    symbol, []
                )
                records.append(record)
                self._analyzed_event_ids.add(ev.event_id)
            except Exception as e:
                logger.debug(
                    "[FundamentalMemory] "
                    "イベント分析失敗: %s",
                    e,
                )

        # 時刻順にソート
        if symbol in self._event_records:
            self._event_records[symbol].sort(
                key=lambda r: r.event_time
            )

    def _synthesize_event_context(
        self,
        symbol: str,
        now: datetime,
        upcoming_dicts: list[dict],
        high_impact_soon: bool,
        decay_coefficient: float = 2.0,
    ) -> FundamentalContext:
        """蓄積済み EventLLMRecord からコンテキストを合成

        BacktestFundamentalProvider._synthesize_event_llm_context()
        と同じアルゴリズムで、リアルタイムイベントから
        Phase 2フィールドを生成する。

        Args:
            symbol: 対象シンボル
            now: 現在時刻
            upcoming_dicts: 直近イベント情報
            high_impact_soon: 高インパクトフラグ
            decay_coefficient: 時間減衰係数

        Returns:
            FundamentalContext: 合成コンテキスト
        """
        records = self._event_records.get(symbol, [])
        if not records:
            return FundamentalContext(
                upcoming_events=upcoming_dicts,
                has_high_impact_within_30min=(
                    high_impact_soon
                ),
            )

        # 過去72時間のアクティブイベントを抽出
        cutoff = now - timedelta(
            hours=MAX_LOOKBACK_HOURS,
        )
        active: list[
            tuple[EventLLMRecord, float]
        ] = []
        for rec in records:
            if rec.event_time < cutoff:
                continue
            if rec.event_time > now:
                continue
            elapsed_h = (
                now - rec.event_time
            ).total_seconds() / 3600
            infl = compute_influence(
                elapsed_h,
                rec.convergence_hours,
                decay_coefficient,
            )
            if infl > INFLUENCE_THRESHOLD:
                active.append((rec, infl))

        if not active:
            return FundamentalContext(
                upcoming_events=upcoming_dicts,
                has_high_impact_within_30min=(
                    high_impact_soon
                ),
            )

        # 方向性合成（重み付き平均）
        total_w = 0.0
        w_bias = 0.0
        w_surprise = 0.0
        for rec, infl in active:
            w = infl * IMPACT_WEIGHT.get(
                rec.impact, 0.3,
            )
            w_bias += rec.direction_bias * w
            w_surprise += rec.surprise_score * w
            total_w += w

        direction_bias = 0.0
        surprise_score = 0.0
        if total_w > 0:
            direction_bias = max(
                -1.0, min(1.0, w_bias / total_w)
            )
            surprise_score = max(
                -1.0, min(1.0, w_surprise / total_w)
            )

        # ボラティリティ合成（通常イベントのmax）
        normal_vols = [
            1.0 + (rec.expected_volatility - 1.0) * infl
            for rec, infl in active
            if not rec.is_holiday
        ]
        volatility_multiplier = (
            max(normal_vols) if normal_vols else 1.0
        )

        # 流動性合成（休日イベントのmin）
        liquidity_factor = 1.0
        is_holiday = False
        for rec, infl in active:
            if rec.is_holiday:
                is_holiday = True
                liq = (
                    rec.expected_volatility * infl
                    + (1.0 - infl)
                )
                liquidity_factor = min(
                    liquidity_factor, liq,
                )

        # 注意度合成
        event_caution_level = max(
            rec.trade_caution_level
            for rec, _ in active
        )

        # 収束進捗
        convergence_progress = min(
            1.0 - infl for _, infl in active
        )

        return FundamentalContext(
            has_high_impact_within_30min=high_impact_soon,
            event_caution_level=event_caution_level,
            is_holiday=is_holiday,
            liquidity_factor=liquidity_factor,
            volatility_multiplier=volatility_multiplier,
            active_event_count=len(active),
            direction_bias=direction_bias,
            surprise_score=surprise_score,
            convergence_progress=convergence_progress,
            upcoming_events=upcoming_dicts,
        )

    # --------------------------------------------------
    # DB記憶操作（従来ロジック）
    # --------------------------------------------------

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
        self,
        symbol: str,
        now: datetime,
        window_minutes: int = 60,
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
            from autotrader.adapters.database.repositories import (  # noqa: PLC0415
                MarketMemoryRepository,
            )

            with self._session_factory() as session:
                repo = MarketMemoryRepository(session)
                records = repo.get_active(
                    symbol, memory_type, now,
                )
                if not records:
                    return 0.0, "記憶なし"

                latest = records[0]
                score = latest.direction_score
                summary = latest.summary or "データなし"
                return score, summary

        except Exception as e:
            logger.debug(
                "[FundamentalMemory] "
                "スコア取得エラー: %s",
                e,
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
        ttl = _TTL_MAP.get(
            memory_type, timedelta(days=1),
        )
        now = datetime.now(timezone.utc)
        valid_until = now + ttl

        try:
            from autotrader.adapters.database.repositories import (  # noqa: PLC0415
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
                    "[FundamentalMemory] %s記録: "
                    "%s score=%+.2f TTL=%s",
                    memory_type,
                    symbol,
                    direction_score,
                    ttl,
                )
        except Exception as e:
            logger.error(
                "[FundamentalMemory] "
                "DB書き込みエラー: %s",
                e,
            )
