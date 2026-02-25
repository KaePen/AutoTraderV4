"""Phase 2a: FundamentalContext スキーマテスト"""

from __future__ import annotations

from autotrader.adapters.fundamental.schemas import (
    FundamentalContext,
)


class TestFundamentalContextPhase2:
    """Phase 2 FundamentalContext のテスト"""

    def test_neutral_returns_defaults(self) -> None:
        """neutral() は全フィールドがデフォルト値"""
        ctx = FundamentalContext.neutral()
        assert ctx.has_high_impact_within_30min is False
        assert ctx.event_caution_level == 0
        assert ctx.is_holiday is False
        assert ctx.liquidity_factor == 1.0
        assert ctx.volatility_multiplier == 1.0
        assert ctx.active_event_count == 0
        assert ctx.direction_bias == 0.0
        assert ctx.surprise_score == 0.0
        assert ctx.convergence_progress == 1.0
        assert ctx.upcoming_events == []

    def test_neutral_backward_compat_fields(self) -> None:
        """neutral() の後方互換フィールドもデフォルト値"""
        ctx = FundamentalContext.neutral()
        assert ctx.macro_bias_score == 0.0
        assert ctx.macro_bias_summary == ""
        assert ctx.post_event_bias_score == 0.0
        assert ctx.post_event_summary == ""
        assert ctx.sentiment_score == 0.0

    def test_backward_compat_construction(self) -> None:
        """旧コード（memory.py等）からの名前付き引数構築"""
        ctx = FundamentalContext(
            macro_bias_score=0.5,
            macro_bias_summary="test summary",
            post_event_bias_score=-0.3,
            post_event_summary="post event",
            sentiment_score=0.1,
            upcoming_events=[{"name": "NFP"}],
            has_high_impact_within_30min=True,
        )
        assert ctx.macro_bias_score == 0.5
        assert ctx.macro_bias_summary == "test summary"
        assert ctx.has_high_impact_within_30min is True
        # Phase 2 フィールドはデフォルト値
        assert ctx.event_caution_level == 0
        assert ctx.liquidity_factor == 1.0

    def test_phase2_construction(self) -> None:
        """Phase 2 フィールドの構築"""
        ctx = FundamentalContext(
            event_caution_level=2,
            is_holiday=True,
            liquidity_factor=0.3,
            volatility_multiplier=1.5,
            active_event_count=3,
            direction_bias=0.7,
            surprise_score=-0.5,
            convergence_progress=0.2,
        )
        assert ctx.event_caution_level == 2
        assert ctx.is_holiday is True
        assert ctx.liquidity_factor == 0.3
        assert ctx.volatility_multiplier == 1.5
        assert ctx.active_event_count == 3
        assert ctx.direction_bias == 0.7
        assert ctx.surprise_score == -0.5
        assert ctx.convergence_progress == 0.2

    def test_to_prompt_section_phase2(self) -> None:
        """to_prompt_section() が新フィールドを含む"""
        ctx = FundamentalContext(
            direction_bias=0.5,
            liquidity_factor=0.3,
            volatility_multiplier=1.5,
            event_caution_level=2,
            is_holiday=True,
            has_high_impact_within_30min=True,
        )
        text = ctx.to_prompt_section()
        assert "方向バイアス: +0.50" in text
        assert "流動性: 0.30" in text
        assert "ボラ倍率: 1.50" in text
        assert "注意度: 2" in text
        assert "休日影響あり" in text
        assert "WARNING" in text

    def test_frozen_immutable(self) -> None:
        """frozen dataclass は変更不可"""
        ctx = FundamentalContext.neutral()
        try:
            ctx.event_caution_level = 2  # type: ignore
            raise AssertionError("Should have raised")
        except AttributeError:
            pass  # 期待通り
