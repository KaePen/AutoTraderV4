"""スプレッドコスト比フィルタのユニットテスト

W2: SizingStep内のspread/TP比率チェックをテスト。
SizingStepは最後に常にshould_abort=Trueでシグナルを返す設計のため、
ブロック時は_hold_with_analysisが呼ばれるかどうかで判定する。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from autotrader.constraint.soft_guard import SoftGuardResult
from autotrader.decision.unified.adaptive.overrides import (
    AdaptiveOverrides,
)
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.pipeline_pkg.pipeline import (
    PipelineContext,
    SizingStep,
)
from autotrader.decision.unified.scoring.consolidator import (
    ConsolidatedSignal,
)


def _make_sg_result(
    total_penalty: float = 0.0,
) -> SoftGuardResult:
    """テスト用SoftGuardResult"""
    return SoftGuardResult(total_penalty=total_penalty)


def _make_bot(
    *,
    spread_cost_ratio_enabled: bool = True,
    spread_cost_ratio_max: float = 0.15,
    spread_cost_ratio_block: float = 0.25,
    spread_pips: float = 3.0,
    tp_sl_ratio: float = 1.2,
    enable_position_sizing: bool = False,
) -> MagicMock:
    """テスト用Botモック"""
    config = UnifiedBotConfig(
        spread_cost_ratio_enabled=spread_cost_ratio_enabled,
        spread_cost_ratio_max=spread_cost_ratio_max,
        spread_cost_ratio_block=spread_cost_ratio_block,
        spread_pips=spread_pips,
        tp_sl_ratio=tp_sl_ratio,
        enable_position_sizing=enable_position_sizing,
        # レジーム関連デフォルト
        regime_breakout_enabled=False,
        dynamic_tp_enabled=False,
        vol_direction_enabled=False,
    )
    bot = MagicMock()
    bot.config = config
    bot._get_spread_pips = MagicMock(
        return_value=spread_pips,
    )
    # _hold_with_analysis はHOLD用シグナルを返す
    _hold_signal = MagicMock(spec=ConsolidatedSignal)
    _hold_signal._is_hold = True
    bot._hold_with_analysis = MagicMock(
        return_value=_hold_signal,
    )
    bot._flow_analyzer = None
    bot.position_sizer = MagicMock()
    bot.state = MagicMock()
    return bot


def _make_ctx(
    *,
    sl_pips: float = 20.0,
    sg_penalty: float = 0.0,
) -> PipelineContext:
    """テスト用PipelineContext（SizingStep入力用）"""
    from autotrader.core.enums import MarketRegime

    consensus = MagicMock()
    consensus.score = 20.0
    consensus.threshold = 18.0
    consensus.reasoning = "test"

    regime_result = MagicMock()
    regime_result.regime = MarketRegime.RANGE
    regime_result.volatility_direction = "stable"

    plan = MagicMock()
    plan.primary_tf = "M15"
    plan.get_recommended_tp_sl_ratio.return_value = 1.0

    tf_signals = {
        "M15": MagicMock(
            atr=0.002, sl_pips=sl_pips, tp_pips=30.0,
        ),
    }

    ctx = PipelineContext(
        current_time=pd.Timestamp("2023-06-01 10:00:00"),
    )
    ctx.consensus = consensus
    ctx.tf_signals = tf_signals
    ctx.plan = plan
    ctx.regime_result = regime_result
    ctx.htf_alignment = 0.3
    ctx.sg_result = _make_sg_result(sg_penalty)
    ctx.overrides = AdaptiveOverrides()
    ctx.fund_assessment = None
    return ctx


def _was_blocked_by_spread_cost(
    bot: MagicMock, result: PipelineContext,
) -> bool:
    """スプレッドコスト比でブロックされたかどうか"""
    if not bot._hold_with_analysis.called:
        return False
    call_args = bot._hold_with_analysis.call_args
    msg = call_args[0][0] if call_args[0] else ""
    return "スプレッドコスト比過大" in str(msg)


class TestSpreadCostRatioFilter:
    """スプレッドコスト比フィルタのテスト"""

    def test_disabled_no_effect(self):
        """無効時はスプレッドコスト比チェックされない"""
        bot = _make_bot(
            spread_cost_ratio_enabled=False,
            spread_pips=5.0,
        )
        ctx = _make_ctx(sl_pips=10.0)
        step = SizingStep()
        result = step.execute(ctx, bot)
        # スプレッドコスト比でブロックされない
        assert not _was_blocked_by_spread_cost(bot, result)
        # _get_spread_pipsは呼ばれない
        bot._get_spread_pips.assert_not_called()

    def test_ratio_below_max_passes(self):
        """比率がmax未満なら通過"""
        # tp_pips = 20 * 1.2 = 24
        # ratio = 2.0 / 24 = 0.083 < 0.15 → 通過
        bot = _make_bot(
            spread_cost_ratio_enabled=True,
            spread_pips=2.0,
        )
        ctx = _make_ctx(sl_pips=20.0)
        step = SizingStep()
        result = step.execute(ctx, bot)
        assert not _was_blocked_by_spread_cost(bot, result)
        # ペナルティ加算なし
        assert result.sg_result.total_penalty == 0.0

    def test_ratio_above_max_adds_penalty(self):
        """比率がmax以上block未満でペナルティ加算"""
        # tp_pips = 10 * 1.2 = 12
        # ratio = 2.0 / 12 = 0.1667 > 0.15, < 0.25
        # penalty = (0.1667 - 0.15) * 2.0 ≈ 0.0333
        bot = _make_bot(
            spread_cost_ratio_enabled=True,
            spread_pips=2.0,
        )
        ctx = _make_ctx(sl_pips=10.0)
        step = SizingStep()
        result = step.execute(ctx, bot)
        assert not _was_blocked_by_spread_cost(bot, result)
        assert result.sg_result.total_penalty > 0.0
        # 期待値: (2/12 - 0.15) * 2.0 ≈ 0.0333
        expected = (2.0 / 12.0 - 0.15) * 2.0
        assert abs(
            result.sg_result.total_penalty - expected
        ) < 0.01

    def test_ratio_above_block_aborts(self):
        """比率がblock以上で完全ブロック"""
        # tp_pips = 10 * 1.2 = 12
        # ratio = 3.0 / 12 = 0.25 >= 0.25 → ブロック
        bot = _make_bot(
            spread_cost_ratio_enabled=True,
            spread_pips=3.0,
        )
        ctx = _make_ctx(sl_pips=10.0)
        step = SizingStep()
        result = step.execute(ctx, bot)
        assert _was_blocked_by_spread_cost(bot, result)

    def test_high_spread_blocks(self):
        """スプレッド幅広でブロック確認"""
        # tp_pips = 10 * 1.2 = 12
        # ratio = 5.0 / 12 = 0.417 >= 0.25 → ブロック
        bot = _make_bot(
            spread_cost_ratio_enabled=True,
            spread_pips=5.0,
        )
        ctx = _make_ctx(sl_pips=10.0)
        step = SizingStep()
        result = step.execute(ctx, bot)
        assert _was_blocked_by_spread_cost(bot, result)

    def test_existing_penalty_accumulated(self):
        """既存ペナルティに加算される"""
        # tp_pips = 10 * 1.2 = 12
        # ratio = 2.0 / 12 = 0.1667 > 0.15
        bot = _make_bot(
            spread_cost_ratio_enabled=True,
            spread_pips=2.0,
        )
        ctx = _make_ctx(
            sl_pips=10.0, sg_penalty=0.1,
        )
        step = SizingStep()
        result = step.execute(ctx, bot)
        assert not _was_blocked_by_spread_cost(bot, result)
        # 0.1（既存） + コスト比ペナルティ
        assert result.sg_result.total_penalty > 0.1
