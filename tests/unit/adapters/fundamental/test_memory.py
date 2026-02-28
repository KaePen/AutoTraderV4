"""FundamentalMemoryService テスト（SQLiteインメモリDB）"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autotrader.adapters.database.models import Base
from autotrader.adapters.fundamental.deterministic_event_analyzer import (
    DeterministicEventAnalyzer,
)
from autotrader.adapters.fundamental.memory import (
    FundamentalMemoryService,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventLLMRecord,
    EventSource,
    ImpactLevel,
    FundamentalContext,
)


@pytest.fixture
def db_engine():
    """テスト用SQLiteインメモリエンジン"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def session_factory(db_engine):
    """テスト用セッションファクトリー"""
    SessionLocal = sessionmaker(bind=db_engine)

    @contextmanager
    def get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    return get_session


@pytest.fixture
def memory_service(session_factory):
    """テスト用FundamentalMemoryService"""
    return FundamentalMemoryService(
        session_factory=session_factory,
        event_guard_minutes=30,
    )


def make_event(
    impact: ImpactLevel = ImpactLevel.HIGH,
    minutes_from_now: float = 15.0,
) -> EconomicEvent:
    """テスト用経済イベント生成"""
    now = datetime.now(timezone.utc)
    return EconomicEvent(
        event_id=f"test_{int(minutes_from_now)}",
        event_time=now + timedelta(minutes=minutes_from_now),
        currency="USD",
        event_name="NFP",
        impact=impact,
        source=EventSource.MT5,
        fetched_at=now,
    )


class TestFundamentalMemoryService:
    """FundamentalMemoryServiceのテスト"""

    def test_write_macro_bias_and_read(self, memory_service):
        """マクロバイアスを書き込んで読み取れる"""
        memory_service.write_macro_bias(
            symbol="USDJPY",
            direction_score=0.7,
            confidence=0.8,
            summary="上昇バイアスあり",
        )
        now = datetime.now(timezone.utc)
        ctx = memory_service.get_context_for_llm("USDJPY", now)
        assert ctx.macro_bias_score == pytest.approx(0.7)
        assert ctx.macro_bias_summary == "上昇バイアスあり"

    def test_write_post_event_bias(self, memory_service):
        """指標後バイアスを書き込んで読み取れる"""
        memory_service.write_post_event_bias(
            symbol="USDJPY",
            direction_score=-0.5,
            confidence=0.7,
            summary="NFPショック下降",
            source_event="NFP",
        )
        now = datetime.now(timezone.utc)
        ctx = memory_service.get_context_for_llm("USDJPY", now)
        assert ctx.post_event_bias_score == pytest.approx(-0.5)

    def test_neutral_context_on_empty_db(self, memory_service):
        """DBが空の場合はニュートラルコンテキスト"""
        now = datetime.now(timezone.utc)
        ctx = memory_service.get_context_for_llm("USDJPY", now)
        assert ctx.macro_bias_score == 0.0
        assert ctx.post_event_bias_score == 0.0
        assert ctx.has_high_impact_within_30min is False

    def test_high_impact_detection(self, session_factory):
        """30分以内の高インパクト指標検出"""
        high_impact_event = make_event(
            impact=ImpactLevel.HIGH, minutes_from_now=20.0
        )
        service = FundamentalMemoryService(
            session_factory=session_factory,
            event_guard_minutes=30,
            cached_events_getter=lambda: [high_impact_event],
        )
        now = datetime.now(timezone.utc)
        ctx = service.get_context_for_llm("USDJPY", now)
        assert ctx.has_high_impact_within_30min is True

    def test_no_high_impact_when_medium_only(self, session_factory):
        """MEDIUMのみの場合は高インパクト検出しない"""
        medium_event = make_event(
            impact=ImpactLevel.MEDIUM, minutes_from_now=15.0
        )
        service = FundamentalMemoryService(
            session_factory=session_factory,
            event_guard_minutes=30,
            cached_events_getter=lambda: [medium_event],
        )
        now = datetime.now(timezone.utc)
        ctx = service.get_context_for_llm("USDJPY", now)
        assert ctx.has_high_impact_within_30min is False

    def test_no_high_impact_when_event_too_far(self, session_factory):
        """60分後のイベントは30分ガード対象外"""
        far_event = make_event(
            impact=ImpactLevel.HIGH, minutes_from_now=60.0
        )
        service = FundamentalMemoryService(
            session_factory=session_factory,
            event_guard_minutes=30,
            cached_events_getter=lambda: [far_event],
        )
        now = datetime.now(timezone.utc)
        ctx = service.get_context_for_llm("USDJPY", now)
        assert ctx.has_high_impact_within_30min is False

    def test_upcoming_events_in_context(self, session_factory):
        """直近イベントがコンテキストに含まれる"""
        event = make_event(
            impact=ImpactLevel.MEDIUM, minutes_from_now=30.0
        )
        service = FundamentalMemoryService(
            session_factory=session_factory,
            event_guard_minutes=30,
            cached_events_getter=lambda: [event],
        )
        now = datetime.now(timezone.utc)
        ctx = service.get_context_for_llm("USDJPY", now)
        assert len(ctx.upcoming_events) >= 1

    def test_context_for_llm_returns_fundamental_context(
        self, memory_service
    ):
        """get_context_for_llmがFundamentalContextを返す"""
        now = datetime.now(timezone.utc)
        ctx = memory_service.get_context_for_llm("USDJPY", now)
        assert isinstance(ctx, FundamentalContext)

    def test_fundamental_context_neutral(self):
        """FundamentalContext.neutral()がデフォルト値を返す"""
        ctx = FundamentalContext.neutral()
        assert ctx.macro_bias_score == 0.0
        assert ctx.has_high_impact_within_30min is False
        assert ctx.upcoming_events == []

    def test_to_prompt_section_contains_scores(self, memory_service):
        """プロンプトセクションにPhase 2bフィールドが含まれる"""
        now = datetime.now(timezone.utc)
        ctx = memory_service.get_context_for_llm("USDJPY", now)
        section = ctx.to_prompt_section()
        # Phase 2b フォーマット確認
        assert "方向バイアス" in section
        assert "流動性" in section
        assert "ボラ倍率" in section
        assert "注意度" in section


