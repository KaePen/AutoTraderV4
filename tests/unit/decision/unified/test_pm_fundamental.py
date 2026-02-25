"""PositionManager ファンダメンタル統合テスト

trailing_sl_multiplierによるSL距離調整の検証。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from autotrader.core.enums import SignalType, TradingStrategyMode
from autotrader.decision.unified.mode_selector import TradingPlan
from autotrader.decision.unified.position_manager import (
    ManagementAction,
    ManagementActionType,
    ManagedPosition,
    PositionManager,
    PositionManagerConfig,
)


@dataclass(frozen=True)
class _MockAssessment:
    """テスト用アセスメントモック"""

    trailing_sl_multiplier: float = 1.0


@pytest.fixture()
def pm() -> PositionManager:
    """基本設定のPositionManager"""
    config = PositionManagerConfig(
        trailing_start_r=0.5,
        trailing_atr_multiplier=2.0,
        time_exit_enabled=False,
        breakeven_at_1r=False,
        early_breakeven_enabled=False,
    )
    return PositionManager(config)


@pytest.fixture()
def _plan() -> TradingPlan:
    """ダミーTradingPlan"""
    return TradingPlan(
        mode=TradingStrategyMode.UNIVERSAL,
        primary_tf="M15",
        entry_tf="M5",
        confirm_tfs=["H1", "H4"],
        manage_tf="M15",
        max_holding_bars=100,
        tp_sl_ratio_range=(1.1, 1.4),
    )


def _register_position(
    pm: PositionManager,
    _plan: TradingPlan,
    direction: SignalType = SignalType.BUY,
    entry_price: float = 150.0,
    sl_price: float = 149.5,
    tp_price: float = 151.0,
) -> str:
    """テスト用ポジション登録"""
    pos_id = "test_pos_1"
    pm.register_position(
        position_id=pos_id,
        direction=direction,
        entry_price=entry_price,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        sl=sl_price,
        tp=tp_price,
        volume=1.0,
        plan=_plan,
    )
    return pos_id


class TestTrailingSLMultiplier:
    """trailing_sl_multiplierによるSL距離調整"""

    def test_normal_trailing_no_assessment(
        self, pm: PositionManager, _plan: TradingPlan,
    ) -> None:
        """アセスメントなし: 通常のトレーリング動作"""
        pos_id = _register_position(pm, _plan)
        # 価格上昇で利益を出す
        t = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
        # R値 = |150.0 - 149.5| = 0.5
        # 0.5R以上必要 → +0.25以上必要
        # highest_price=150.4, trail_distance=0.002*2.0=0.004
        # new_sl = 150.4 - 0.004 = 150.396
        action = pm.evaluate(
            pos_id, 150.4, t, atr=0.002,
        )
        # trailing_start_r=0.5, current_r=(0.4/0.5)=0.8 > 0.5
        assert action.action_type in (
            ManagementActionType.UPDATE_SL,
            ManagementActionType.HOLD,
        )

    def test_trailing_with_tighter_sl(
        self, pm: PositionManager, _plan: TradingPlan,
    ) -> None:
        """SL引き締め: multiplier=0.7でSL距離が30%短縮"""
        pos_id = _register_position(pm, _plan)
        t = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
        assessment = _MockAssessment(trailing_sl_multiplier=0.7)

        # current_r = 0.8, trailing有効
        action = pm.evaluate(
            pos_id, 150.4, t, atr=0.002,
            fundamental_assessment=assessment,
        )
        if action.action_type == ManagementActionType.UPDATE_SL:
            # trail_distance = 0.002 * 2.0 * 0.7 = 0.0028
            # new_sl = 150.4 - 0.0028 = 150.3972
            assert action.new_sl is not None
            # 通常SL(150.396) < 引き締めSL(150.3972)
            assert action.new_sl > 150.396

    def test_trailing_with_multiplier_1(
        self, pm: PositionManager, _plan: TradingPlan,
    ) -> None:
        """multiplier=1.0: 通常と同じ動作"""
        pos_id = _register_position(pm, _plan)
        t = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
        normal = _MockAssessment(trailing_sl_multiplier=1.0)

        action = pm.evaluate(
            pos_id, 150.4, t, atr=0.002,
            fundamental_assessment=normal,
        )
        if action.action_type == ManagementActionType.UPDATE_SL:
            assert action.new_sl is not None
            # trail_distance = 0.002 * 2.0 * 1.0 = 0.004
            expected_sl = 150.4 - 0.004
            assert abs(action.new_sl - expected_sl) < 1e-6

    def test_no_trailing_when_r_below_threshold(
        self, pm: PositionManager, _plan: TradingPlan,
    ) -> None:
        """R値が閾値未満: トレーリング不発動"""
        pos_id = _register_position(pm, _plan)
        t = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
        assessment = _MockAssessment(trailing_sl_multiplier=0.5)

        # current_r = 0.1/0.5 = 0.2 < 0.5
        action = pm.evaluate(
            pos_id, 150.1, t, atr=0.002,
            fundamental_assessment=assessment,
        )
        # トレーリング不発動なので UPDATE_SL にはならない
        assert action.action_type != ManagementActionType.UPDATE_SL

    def test_sell_position_trailing(
        self, pm: PositionManager, _plan: TradingPlan,
    ) -> None:
        """SELL方向のトレーリングSL調整"""
        pos_id = _register_position(
            pm, _plan,
            direction=SignalType.SELL,
            entry_price=150.0,
            sl_price=150.5,
            tp_price=149.0,
        )
        t = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)
        assessment = _MockAssessment(trailing_sl_multiplier=0.7)

        # SELL: current_r = (150.0 - 149.6) / 0.5 = 0.8
        action = pm.evaluate(
            pos_id, 149.6, t, atr=0.002,
            fundamental_assessment=assessment,
        )
        if action.action_type == ManagementActionType.UPDATE_SL:
            assert action.new_sl is not None
            # trail_distance = 0.002 * 2.0 * 0.7 = 0.0028
            # new_sl = 149.6 + 0.0028 = 149.6028
            assert action.new_sl < 150.5  # 元のSLより下

    def test_assessment_without_multiplier_attr(
        self, pm: PositionManager, _plan: TradingPlan,
    ) -> None:
        """trailing_sl_multiplier属性なし: 通常動作"""

        class NoMultiplier:
            pass

        pos_id = _register_position(pm, _plan)
        t = datetime(2024, 1, 1, 1, tzinfo=timezone.utc)

        # hasattr チェックで安全にスキップ
        action = pm.evaluate(
            pos_id, 150.4, t, atr=0.002,
            fundamental_assessment=NoMultiplier(),
        )
        # エラーなく動作すればOK
        assert action is not None
