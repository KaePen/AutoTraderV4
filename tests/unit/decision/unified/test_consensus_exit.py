"""コンセンサス逆転exitのテスト"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.core.enums import SignalType
from autotrader.decision.unified.mode_selector import TradingPlan
from autotrader.decision.unified.risk.position_manager import (
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
        mode="UNIVERSAL",
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


def _make_no_be_pm(
    consensus_exit_enabled: bool = True,
    threshold: float = 6.0,
    own_max: float = 3.0,
    loss_only: bool = False,
    **kwargs: float | bool,
) -> PositionManager:
    """BE無効のテスト用PositionManager"""
    config = PositionManagerConfig(
        consensus_exit_enabled=consensus_exit_enabled,
        consensus_exit_threshold=threshold,
        consensus_exit_own_max=own_max,
        consensus_exit_loss_only=loss_only,
        # BE・stagnation関連を無効化
        be_enabled_modes=(),
        early_breakeven_enabled=False,
        early_breakeven_r=999.0,
        range_day_be_disabled=True,
        range_day_early_be_r=999.0,
        range_day_fast_be_enabled=False,
        range_day_insurance_enabled=False,
        range_day_half_r_partial_enabled=False,
        universal_half_r_enabled=False,
        # stagnation無効化（コンセンサステスト用）
        progressive_stagnation_enabled=False,
        stagnation_exit_minutes=9999.0,
        **kwargs,
    )
    return PositionManager(config)


class TestConsensusExit:
    """コンセンサス逆転exitテスト"""

    def test_exit_when_consensus_reverses(self) -> None:
        """逆方向スコアが高く自方向が低い時にexit"""
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0)
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
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0)
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
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0)
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
        pm = _make_no_be_pm(consensus_exit_enabled=False)
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
        pm = _make_no_be_pm()
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

    def test_profit_reversal_guard_triggers(self) -> None:
        """利益反転ガード: MFE到達後に利益低下で退出"""
        config = PositionManagerConfig(
            profit_reversal_enabled=True,
            profit_reversal_mfe_r=0.3,
            profit_reversal_drop_r=0.25,
            profit_reversal_max_r=0.05,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # MFE到達（0.5R = 150.10）
        pm.evaluate(
            position_id=pid,
            current_price=150.10,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 利益が戻る（0.0R = 150.00）
        action = pm.evaluate(
            position_id=pid,
            current_price=150.01,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # highest_r=0.5, current_r=0.05, drop=0.45 >= 0.25
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_profit_reversal_guard_no_trigger_low_mfe(self) -> None:
        """利益反転ガード: MFE未到達なら不発動"""
        config = PositionManagerConfig(
            profit_reversal_enabled=True,
            profit_reversal_mfe_r=0.3,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # MFE 0.15R（閾値0.3R未満）
        pm.evaluate(
            position_id=pid,
            current_price=150.03,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        action = pm.evaluate(
            position_id=pid,
            current_price=149.95,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_progressive_stagnation_stage1(self) -> None:
        """段階的STAGNATION: Stage1（60分+低MFE+含み損）"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_exit_minutes=120.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # 65分経過、含み損で停滞
        action = pm.evaluate(
            position_id=pid,
            current_price=149.97,  # -0.15R
            current_time=datetime(2024, 1, 1, 13, 5),
            atr=0.002,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_progressive_stagnation_no_trigger_positive_r(
        self,
    ) -> None:
        """段階的STAGNATION: 含み益なら不発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # 65分経過だが含み益
        action = pm.evaluate(
            position_id=pid,
            current_price=150.05,
            current_time=datetime(2024, 1, 1, 13, 5),
            atr=0.002,
        )
        assert action.action_type != ManagementActionType.FULL_CLOSE

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


class TestUniversalHalfR:
    """ユニバーサル0.5R部分利確テスト"""

    def test_universal_half_r_triggers(self) -> None:
        """0.5R到達時に部分利確+BE移動"""
        config = PositionManagerConfig(
            universal_half_r_enabled=True,
            universal_half_r_trigger=0.5,
            universal_half_r_ratio=0.25,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # 0.5R = 150.10到達
        action = pm.evaluate(
            position_id=pid,
            current_price=150.11,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.close_ratio == 0.25

    def test_universal_half_r_no_trigger_below(self) -> None:
        """0.5R未到達では不発動"""
        config = PositionManagerConfig(
            universal_half_r_enabled=True,
            universal_half_r_trigger=0.5,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        action = pm.evaluate(
            position_id=pid,
            current_price=150.08,  # 0.4R
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        assert action.action_type != ManagementActionType.PARTIAL_CLOSE

    def test_universal_half_r_disabled(self) -> None:
        """無効時は不発動"""
        config = PositionManagerConfig(
            universal_half_r_enabled=False,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        action = pm.evaluate(
            position_id=pid,
            current_price=150.15,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 0.5R部分利確は発動しない（1R未満なのでPARTIAL_CLOSEにならない）
        assert action.action_type != ManagementActionType.PARTIAL_CLOSE


# ==============================================================================
# 追加テスト: profit_reversal, progressive_stagnation, consensus_exit 境界値
# ==============================================================================


def _make_profit_reversal_pm(
    enabled: bool = True,
    mfe_r: float = 0.3,
    drop_r: float = 0.25,
    max_r: float = 0.05,
) -> PositionManager:
    """利益反転テスト用PM（BE無効）"""
    config = PositionManagerConfig(
        profit_reversal_enabled=enabled,
        profit_reversal_mfe_r=mfe_r,
        profit_reversal_drop_r=drop_r,
        profit_reversal_max_r=max_r,
        # BE・stagnation関連を無効化
        be_enabled_modes=(),
        early_breakeven_enabled=False,
        early_breakeven_r=999.0,
        range_day_be_disabled=True,
        range_day_early_be_r=999.0,
        range_day_fast_be_enabled=False,
        range_day_insurance_enabled=False,
        range_day_half_r_partial_enabled=False,
        universal_half_r_enabled=False,
        # stagnation無効化
        progressive_stagnation_enabled=False,
        stagnation_exit_minutes=9999.0,
        # consensus無効化（profit_reversalテスト用）
        consensus_exit_enabled=False,
        # トレーリングを高い閾値で無効化
        trailing_start_r=999.0,
    )
    return PositionManager(config)


class TestProfitReversalBoundary:
    """利益反転ガードの境界値テスト"""

    def test_mfe_exactly_at_threshold_triggers(self) -> None:
        """MFEが閾値ちょうどで発動する"""
        pm = _make_profit_reversal_pm(
            mfe_r=0.3, drop_r=0.25, max_r=0.05,
        )
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2, 0.3R = 0.06

        # MFE 0.3Rちょうど（150.0 + 0.3*0.2 = 150.06）
        pm.evaluate(
            position_id=pid,
            current_price=150.06,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 0.25R下落 + 現在R <= 0.05R
        # current_r = 0.05R → 150.01
        action = pm.evaluate(
            position_id=pid,
            current_price=150.01,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # drop = 0.3 - 0.05 = 0.25R >= 0.25R, current_r=0.05 <= 0.05
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_mfe_just_below_threshold_no_trigger(self) -> None:
        """MFEが閾値未満で不発動"""
        pm = _make_profit_reversal_pm(
            mfe_r=0.3, drop_r=0.25, max_r=0.05,
        )
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2

        # MFE 0.29R（150.0 + 0.29*0.2 = 150.058）
        pm.evaluate(
            position_id=pid,
            current_price=150.058,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 大幅下落しても閾値未達
        action = pm.evaluate(
            position_id=pid,
            current_price=149.95,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_drop_exactly_at_threshold_but_max_r_high(self) -> None:
        """下落幅が閾値ちょうどだがmax_r超で不発動"""
        pm = _make_profit_reversal_pm(
            mfe_r=0.3, drop_r=0.25, max_r=0.05,
        )
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2

        # MFE 0.4R（150.0 + 0.4*0.2 = 150.08）
        pm.evaluate(
            position_id=pid,
            current_price=150.08,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # drop = 0.4 - 0.15 = 0.25R, current_r = 0.15R > 0.05R
        action = pm.evaluate(
            position_id=pid,
            current_price=150.03,  # 0.15R
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # drop=0.25 >= 0.25, current_r=0.15 > 0.05 → 不発動
        assert action.action_type != ManagementActionType.FULL_CLOSE

    def test_drop_below_threshold_no_trigger(self) -> None:
        """下落幅が閾値未満で不発動"""
        pm = _make_profit_reversal_pm(
            mfe_r=0.3, drop_r=0.25, max_r=0.10,
        )
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2

        # MFE 0.4R
        pm.evaluate(
            position_id=pid,
            current_price=150.08,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # drop = 0.4 - 0.20 = 0.20R < 0.25R
        action = pm.evaluate(
            position_id=pid,
            current_price=150.04,  # 0.2R
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        assert action.action_type != ManagementActionType.FULL_CLOSE

    def test_max_r_at_boundary_triggers(self) -> None:
        """current_rが閾値以下で発動"""
        pm = _make_profit_reversal_pm(
            mfe_r=0.3, drop_r=0.20, max_r=0.10,
        )
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2

        # MFE 0.4R（150.08）
        pm.evaluate(
            position_id=pid,
            current_price=150.08,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # current_r = 0.05R（150.01）, drop = 0.4 - 0.05 = 0.35R
        action = pm.evaluate(
            position_id=pid,
            current_price=150.01,  # 浮動小数点誤差を避けるため少し低め
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # drop=0.35 >= 0.2, current_r=0.05 <= 0.1 → 発動
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_max_r_just_above_boundary_no_trigger(self) -> None:
        """current_rが閾値超で不発動"""
        pm = _make_profit_reversal_pm(
            mfe_r=0.3, drop_r=0.20, max_r=0.05,
        )
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2

        # MFE 0.4R
        pm.evaluate(
            position_id=pid,
            current_price=150.08,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # current_r = 0.10R > 0.05R
        action = pm.evaluate(
            position_id=pid,
            current_price=150.02,  # 0.1R
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        assert action.action_type != ManagementActionType.FULL_CLOSE

    def test_disabled_config_no_trigger(self) -> None:
        """無効時は不発動"""
        pm = _make_profit_reversal_pm(enabled=False)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        pm.evaluate(
            position_id=pid,
            current_price=150.10,  # MFE 0.5R
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        action = pm.evaluate(
            position_id=pid,
            current_price=149.95,  # 大幅下落
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_sell_position_profit_reversal(self) -> None:
        """SELLポジションでも利益反転ガード動作"""
        pm = _make_profit_reversal_pm(
            mfe_r=0.3, drop_r=0.25, max_r=0.05,
        )
        pm.register_position(
            position_id="sell_pos",
            direction=SignalType.SELL,
            entry_price=150.0,
            sl=150.2,  # 1R = 0.2
            tp=149.6,
            volume=1.0,
            entry_time=datetime(2024, 1, 1, 12, 0),
            plan=_make_plan(),
        )
        # MFE 0.4R（149.92）
        pm.evaluate(
            position_id="sell_pos",
            current_price=149.92,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 戻って0.05R（149.99）
        action = pm.evaluate(
            position_id="sell_pos",
            current_price=149.99,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # drop = 0.4 - 0.05 = 0.35R >= 0.25, current_r=0.05 <= 0.05
        assert action.action_type == ManagementActionType.FULL_CLOSE


class TestProgressiveStagnationBoundary:
    """段階的STAGNATIONの境界値テスト"""

    def test_stage1_time_exactly_at_threshold(self) -> None:
        """Stage1: 時間が閾値ちょうどで発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2

        # 60分ちょうど経過、MFE<0.05R、含み損-0.15R
        # -0.15R = 150.0 - 0.15*0.2 = 149.97
        action = pm.evaluate(
            position_id=pid,
            current_price=149.97,
            current_time=datetime(2024, 1, 1, 13, 0),  # 12:00 + 60分
            atr=0.002,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_stage1_time_just_below_threshold(self) -> None:
        """Stage1: 時間が閾値未満で不発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # 59分経過
        action = pm.evaluate(
            position_id=pid,
            current_price=149.97,  # -0.15R
            current_time=datetime(2024, 1, 1, 12, 59),
            atr=0.002,
        )
        assert action.action_type != ManagementActionType.FULL_CLOSE

    def test_stage1_mfe_above_threshold_no_trigger(self) -> None:
        """Stage1: MFEが閾値以上で不発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2

        # MFE 0.06R記録（150.0 + 0.06*0.2 = 150.012）
        # 0.05より少し大きい値で閾値超え
        pm.evaluate(
            position_id=pid,
            current_price=150.012,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 60分経過、含み損
        action = pm.evaluate(
            position_id=pid,
            current_price=149.97,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # MFE = 0.06 >= 0.05 → 不発動
        assert action.action_type != ManagementActionType.FULL_CLOSE

    def test_stage1_current_r_exactly_at_threshold(self) -> None:
        """Stage1: current_rが閾値ちょうどで発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2, -0.15R = 149.97

        action = pm.evaluate(
            position_id=pid,
            current_price=149.97,  # -0.15R ちょうど
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # current_r = -0.15 <= -0.15 → 発動
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_stage1_current_r_just_above_threshold(self) -> None:
        """Stage1: current_rが閾値超で不発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2, -0.14R = 149.972

        action = pm.evaluate(
            position_id=pid,
            current_price=149.972,  # -0.14R
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # current_r = -0.14 > -0.15 → 不発動
        assert action.action_type != ManagementActionType.FULL_CLOSE

    def test_stage2_triggers_after_stage1_conditions(self) -> None:
        """Stage2: Stage1条件不一致でStage2発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_stage2_minutes=90.0,
            stagnation_stage2_mfe_r=0.10,
            stagnation_stage2_max_r=-0.10,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)
        # 1R = 0.2

        # MFE 0.08R記録（Stage1 MFE=0.05超だがStage2 MFE=0.10未満）
        pm.evaluate(
            position_id=pid,
            current_price=150.016,  # 0.08R
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 90分経過、含み損-0.10R
        action = pm.evaluate(
            position_id=pid,
            current_price=149.98,  # -0.10R
            current_time=datetime(2024, 1, 1, 13, 30),
            atr=0.002,
        )
        # Stage1: MFE=0.08 >= 0.05 → 不発動
        # Stage2: MFE=0.08 < 0.10, current_r=-0.10 <= -0.10 → 発動
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_stage2_time_boundary(self) -> None:
        """Stage2: 時間が閾値ちょうどで発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_stage2_minutes=90.0,
            stagnation_stage2_mfe_r=0.10,
            stagnation_stage2_max_r=-0.10,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # MFE 0.08R記録
        pm.evaluate(
            position_id=pid,
            current_price=150.016,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 90分ちょうど経過
        action = pm.evaluate(
            position_id=pid,
            current_price=149.98,  # -0.10R
            current_time=datetime(2024, 1, 1, 13, 30),
            atr=0.002,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_stage2_time_just_below_threshold(self) -> None:
        """Stage2: 時間が閾値未満で不発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_stage2_minutes=90.0,
            stagnation_stage2_mfe_r=0.10,
            stagnation_stage2_max_r=-0.10,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # MFE 0.08R記録（Stage1 MFE超、Stage2未満）
        pm.evaluate(
            position_id=pid,
            current_price=150.016,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 89分経過
        action = pm.evaluate(
            position_id=pid,
            current_price=149.98,
            current_time=datetime(2024, 1, 1, 13, 29),
            atr=0.002,
        )
        assert action.action_type != ManagementActionType.FULL_CLOSE

    def test_disabled_config_no_trigger(self) -> None:
        """無効時は両Stage不発動"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=False,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # 全条件満たすが無効
        action = pm.evaluate(
            position_id=pid,
            current_price=149.97,  # -0.15R
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # progressive無効時はdefault stagnationロジックへ
        # 60分 < 180分閾値なのでHOLD
        assert action.action_type == ManagementActionType.HOLD

    def test_sell_position_progressive_stagnation(self) -> None:
        """SELLポジションでも段階的STAGNATION動作"""
        config = PositionManagerConfig(
            progressive_stagnation_enabled=True,
            stagnation_stage1_minutes=60.0,
            stagnation_stage1_mfe_r=0.05,
            stagnation_stage1_max_r=-0.15,
            stagnation_exit_minutes=180.0,
        )
        pm = PositionManager(config)
        pm.register_position(
            position_id="sell_pos",
            direction=SignalType.SELL,
            entry_price=150.0,
            sl=150.2,  # 1R = 0.2
            tp=149.6,
            volume=1.0,
            entry_time=datetime(2024, 1, 1, 12, 0),
            plan=_make_plan(),
        )
        # SELL: -0.15R = 150.0 + 0.15*0.2 = 150.03
        action = pm.evaluate(
            position_id="sell_pos",
            current_price=150.03,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE


class TestConsensusExitBoundary:
    """コンセンサス逆転exitの境界値テスト"""

    def test_opp_score_exactly_at_threshold(self) -> None:
        """逆方向スコアが閾値ちょうどで発動"""
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0)
        pid = _register_buy_pos(pm)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=2.0,
            sell_score=6.0,  # ちょうど閾値
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_opp_score_just_below_threshold(self) -> None:
        """逆方向スコアが閾値未満で不発動"""
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0)
        pid = _register_buy_pos(pm)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=1.0,
            sell_score=5.9,  # 閾値未満
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_own_score_exactly_at_threshold(self) -> None:
        """自方向スコアが閾値ちょうどで発動"""
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0)
        pid = _register_buy_pos(pm)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=3.0,  # ちょうど閾値
            sell_score=8.0,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_own_score_just_above_threshold(self) -> None:
        """自方向スコアが閾値超で不発動"""
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0)
        pid = _register_buy_pos(pm)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=3.1,  # 閾値超
            sell_score=8.0,
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_loss_only_current_r_exactly_zero(self) -> None:
        """含み損限定モード: current_r=0ちょうどは発動"""
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0, loss_only=True)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        action = pm.evaluate(
            position_id=pid,
            current_price=150.0,  # current_r = 0
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=1.0,
            sell_score=9.0,
        )
        # current_r=0 > 0 is False, so exit triggers
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_loss_only_current_r_slightly_positive(self) -> None:
        """含み損限定モード: current_r>0で不発動"""
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0, loss_only=True)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        action = pm.evaluate(
            position_id=pid,
            current_price=150.001,  # 微小な含み益
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=1.0,
            sell_score=9.0,
        )
        assert action.action_type != ManagementActionType.FULL_CLOSE

    def test_both_scores_zero_no_trigger(self) -> None:
        """両スコアが0で不発動"""
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0)
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

    def test_only_own_score_zero_triggers(self) -> None:
        """自方向スコア0、逆方向高い場合は発動"""
        pm = _make_no_be_pm(threshold=6.0, own_max=3.0)
        pid = _register_buy_pos(pm)

        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=0.0,
            sell_score=8.0,
        )
        # buy_score=0 and sell_score=0 is the skip condition
        # buy_score=0, sell_score=8 should trigger
        assert action.action_type == ManagementActionType.FULL_CLOSE

    def test_custom_threshold_values(self) -> None:
        """カスタム閾値での動作確認"""
        pm = _make_no_be_pm(threshold=8.0, own_max=2.0)
        pid = _register_buy_pos(pm)

        # 閾値引き上げ後、6.0では不発動
        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=1.0,
            sell_score=7.0,
        )
        assert action.action_type == ManagementActionType.HOLD

        # 8.0以上で発動
        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 5),
            atr=0.002,
            buy_score=1.0,
            sell_score=8.5,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE


class TestCombinedExitConditions:
    """複数exit条件の組み合わせテスト"""

    def test_profit_reversal_triggers_correctly(self) -> None:
        """利益反転ガードが正しく発動する"""
        config = PositionManagerConfig(
            profit_reversal_enabled=True,
            profit_reversal_mfe_r=0.3,
            profit_reversal_drop_r=0.25,
            profit_reversal_max_r=0.05,
            # 全ての他のexitを無効化
            progressive_stagnation_enabled=False,
            stagnation_exit_minutes=9999.0,
            consensus_exit_enabled=False,
            be_enabled_modes=(),
            early_breakeven_enabled=False,
            early_breakeven_r=999.0,
            range_day_be_disabled=True,
            range_day_early_be_r=999.0,
            range_day_fast_be_enabled=False,
            range_day_insurance_enabled=False,
            range_day_half_r_partial_enabled=False,
            universal_half_r_enabled=False,
            trailing_start_r=999.0,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # MFE 0.5R記録
        pm.evaluate(
            position_id=pid,
            current_price=150.10,
            current_time=datetime(2024, 1, 1, 12, 30),
            atr=0.002,
        )
        # 0.05Rに下落（drop=0.45R, current_r=0.05）
        action = pm.evaluate(
            position_id=pid,
            current_price=150.01,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
        )
        # profit_reversalが発動
        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert "利益反転" in action.reason

    def test_consensus_exit_triggers_correctly(self) -> None:
        """コンセンサス逆転が正しく発動する"""
        config = PositionManagerConfig(
            consensus_exit_enabled=True,
            consensus_exit_threshold=6.0,
            consensus_exit_own_max=3.0,
            # 全ての他のexitを無効化
            progressive_stagnation_enabled=False,
            stagnation_exit_minutes=9999.0,
            profit_reversal_enabled=False,
            be_enabled_modes=(),
            early_breakeven_enabled=False,
            early_breakeven_r=999.0,
            range_day_be_disabled=True,
            range_day_early_be_r=999.0,
            range_day_fast_be_enabled=False,
            range_day_insurance_enabled=False,
            range_day_half_r_partial_enabled=False,
            universal_half_r_enabled=False,
        )
        pm = PositionManager(config)
        pid = _register_buy_pos(pm, entry=150.0, sl=149.8)

        # 含み損でconsensus条件満たす
        action = pm.evaluate(
            position_id=pid,
            current_price=149.85,
            current_time=datetime(2024, 1, 1, 13, 0),
            atr=0.002,
            buy_score=1.0,
            sell_score=9.0,
        )
        # consensus_exitが発動
        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert "コンセンサス" in action.reason