class TestPhase2EventSynthesis:
    """Phase 2: リアルタイムイベント分析・合成テスト"""

    @pytest.fixture
    def analyzer(self):
        """テスト用DeterministicEventAnalyzer"""
        return DeterministicEventAnalyzer()

    @pytest.fixture
    def analyzer_service(self, session_factory, analyzer):
        """analyzer付きFundamentalMemoryService"""
        now = datetime.now(timezone.utc)
        # 1時間前に発表済みの高インパクトイベント
        nfp_event = EconomicEvent(
            event_id="nfp_001",
            event_time=now - timedelta(hours=1),
            currency="USD",
            event_name="Non-Farm Employment Change",
            impact=ImpactLevel.HIGH,
            source=EventSource.MT5,
            fetched_at=now,
            actual=300.0,
            forecast=200.0,
            previous=180.0,
        )
        return FundamentalMemoryService(
            session_factory=session_factory,
            event_guard_minutes=30,
            cached_events_getter=lambda: [nfp_event],
            analyzer=analyzer,
        )

    def test_analyzer_produces_phase2_fields(
        self, analyzer_service
    ):
        """analyzer 付きサービスが Phase 2 フィールドを返す"""
        now = datetime.now(timezone.utc)
        ctx = analyzer_service.get_context_for_llm(
            "USDJPY", now
        )
        assert isinstance(ctx, FundamentalContext)
        # Phase 2 フィールドが設定されている
        assert ctx.direction_bias != 0.0
        assert ctx.surprise_score != 0.0
        assert ctx.active_event_count >= 1
        assert ctx.convergence_progress < 1.0

    def test_analyzer_caches_events(
        self, analyzer_service
    ):
        """分析済みイベントがキャッシュされる"""
        now = datetime.now(timezone.utc)
        # 1回目
        analyzer_service.get_context_for_llm(
            "USDJPY", now
        )
        assert "nfp_001" in (
            analyzer_service._analyzed_event_ids
        )
        assert len(
            analyzer_service._event_records.get(
                "USDJPY", []
            )
        ) == 1

        # 2回目（重複分析しない）
        analyzer_service.get_context_for_llm(
            "USDJPY", now
        )
        assert len(
            analyzer_service._event_records.get(
                "USDJPY", []
            )
        ) == 1

    def test_no_analyzer_fallback_to_db(
        self, session_factory
    ):
        """analyzer なしでは従来のDB記憶ベースに戻る"""
        service = FundamentalMemoryService(
            session_factory=session_factory,
            event_guard_minutes=30,
        )
        now = datetime.now(timezone.utc)
        ctx = service.get_context_for_llm("USDJPY", now)
        # Phase 2 フィールドはデフォルト
        assert ctx.direction_bias == 0.0
        assert ctx.active_event_count == 0
        assert ctx.convergence_progress == 1.0

    def test_volatility_multiplier_gt_1(
        self, analyzer_service
    ):
        """HIGH イベントの volatility_multiplier > 1.0"""
        now = datetime.now(timezone.utc)
        ctx = analyzer_service.get_context_for_llm(
            "USDJPY", now
        )
        assert ctx.volatility_multiplier > 1.0

    def test_event_caution_level(
        self, analyzer_service
    ):
        """HIGH イベントの event_caution_level >= 1"""
        now = datetime.now(timezone.utc)
        ctx = analyzer_service.get_context_for_llm(
            "USDJPY", now
        )
        assert ctx.event_caution_level >= 1

    def test_unreleased_events_not_analyzed(
        self, session_factory, analyzer
    ):
        """未発表イベントは分析されない"""
        now = datetime.now(timezone.utc)
        # 未来の未発表イベント
        future_event = EconomicEvent(
            event_id="future_001",
            event_time=now + timedelta(hours=2),
            currency="USD",
            event_name="Federal Funds Rate",
            impact=ImpactLevel.HIGH,
            source=EventSource.MT5,
            fetched_at=now,
            actual=None,
            forecast=5.25,
            previous=5.0,
        )
        service = FundamentalMemoryService(
            session_factory=session_factory,
            event_guard_minutes=30,
            cached_events_getter=lambda: [future_event],
            analyzer=analyzer,
        )
        ctx = service.get_context_for_llm("USDJPY", now)
        # 未発表なので分析されない → デフォルト値
        assert ctx.direction_bias == 0.0
        assert ctx.active_event_count == 0

    def test_holiday_event_reduces_liquidity(
        self, session_factory, analyzer
    ):
        """休日イベントで流動性が低下する"""
        now = datetime.now(timezone.utc)
        holiday = EconomicEvent(
            event_id="holiday_001",
            event_time=now - timedelta(hours=2),
            currency="USD",
            event_name="Bank Holiday",
            impact=ImpactLevel.LOW,
            source=EventSource.MT5,
            fetched_at=now,
            actual=0.0,
            forecast=None,
            previous=None,
        )
        service = FundamentalMemoryService(
            session_factory=session_factory,
            event_guard_minutes=30,
            cached_events_getter=lambda: [holiday],
            analyzer=analyzer,
        )
        ctx = service.get_context_for_llm("USDJPY", now)
        assert ctx.is_holiday is True
        assert ctx.liquidity_factor < 1.0
