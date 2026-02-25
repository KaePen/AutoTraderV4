"""FundamentalRiskAssessor テスト

リスクレベル計算・方向バイアス評価・イベントフェーズ判定の検証。
"""

from __future__ import annotations

import pytest

from autotrader.adapters.fundamental.schemas import (
    FundamentalContext,
    FundamentalMemory,
    FundamentalMemorySnapshot,
)
from autotrader.decision.unified.fundamental_assessor import (
    EventPhase,
    FundamentalAssessment,
    FundamentalAssessorConfig,
    FundamentalRiskAssessor,
    RiskCategory,
)


@pytest.fixture()
def assessor() -> FundamentalRiskAssessor:
    """デフォルト設定のアセッサー"""
    return FundamentalRiskAssessor()


@pytest.fixture()
def neutral_memory() -> FundamentalMemorySnapshot:
    """ニュートラルなメモリスナップショット"""
    return FundamentalMemory().snapshot()


class TestRiskLevelCalculation:
    """リスクレベル計算テスト"""

    def test_normal_conditions(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """通常条件ではNORMAL"""
        ctx = FundamentalContext()
        result = assessor.assess(ctx, neutral_memory)
        assert result.risk_category == RiskCategory.NORMAL
        assert result.risk_level < 0.3

    def test_nfp_pre_event_scenario(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """NFP発表前シナリオ → CAUTION以上

        max()で相関要因を統合するため、単一イベントでは
        event_risk=0.35 + pre_event_risk=0.15 = 0.50。
        追加のイベント密集があればHIGHに到達する。
        """
        ctx = FundamentalContext(
            event_caution_level=1,
            volatility_multiplier=1.8,
            convergence_progress=0.0,
            active_event_count=1,
            has_high_impact_within_30min=True,
        )
        result = assessor.assess(ctx, neutral_memory)
        assert result.risk_category in (
            RiskCategory.CAUTION,
            RiskCategory.HIGH,
            RiskCategory.BLOCK,
        )
        assert result.risk_level >= 0.3
        assert result.event_phase == EventPhase.PRE_EVENT

    def test_post_event_high_surprise(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """指標発表直後（大サプライズ）→ CAUTION以上+高dominance

        max()統合でevent_risk=0.35、閾値調整でHOLDを実現。
        """
        ctx = FundamentalContext(
            event_caution_level=1,
            volatility_multiplier=2.5,
            convergence_progress=0.1,
            surprise_score=0.8,
            direction_bias=0.7,
            active_event_count=1,
        )
        result = assessor.assess(ctx, neutral_memory)
        assert result.risk_level >= 0.3
        assert result.event_phase == EventPhase.POST_EVENT
        assert result.event_dominance > 0.5
        # 逆行シグナルに対する閾値上げが大きいことを確認
        adj = result.get_threshold_adjustment(
            signal_direction=-1.0,
        )
        assert adj > 2.0, (
            f"大サプライズ後の逆行フィルターが弱い: {adj:.1f}"
        )

    def test_normal_pmi_event(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """通常指標（PMI等）→ NORMAL"""
        ctx = FundamentalContext(
            event_caution_level=0,
            volatility_multiplier=1.2,
            convergence_progress=0.7,
            active_event_count=1,
        )
        result = assessor.assess(ctx, neutral_memory)
        assert result.risk_category == RiskCategory.NORMAL

    def test_holiday_scenario(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """祝日シナリオ → ペナルティは適度"""
        ctx = FundamentalContext(
            is_holiday=True,
            liquidity_factor=0.3,
        )
        result = assessor.assess(ctx, neutral_memory)
        # 祝日のみならCAUTION程度
        assert result.risk_level > 0.1
        assert result.risk_level < 0.6

    def test_correlated_factors_use_max_not_sum(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """相関要因はmax()で統合（破綻2修正）"""
        # 単一イベントが原因の3要因
        ctx_single = FundamentalContext(
            event_caution_level=1,
            volatility_multiplier=2.0,
            convergence_progress=0.1,
            active_event_count=1,
        )
        # 3つの別イベントが同時
        ctx_multi = FundamentalContext(
            event_caution_level=1,
            volatility_multiplier=1.3,
            convergence_progress=0.5,
            active_event_count=4,
        )
        r_single = assessor.assess(ctx_single, neutral_memory)
        r_multi = assessor.assess(ctx_multi, neutral_memory)

        # マルチイベントの方がリスクが高い場合もある
        # 少なくとも単一イベントが過大評価されていないことを確認
        assert r_single.risk_level < 0.8, (
            f"単一イベントのリスク{r_single.risk_level:.2f}が"
            f"過大（相関要因の二重計上）"
        )


class TestEventPhase:
    """イベントフェーズ判定テスト（破綻3修正）"""

    def test_pre_event_phase(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """30分以内にイベント → PRE_EVENT"""
        ctx = FundamentalContext(
            has_high_impact_within_30min=True,
            event_caution_level=1,
        )
        result = assessor.assess(ctx, neutral_memory)
        assert result.event_phase == EventPhase.PRE_EVENT

    def test_post_event_phase(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """発表直後 (convergence < 0.3) → POST_EVENT"""
        ctx = FundamentalContext(
            event_caution_level=1,
            convergence_progress=0.1,
            surprise_score=0.5,
        )
        result = assessor.assess(ctx, neutral_memory)
        assert result.event_phase == EventPhase.POST_EVENT

    def test_converging_phase(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """収束中 (0.3-0.8) → CONVERGING"""
        ctx = FundamentalContext(
            event_caution_level=1,
            convergence_progress=0.5,
        )
        result = assessor.assess(ctx, neutral_memory)
        assert result.event_phase == EventPhase.CONVERGING

    def test_normal_phase(
        self, assessor: FundamentalRiskAssessor,
        neutral_memory: FundamentalMemorySnapshot,
    ) -> None:
        """イベントなし → NORMAL"""
        ctx = FundamentalContext()
        result = assessor.assess(ctx, neutral_memory)
        assert result.event_phase == EventPhase.NORMAL


class TestEffectiveBias:
    """統合バイアス計算テスト"""

    def test_post_event_bias_dominated_by_event(
        self, assessor: FundamentalRiskAssessor,
    ) -> None:
        """イベント直後はイベントバイアスが支配的"""
        ctx = FundamentalContext(
            direction_bias=0.8,
            surprise_score=0.7,
            convergence_progress=0.1,
            event_caution_level=1,
        )
        # メモリにはニュースの逆方向バイアス
        mem = FundamentalMemory()
        mem.news_bias = -0.3
        mem.news_strength = 0.5
        snap = mem.snapshot()

        result = assessor.assess(ctx, snap)
        # イベント方向に寄っている
        assert result.effective_bias > 0.3

    def test_no_event_memory_dominates(
        self, assessor: FundamentalRiskAssessor,
    ) -> None:
        """イベントなし時はメモリバイアスが支配的"""
        ctx = FundamentalContext()  # ニュートラル
        mem = FundamentalMemory()
        mem.event_bias = 0.5
        mem.event_strength = 0.7
        snap = mem.snapshot()

        result = assessor.assess(ctx, snap)
        # メモリ方向に寄っている
        assert result.effective_bias > 0.2


class TestDisagreement:
    """矛盾検知テスト（破綻6修正）"""

    def test_disagreement_increases_risk(
        self, assessor: FundamentalRiskAssessor,
    ) -> None:
        """イベントとニュースの矛盾でリスク増加"""
        ctx = FundamentalContext()
        # 矛盾するメモリ
        mem = FundamentalMemory()
        mem.event_bias = 0.8
        mem.event_strength = 0.5
        mem.news_bias = -0.5
        mem.news_strength = 0.5
        snap_disagree = mem.snapshot()

        # 一致するメモリ
        mem2 = FundamentalMemory()
        mem2.event_bias = 0.6
        mem2.event_strength = 0.5
        mem2.news_bias = 0.5
        mem2.news_strength = 0.5
        snap_agree = mem2.snapshot()

        r_disagree = assessor.assess(ctx, snap_disagree)
        r_agree = assessor.assess(ctx, snap_agree)

        assert r_disagree.risk_level > r_agree.risk_level
        assert r_disagree.bias_confidence < r_agree.bias_confidence


class TestConvictionBoost:
    """コンビクションブーストテスト"""

    def test_threshold_adjustment_for_aligned_signal(
        self, assessor: FundamentalRiskAssessor,
    ) -> None:
        """ファンダ一致時は閾値調整が負（ブースト）"""
        ctx = FundamentalContext(
            direction_bias=0.6,
            convergence_progress=0.8,
        )
        mem = FundamentalMemory()
        mem.event_bias = 0.5
        mem.event_strength = 0.6
        snap = mem.snapshot()

        result = assessor.assess(ctx, snap)
        # BUY方向のシグナルに対する閾値調整
        adj = result.get_threshold_adjustment(
            signal_direction=1.0,
        )
        # 一致方向 → 負の調整（閾値下げ = ブースト）
        assert adj < 0.0

    def test_threshold_adjustment_for_opposed_signal(
        self, assessor: FundamentalRiskAssessor,
    ) -> None:
        """ファンダ逆行時は閾値調整が正（フィルター）"""
        ctx = FundamentalContext(
            direction_bias=0.6,
            surprise_score=0.5,
            convergence_progress=0.2,
            event_caution_level=1,
        )
        mem = FundamentalMemory()
        mem.event_bias = 0.5
        mem.event_strength = 0.6
        snap = mem.snapshot()

        result = assessor.assess(ctx, snap)
        # SELL方向（逆行）のシグナルに対する閾値調整
        adj = result.get_threshold_adjustment(
            signal_direction=-1.0,
        )
        # 逆行方向 → 正の調整（閾値上げ）
        assert adj > 0.0

    def test_threshold_adjustment_capped(
        self, assessor: FundamentalRiskAssessor,
    ) -> None:
        """閾値調整に上限がある（破綻4修正）"""
        ctx = FundamentalContext(
            event_caution_level=1,
            volatility_multiplier=2.5,
            convergence_progress=0.05,
            surprise_score=0.9,
            direction_bias=0.9,
            active_event_count=3,
        )
        mem = FundamentalMemory()
        mem.event_bias = 0.8
        mem.event_strength = 0.8
        snap = mem.snapshot()

        result = assessor.assess(ctx, snap)
        adj = result.get_threshold_adjustment(
            signal_direction=-1.0,
        )
        # 上限を超えない
        assert adj <= 5.0
