"""コンセンサス逆転exitのテスト"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.core.enums import SignalType, TradingStrategyMode
from autotrader.decision.unified.mode_selector import TradingPlan
from autotrader.decision.unified.position_manager import (
    ManagedPosition,
    ManagementAction,
    ManagementActionType,
    PositionManager,
    PositionManagerConfig,
)


def _make_pm(
    consensus_exit_enabled: bool = True,
    threshold: float = 6.0,
    own_max: float = 3.0,
    loss_only: bool = False,
) -> PositionManager:
    """テスト用PositionManager"""
    config = PositionManagerConfig(
        consensus_exit_enabled=consensus_exit_enabled,
        consensus_exit_threshold=threshold,
        consensus_exit_own_max=own_max,
        consensus_exit_loss_only=loss_only,
    )
    return PositionManager(config)


def _make_plan() -> TradingPlan:
    """テスト用TradingPlan"""
    return TradingPlan(
        mode=TradingStrategyMode.UNIVERSAL,
        primary_tf="M15",
        entry_tf="M5",
        confirm_tfs=["H1", "H4"],
        manage_tf="M15",
        max_holding_bars=32,
        tp_sl_ratio_range=(1.1, 1.4),
    )


def _register_buy_pos(
    pm: PositionManager,
    pos_id: str = "test_pos",
    entry: float = 150.0,
    sl: float = 149.8,
    tp: float = 150.4,
) -> str:
    """BUYポジションを登録"""
    pm.register_position(
        position_id=pos_id,
        direction=SignalType.BUY,
        entry_price=entry,
        sl=sl,
        tp=tp,
        volume=1.0,
        entry_time=datetime(2024, 1, 1, 12, 0),
        plan=_make_plan(),
    )
    return pos_id


class TestConsensusExit:
    """コンセンサス逆転exitテスト"""

    def test_exit_when_consensus_reverses(self) -> None:
        """逆方向スコアが高く自方向が低い時にexit"""
        pm = _make_pm(threshold=6.0, own_max=3.0)
        pid = _register_buy_pos(pm)

        # BUYポジション保有中、SELL方向スコアが優勢
        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,  # 含み損
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=2.0,   # 自方向弱い
            sell_score=8.0,  # 逆方向強い
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_no_exit_when_own_score_high(self) -> None:
        """自方向スコアが高い場合はexit不要"""
        pm = _make_pm(threshold=6.0, own_max=3.0)
        pid = _register_buy_pos(pm)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=5.0,   # 自方向まだ強い
            sell_score=8.0,  # 逆方向も強い
        )
        # own_score > own_max なのでexit不発動
        assert action.action_type == ManagementActionType.HOLD

    def test_no_exit_when_opp_score_low(self) -> None:
        """逆方向スコアが低い場合はexit不要"""
        pm = _make_pm(threshold=6.0, own_max=3.0)
        pid = _register_buy_pos(pm)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=2.0,   # 自方向弱い
            sell_score=4.0,  # 逆方向もまだ弱い
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_no_exit_when_disabled(self) -> None:
        """無効時はexit不発動"""
        pm = _make_pm(consensus_exit_enabled=False)
        pid = _register_buy_pos(pm)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=1.0,
            sell_score=10.0,
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_no_exit_when_scores_zero(self) -> None:
        """スコアが両方0の場合はexit不発動"""
        pm = _make_pm()
        pid = _register_buy_pos(pm)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=0.0,
            sell_score=0.0,
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_loss_only_mode_blocks_profit_exit(self) -> None:
        """含み損限定モードで含み益時はexit不発動"""
        pm = _make_pm(loss_only=True)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        action = pm.evaluate(
            position_id=pid,
            current_price=150.3,  # 含み益
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=1.0,
            sell_score=9.0,
        )
        assert action.action_type != ManagementActionType.FULL_CLOSE

    def test_loss_only_mode_allows_loss_exit(self) -> None:
        """含み損限定モードで含み損時はexit発動"""
        pm = _make_pm(loss_only=True)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,  # 含み損
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=1.0,
            sell_score=9.0,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_sell_position_consensus_exit(self) -> None:
        """SELLポジションでもコンセンサス逆転exit動作"""
        pm = _make_pm(threshold=6.0, own_max=3.0)
        pm.register_position(
            position_id="sell_pos",
            direction=SignalType.SELL,
            entry_price=150.0,
            sl=150.2,
            tp=149.6,
            volume=1.0,
            entry_time=datetime(2024, 1, 1, 12, 0),
            plan=_make_plan(),
        )

        action = pm.evaluate(
            position_id="sell_pos",
            current_price=150.15,  # 含み損
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=8.0,   # BUY方向が強い（SELLの逆）
            sell_score=2.0,  # SELL方向が弱い（自方向）
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE
