"""PositionManagerのユニットテスト"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from autotrader.core.enums import ExitReason, SignalType
from autotrader.decision.unified.mode_selector import TradingPlan
from autotrader.decision.unified.position_manager import (
    ManagementAction,
    ManagementActionType,
    PositionManager,
    PositionManagerConfig,
)

# 長期保有プラン
SWING_PLAN = TradingPlan(
    mode="UNIVERSAL",
    primary_tf="H1",
    entry_tf="M15",
    confirm_tfs=["H4"],
    manage_tf="H1",
    max_holding_bars=48,
    tp_sl_ratio_range=(1.2, 1.6),
)

# 短期保有プラン
SCALP_PLAN = TradingPlan(
    mode="UNIVERSAL",
    primary_tf="M5",
    entry_tf="M1",
    confirm_tfs=["M15"],
    manage_tf="M5",
    max_holding_bars=18,
    tp_sl_ratio_range=(1.0, 1.5),
)


class TestPositionManager:
    """PositionManagerのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.manager = PositionManager()
        # UNIVERSALプラン（BE有効）
        self.plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
        )
        self.entry_time = datetime(2024, 1, 1, 10, 0, 0)

    def test_register_position(self) -> None:
        """ポジション登録"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        position = self.manager.get_position("test1")

        assert position is not None
        assert position.direction == SignalType.BUY
        assert position.entry_price == 150.0
        assert position.original_sl == 149.5
        assert position.original_tp == 151.0

    def test_unregister_position(self) -> None:
        """ポジション登録解除"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        self.manager.unregister_position("test1")

        assert self.manager.get_position("test1") is None

    def test_sl_hit_buy(self) -> None:
        """買いポジションのSL到達"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        action = self.manager.evaluate(
            position_id="test1",
            current_price=149.4,  # SL以下
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )

        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert action.exit_reason == ExitReason.STOP_LOSS

    def test_sl_hit_sell(self) -> None:
        """売りポジションのSL到達"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.SELL,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=150.5,
            tp=149.0,
            volume=0.1,
            plan=self.plan,
        )

        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.6,  # SL以上
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )

        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert action.exit_reason == ExitReason.STOP_LOSS

    def test_tp_hit_buy(self) -> None:
        """買いポジションのTP到達（TP無効化OFF）"""
        # TP無効化OFFでテスト
        config = PositionManagerConfig(
            disable_tp_after_partial=False,
        )
        manager = PositionManager(config)
        manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 早期BE通過（0.5R閾値）
        manager.evaluate(
            "test1", 150.3,
            self.entry_time + timedelta(minutes=10), 0.5,
        )

        # 1R到達 → 部分利確（順序変更により先に発火）
        action = manager.evaluate(
            position_id="test1",
            current_price=150.6,
            current_time=self.entry_time + timedelta(minutes=15),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.exit_reason == ExitReason.TAKE_PROFIT_1R

        # 2R到達 → 部分利確
        action = manager.evaluate(
            position_id="test1",
            current_price=151.1,
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.exit_reason == ExitReason.TAKE_PROFIT_2R

        # TP到達 → 全決済（TP無効化OFF）
        action = manager.evaluate(
            position_id="test1",
            current_price=151.1,
            current_time=self.entry_time + timedelta(minutes=45),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert action.exit_reason == ExitReason.TAKE_PROFIT

    def test_time_exit_day_trade(self) -> None:
        """UNIVERSALの時間決済"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 一度0.25R以上に到達させてhighest_rを記録（超早期exitを回避）
        # 0.25R = 0.125 → price=150.125
        self.manager.evaluate(
            "test1", 150.125,
            self.entry_time + timedelta(minutes=15), 0.5,
        )

        # 8時間後（最大保有時間）
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.1,  # 小幅利益（early_be_r=0.3未満の0.2R）
            current_time=self.entry_time + timedelta(hours=9),
            atr=0.5,
        )

        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert action.exit_reason == ExitReason.TIME_EXIT

    def test_time_exit_scalping(self) -> None:
        """時間決済（90分）"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.9,
            tp=150.15,
            volume=0.1,
            plan=SCALP_PLAN,
        )

        # early_BE通過済みにして（0.6Rで発火させる）
        self.manager.evaluate(
            "test1", 150.06,
            self.entry_time + timedelta(minutes=10), 0.1,
        )

        # 90分後（M1 entry_tfの最大保有時間）
        # BE価格=entry+slip*2+cushion=150.0+0.01+0.03=150.04
        # → 150.05はBE上で時間決済が先に発動
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.05,  # 0.5R（BE価格150.04の上）
            current_time=self.entry_time + timedelta(minutes=95),
            atr=0.1,
        )

        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert action.exit_reason == ExitReason.TIME_EXIT

    def test_signal_reversal_with_profit(self) -> None:
        """シグナル反転: 含み益で部分決済+BE"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 早期BE通過（0.5R閾値、BE有効）
        self.manager.evaluate(
            "test1", 150.3,
            self.entry_time + timedelta(minutes=15), 0.5,
        )

        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.3,  # R>0（含み益）
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
            current_signal=SignalType.SELL,
        )

        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        # signal_rev_close_ratio=0.0: 部分決済なし、BE移動のみ
        assert action.close_ratio == 0.0
        assert action.exit_reason == ExitReason.SIGNAL_REVERSAL
        assert action.new_sl is not None  # BE移動

    def test_signal_reversal_ignored_when_losing(self) -> None:
        """シグナル反転: 含み損(R<=0)で無視

        Note: 超早期exit（MFE<0.2R + 30分経過）が先に発火するため、
        シグナル反転チェックには到達しない。29分未満でテスト。
        """
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 29分未満で超早期exitを回避
        action = self.manager.evaluate(
            position_id="test1",
            current_price=149.8,  # R<0（含み損）
            current_time=self.entry_time + timedelta(minutes=25),
            atr=0.5,
            current_signal=SignalType.SELL,
        )

        # 含み損では反転を無視し、SLに委ねる
        assert action.action_type == ManagementActionType.HOLD

    def test_partial_close_1r(self) -> None:
        """1R到達での部分決済（DAY_TRADE: BE有効）"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,  # 0.5 = 1R
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 早期BE通過（0.5R閾値、BE有効）
        self.manager.evaluate(
            "test1", 150.3,
            self.entry_time + timedelta(minutes=15), 0.5,
        )

        # 1R超過 → 部分決済 + BE移動
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.6,  # 1R超過
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.close_ratio == 0.05
        # DAY_TRADEもBE移動あり（slip*2 + cushion = 0.01+0.03）
        assert action.new_sl == pytest.approx(150.04)

    def test_partial_close_2r(self) -> None:
        """2R到達での部分決済（DAY_TRADE）"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,  # 0.5 = 1R
            tp=152.0,
            volume=0.1,
            plan=self.plan,
        )

        # 早期BE通過（0.5R閾値）
        self.manager.evaluate(
            "test1", 150.3,
            self.entry_time + timedelta(minutes=15), 0.5,
        )

        # 1R部分決済（BE有効、DAY_TRADE）
        self.manager.evaluate(
            position_id="test1",
            current_price=150.6,
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )

        # 2R = 1.0円 → 151.0で2R到達
        action = self.manager.evaluate(
            position_id="test1",
            current_price=151.1,  # 2R超過
            current_time=self.entry_time + timedelta(minutes=60),
            atr=0.5,
        )

        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.close_ratio == 0.05
        # SLを1Rに移動（2R時は全モードで適用）
        assert action.new_sl == 150.5  # entry + 1R

    def test_trailing_stop(self) -> None:
        """トレーリングストップ（DAY_TRADE）"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=152.0,
            volume=0.1,
            plan=self.plan,
        )

        # 早期BE通過 → 1R, 2R通過
        self.manager.evaluate(
            "test1", 150.3,
            self.entry_time + timedelta(minutes=15), 0.5,
        )
        self.manager.evaluate(
            "test1", 150.6,
            self.entry_time + timedelta(minutes=30), 0.5,
        )
        self.manager.evaluate(
            "test1", 151.1,
            self.entry_time + timedelta(minutes=60), 0.5,
        )

        # 3R超過（トレーリング開始）
        action = self.manager.evaluate(
            position_id="test1",
            current_price=151.6,  # 3R超過
            current_time=self.entry_time + timedelta(minutes=90),
            atr=0.3,
        )

        assert action.action_type == ManagementActionType.UPDATE_SL
        assert action.new_sl is not None
        assert action.new_sl > 149.5  # SL引き上げ

    def test_hold_when_no_conditions(self) -> None:
        """条件未達でHOLD

        Note: 超早期exit（MFE<0.2R + 30分経過）を回避するため、
        MFE >= 0.2R を先に記録する必要がある。
        """
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 先に0.25Rに到達させてhighest_rを記録（超早期exitを回避）
        # 0.25R = 0.125 → price=150.125
        self.manager.evaluate(
            "test1", 150.125,
            self.entry_time + timedelta(minutes=15), 0.5,
        )

        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.1,  # 0.2R（early_be_r=0.3未満）
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )

        assert action.action_type == ManagementActionType.HOLD

    def test_unregistered_position(self) -> None:
        """未登録ポジション"""
        action = self.manager.evaluate(
            position_id="unknown",
            current_price=150.0,
            current_time=self.entry_time,
            atr=0.5,
        )

        assert action.action_type == ManagementActionType.HOLD
        assert "未登録" in action.reason

    def test_reset(self) -> None:
        """リセット"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        self.manager.reset()

        assert self.manager.get_position("test1") is None

    def test_tp_disabled_after_1r_partial(self) -> None:
        """1R部分利確後にTPが無効化される"""
        # デフォルト設定（disable_tp_after_partial=True）
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 早期BE通過（0.5R閾値）
        self.manager.evaluate(
            "test1", 150.3,
            self.entry_time + timedelta(minutes=10), 0.5,
        )

        # 1R到達 → 部分利確 + TP無効化
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.6,
            current_time=self.entry_time + timedelta(minutes=15),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.exit_reason == ExitReason.TAKE_PROFIT_1R

        # 2R到達 → 部分利確
        action = self.manager.evaluate(
            position_id="test1",
            current_price=151.1,
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.exit_reason == ExitReason.TAKE_PROFIT_2R

        # TP到達地点でもTP無効 → トレーリング更新
        action = self.manager.evaluate(
            position_id="test1",
            current_price=151.1,
            current_time=self.entry_time + timedelta(minutes=45),
            atr=0.5,
        )
        # TPは無効、2R以上なのでトレーリング更新
        assert action.action_type in (
            ManagementActionType.UPDATE_SL,
            ManagementActionType.HOLD,
        )
        assert action.exit_reason != ExitReason.TAKE_PROFIT

    def test_tp_not_disabled_when_config_off(self) -> None:
        """disable_tp_after_partial=FalseでTP有効維持"""
        config = PositionManagerConfig(
            disable_tp_after_partial=False,
        )
        manager = PositionManager(config)
        manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 早期BE通過（0.5R閾値）
        manager.evaluate(
            "test1", 150.3,
            self.entry_time + timedelta(minutes=10), 0.5,
        )

        # 1R到達 → 部分利確
        action = manager.evaluate(
            position_id="test1",
            current_price=150.6,
            current_time=self.entry_time + timedelta(minutes=15),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE

        # 2R到達 → 部分利確
        action = manager.evaluate(
            position_id="test1",
            current_price=151.1,
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE

        # TP到達 → 全決済（TP無効化されていない）
        action = manager.evaluate(
            position_id="test1",
            current_price=151.1,
            current_time=self.entry_time + timedelta(minutes=45),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert action.exit_reason == ExitReason.TAKE_PROFIT

    def test_partial_before_tp_at_1r(self) -> None:
        """1R=TP同時到達で部分利確が優先"""
        config = PositionManagerConfig(
            disable_tp_after_partial=False,
        )
        manager = PositionManager(config)
        # TP=1.0R（entry+r_value=150.5）
        manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=150.5,
            volume=0.1,
            plan=self.plan,
        )

        # 早期BE通過（0.5R閾値）
        manager.evaluate(
            "test1", 150.3,
            self.entry_time + timedelta(minutes=10), 0.5,
        )

        # 1R=TP地点 → 部分利確が先に発火
        action = manager.evaluate(
            position_id="test1",
            current_price=150.6,
            current_time=self.entry_time + timedelta(minutes=15),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.exit_reason == ExitReason.TAKE_PROFIT_1R



class TestStagnationExit:
    """Stagnation Exit（進捗なし撤退）のテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.config = PositionManagerConfig(
            stagnation_exit_minutes=120.0,
            stagnation_min_mfe_r=0.2,
        )
        self.manager = PositionManager(config=self.config)
        self.plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
        )
        self.entry_time = datetime(2024, 1, 1, 10, 0, 0)

    def test_stagnation_exit_triggered(self) -> None:
        """120分+MFE<0.2Rで撤退"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 進捗なし状態で120分経過
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.05,  # MFE=0.1R（<0.2R）
            current_time=(
                self.entry_time + timedelta(minutes=121)
            ),
            atr=0.5,
        )

        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert action.exit_reason == ExitReason.STAGNATION

    def test_stagnation_exit_not_triggered_with_progress(
        self,
    ) -> None:
        """MFE>=0.2Rで継続"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 先に0.2R以上の進捗を記録（early_be_r=0.3未満）
        self.manager.evaluate(
            position_id="test1",
            current_price=150.12,  # 0.24R（early_be未発火、stag_mfe超過）
            current_time=(
                self.entry_time + timedelta(minutes=30)
            ),
            atr=0.5,
        )

        # 価格が戻っても、highest_r >= stagnation_min_mfe_rなので継続
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.01,
            current_time=(
                self.entry_time + timedelta(minutes=241)
            ),
            atr=0.5,
        )

        assert action.action_type == ManagementActionType.HOLD

    def test_stagnation_exit_not_triggered_early(
        self,
    ) -> None:
        """120分未満で継続"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.plan,
        )

        # 超早期exit（MFE<0.2R + 30分経過）を回避するため、
        # MFE >= 0.2R を先に記録
        # 0.25R = 0.125 → price=150.125
        self.manager.evaluate(
            "test1", 150.125,
            self.entry_time + timedelta(minutes=15), 0.5,
        )

        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.05,
            current_time=(
                self.entry_time + timedelta(minutes=60)
            ),
            atr=0.5,
        )

        assert action.action_type == ManagementActionType.HOLD


class TestBreakevenImprovement:
    """BE改善テスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        # early_breakeven_enabled を明示的にONにしてテスト
        cfg = PositionManagerConfig(early_breakeven_enabled=True)
        self.manager = PositionManager(config=cfg)
        self.entry_time = datetime(2024, 1, 1, 10, 0, 0)
        # デフォルト: slip=0.5, cushion=3.0
        # offset = slip*2*0.01 + cushion*0.01 = 0.01 + 0.03 = 0.04
        self.be_offset = 0.5 * 2 * 0.01 + 3.0 * 0.01  # 0.04

    def test_be_price_buy_includes_spread(self) -> None:
        """BUY BE価格 = entry + spread + slippage"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=SWING_PLAN,
        )
        position = self.manager.get_position("test1")
        be_price = self.manager._get_be_price(position)
        # BUY: entry + 2*slip + cushion = 150.0 + 0.01 + 0.03
        assert be_price == pytest.approx(150.04)

    def test_be_price_sell_includes_spread(self) -> None:
        """SELL BE価格 = entry - spread - slippage"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.SELL,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=150.5,
            tp=149.0,
            volume=0.1,
            plan=SWING_PLAN,
        )
        position = self.manager.get_position("test1")
        be_price = self.manager._get_be_price(position)
        # SELL: entry - 2*slip - cushion = 150.0 - 0.01 - 0.03
        assert be_price == pytest.approx(149.96)

    def test_day_trade_early_be_at_0_3r(self) -> None:
        """UNIVERSALで0.3RでBE発火"""
        day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
        )
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,  # r_value=0.5
            tp=151.0,
            volume=0.1,
            plan=day_plan,
        )

        # 0.2R → まだ未達（閾値0.3R）
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.1,  # 0.2R
            current_time=self.entry_time + timedelta(minutes=15),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.HOLD

        # 0.4R → 早期BE発火（閾値0.3R超）
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.2,  # 0.4R
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.UPDATE_SL
        assert action.new_sl == pytest.approx(150.04)

    def test_be_applied_for_universal(self) -> None:
        """UNIVERSALでBE発火

        Note: SL=149.9 → r_value=0.1
        0.4R=0.04 → price=150.04 (0.5R部分利確より前、early_BE 0.3R超)
        """
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.9,  # r_value=0.1
            tp=150.2,
            volume=0.1,
            plan=SCALP_PLAN,
        )

        # 0.4R超過（0.3R early_BE閾値を超える、0.5R部分利確未満）
        # r_value=0.1なので、0.4R=0.04 → price=150.04
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.04,  # 0.4R
            current_time=self.entry_time + timedelta(minutes=5),
            atr=0.1,
        )
        # UNIVERSALではBE有効 → UPDATE_SL
        assert action.action_type == ManagementActionType.UPDATE_SL

    def test_partial_close_with_be_for_day_trade(self) -> None:
        """DAY_TRADEで1R部分利確+BE移動"""
        day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
        )
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,  # r_value=0.5
            tp=151.5,
            volume=0.1,
            plan=day_plan,
        )

        # 早期BE通過（0.5R閾値）
        self.manager.evaluate(
            "test1", 150.3,
            self.entry_time + timedelta(minutes=15), 0.5,
        )

        # 1R超過 → 部分利確 + BE移動
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.6,  # 1.2R
            current_time=self.entry_time + timedelta(minutes=30),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.close_ratio == 0.05
        # DAY_TRADEでもBE移動あり
        assert action.new_sl == pytest.approx(150.04)
        assert action.exit_reason == ExitReason.TAKE_PROFIT_1R

    def test_be_exit_reason_with_offset(self) -> None:
        """SWING BE_HIT SL = be_price（offset付き）

        Note: early_BE閾値0.3Rをテスト。0.5R部分利確は別テストで確認。
        """
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,  # r_value=0.5
            tp=152.0,
            volume=0.1,
            plan=SWING_PLAN,
        )

        # 1回目: 早期BE移動（0.3R閾値、0.5R部分利確未満）
        # 0.4R >= 0.2R なので超早期exitは発火しない
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.2,  # 0.4R (0.5R部分利確未満)
            current_time=self.entry_time + timedelta(minutes=25),  # 30分未満
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.UPDATE_SL
        be_price = 150.0 + self.be_offset  # 150.02
        assert action.new_sl == pytest.approx(be_price)

        # current_slもbe_priceに更新されているか確認
        position = self.manager.get_position("test1")
        assert position.current_sl == pytest.approx(be_price)

        # 2回目: 価格がBE価格まで戻る → BREAKEVEN判定
        action = self.manager.evaluate(
            position_id="test1",
            current_price=be_price - 0.001,  # BEのSLを下回る
            current_time=self.entry_time + timedelta(minutes=60),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert action.exit_reason == ExitReason.BREAKEVEN

    def test_swing_early_be_at_0_3r(self) -> None:
        """SWING早期BEが0.3Rで発火

        Note: 超早期exit（MFE<0.2R + 30分経過）を回避するため、
        - 最初の評価は29分以内
        - 後続でMFE >= 0.2Rを記録
        """
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,  # r_value=0.5
            tp=152.0,
            volume=0.1,
            plan=SWING_PLAN,
        )

        # 0.2R → まだ未達（閾値0.3R）。29分で超早期exitを回避
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.1,  # 0.2R
            current_time=self.entry_time + timedelta(minutes=25),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.HOLD

        # 0.4R → 早期BE発火（閾値0.3R超）
        # 0.4R >= 0.2R なので超早期exitは発火しない
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.2,  # 0.4R
            current_time=self.entry_time + timedelta(minutes=60),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.UPDATE_SL
        assert action.new_sl == pytest.approx(150.04)

    def test_swing_1r_be_with_offset(self) -> None:
        """SWING 1RでBE移動（offset付き）"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,  # r_value=0.5
            tp=152.0,
            volume=0.1,
            plan=SWING_PLAN,
        )

        # 早期BE通過
        self.manager.evaluate(
            "test1", 150.4,
            self.entry_time + timedelta(minutes=30), 0.5,
        )

        # 1R到達 → 部分利確 + BE移動
        action = self.manager.evaluate(
            position_id="test1",
            current_price=150.6,  # 1.2R
            current_time=self.entry_time + timedelta(minutes=60),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.close_ratio == 0.05
        # SWINGではBE移動あり（offset付き）
        assert action.new_sl == pytest.approx(150.04)

    def test_current_sl_updated_on_2r(self) -> None:
        """2R到達時にcurrent_slが同期される"""
        self.manager.register_position(
            position_id="test1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,  # r_value=0.5
            tp=152.0,
            volume=0.1,
            plan=SWING_PLAN,
        )

        # 早期BE通過
        self.manager.evaluate(
            "test1", 150.4,
            self.entry_time + timedelta(minutes=30), 0.5,
        )
        # 1R通過
        self.manager.evaluate(
            "test1", 150.6,
            self.entry_time + timedelta(minutes=60), 0.5,
        )
        # 2R到達
        action = self.manager.evaluate(
            position_id="test1",
            current_price=151.1,  # 2.2R
            current_time=self.entry_time + timedelta(minutes=90),
            atr=0.5,
        )
        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.new_sl == 150.5  # entry + 1R

        # current_slも更新されているか確認
        position = self.manager.get_position("test1")
        assert position.current_sl == 150.5


class TestManagementAction:
    """ManagementActionのテスト"""

    def test_hold_factory(self) -> None:
        """holdファクトリメソッド"""
        action = ManagementAction.hold("テスト理由")

        assert action.action_type == ManagementActionType.HOLD
        assert action.close_ratio == 0.0
        assert action.new_sl is None
        assert action.reason == "テスト理由"

    def test_update_sl_factory(self) -> None:
        """update_slファクトリメソッド"""
        action = ManagementAction.update_sl(150.5, "トレーリング")

        assert action.action_type == ManagementActionType.UPDATE_SL
        assert action.new_sl == 150.5
        assert action.close_ratio == 0.0

    def test_partial_close_factory(self) -> None:
        """partial_closeファクトリメソッド"""
        action = ManagementAction.partial_close(0.3, 150.0, "1R到達")

        assert action.action_type == ManagementActionType.PARTIAL_CLOSE
        assert action.close_ratio == 0.3
        assert action.new_sl == 150.0
        assert action.exit_reason == ExitReason.TAKE_PROFIT

    def test_full_close_factory(self) -> None:
        """full_closeファクトリメソッド"""
        action = ManagementAction.full_close(
            "SL到達", ExitReason.STOP_LOSS
        )

        assert action.action_type == ManagementActionType.FULL_CLOSE
        assert action.close_ratio == 1.0
        assert action.exit_reason == ExitReason.STOP_LOSS


class TestRangeDayBeFix:
    """UNIVERSAL×RANGE BE修正のテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.config = PositionManagerConfig(
            early_breakeven_enabled=True,
            range_day_be_disabled=True,
            range_day_early_be_r=1.0,
            range_day_fast_be_enabled=False,
            range_day_insurance_enabled=False,
            range_day_half_r_partial_enabled=False,
        )
        self.manager = PositionManager(self.config)
        self.entry_time = datetime(2024, 1, 1, 10, 0, 0)

        # RANGE×UNIVERSALプラン
        self.range_day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="RANGE",
        )

        # TREND×UNIVERSALプラン
        self.trend_day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="TREND",
        )

    def _register(
        self,
        plan: TradingPlan,
        pos_id: str = "pos1",
    ) -> None:
        """ヘルパー: ポジション登録"""
        self.manager.register_position(
            position_id=pos_id,
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=plan,
        )

    def test_range_day_no_early_be_at_05r(self) -> None:
        """RANGE×DAYで0.5RではBE発火しない"""
        self._register(self.range_day_plan)
        now = self.entry_time + timedelta(minutes=15)

        # 0.5R到達（150.0→150.25）
        action = self.manager.evaluate(
            "pos1", 150.25, now, atr=0.5,
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_range_day_1r_partial_close_with_be(self) -> None:
        """RANGE×DAYで1Rでは部分利確+BE移動"""
        self._register(self.range_day_plan)
        now = self.entry_time + timedelta(minutes=15)

        # 1R到達（150.0→150.5）
        action = self.manager.evaluate(
            "pos1", 150.5, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action.exit_reason == ExitReason.TAKE_PROFIT_1R

    def test_trend_day_still_has_early_be(self) -> None:
        """TREND×DAYは従来通り0.3RでBE

        Note: early_BE閾値0.3R、0.5R部分利確より前の価格でテスト
        """
        self._register(self.trend_day_plan)
        now = self.entry_time + timedelta(minutes=25)  # 30分未満

        # 0.4R到達（150.0→150.20）- 0.5R部分利確未満、0.3R early_BE超
        action = self.manager.evaluate(
            "pos1", 150.20, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.UPDATE_SL
        )

    def test_range_regime_applies_range_day_be_disabled(self) -> None:
        """RANGE regimeではrange_day_be_disabled設定が適用される"""
        range_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="H1",
            entry_tf="M15",
            confirm_tfs=["H4"],
            manage_tf="H1",
            max_holding_bars=48,
            tp_sl_ratio_range=(1.2, 1.6),
            regime="RANGE",
        )
        self._register(range_plan)
        now = self.entry_time + timedelta(minutes=15)

        # 0.5R到達 → range_day_be_disabled=True + range_day_early_be_r=1.0
        # なので0.5Rでは発火しない → HOLD
        action = self.manager.evaluate(
            "pos1", 150.25, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.HOLD
        )

    def test_1r_priority_over_early_be_same_tick(
        self,
    ) -> None:
        """同一ティックで1R優先（処理順テスト）"""
        self._register(self.trend_day_plan)
        now = self.entry_time + timedelta(minutes=15)

        # 1.0R到達 → 早期BEではなく1R部分利確
        action = self.manager.evaluate(
            "pos1", 150.5, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action.exit_reason == ExitReason.TAKE_PROFIT_1R

    def test_cli_flag_disable_returns_legacy(self) -> None:
        """--no-range-day-be-fixで従来動作（0.5R BE）"""
        legacy_config = PositionManagerConfig(
            early_breakeven_enabled=True,
            range_day_be_disabled=False,
            range_day_insurance_enabled=False,
            range_day_half_r_partial_enabled=False,
        )
        manager = PositionManager(legacy_config)
        manager.register_position(
            position_id="pos1",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=self.range_day_plan,
        )
        now = self.entry_time + timedelta(minutes=15)

        # 0.5R到達 → レガシーでは0.5R BEが発火
        action = manager.evaluate(
            "pos1", 150.25, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.UPDATE_SL
        )


class TestFastBeAndStagnation:
    """速度ベースBE + RANGE×DAY stagnation厳格化テスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.entry_time = datetime(2024, 1, 1, 10, 0, 0)
        self.range_day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="RANGE",
        )
        self.trend_day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="TREND",
        )

    def _register(
        self,
        manager: PositionManager,
        plan: TradingPlan,
        pos_id: str = "pos1",
    ) -> None:
        """ヘルパー: ポジション登録"""
        manager.register_position(
            position_id=pos_id,
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=plan,
        )

    def test_fast_be_fires_when_quick(self) -> None:
        """RANGE×DAY: 30分で0.5R到達→BE発火"""
        config = PositionManagerConfig(
            early_breakeven_enabled=True,
            range_day_be_disabled=True,
            range_day_early_be_r=1.0,
            range_day_fast_be_enabled=True,
            range_day_fast_be_minutes=90.0,
            range_day_insurance_enabled=False,
            range_day_half_r_partial_enabled=False,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)
        now = self.entry_time + timedelta(minutes=30)

        action = manager.evaluate(
            "pos1", 150.25, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.UPDATE_SL
        )

    def test_slow_be_does_not_fire(self) -> None:
        """RANGE×DAY: 120分で0.5R到達→BE不発火"""
        config = PositionManagerConfig(
            range_day_be_disabled=True,
            range_day_early_be_r=1.0,
            range_day_fast_be_enabled=True,
            range_day_fast_be_minutes=90.0,
            range_day_insurance_enabled=False,
            range_day_half_r_partial_enabled=False,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)
        now = self.entry_time + timedelta(minutes=120)

        action = manager.evaluate(
            "pos1", 150.25, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.HOLD
        )

    def test_range_day_strict_stagnation(self) -> None:
        """RANGE×DAY: 65分MFE<0.10R→Stage2 STAGNATION決済"""
        config = PositionManagerConfig(
            range_day_stagnation_enabled=True,
            range_day_stagnation_stage1_minutes=45.0,
            range_day_stagnation_stage1_min_mfe_r=0.05,
            range_day_stagnation_stage2_minutes=60.0,
            range_day_stagnation_stage2_min_mfe_r=0.10,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)
        now = self.entry_time + timedelta(minutes=65)

        # MFE=0.04R < 0.10R → Stage2発火
        # entry=150.0, sl=149.5, 1R=0.5, 0.04R=0.02
        action = manager.evaluate(
            "pos1", 150.02, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.FULL_CLOSE
        )
        assert action.exit_reason == ExitReason.STAGNATION

    def test_trend_day_normal_stagnation(self) -> None:
        """TREND×DAY: レジームベースSTAGNATION（TRENDは60分）

        新ロジック: TREND/RANGE/CHOPPYでSTAGNATION時間が異なる
        - TREND: 60分
        - RANGE: 90分
        - CHOPPY: 120分

        65分経過 + MFE<0.1R → TRENDは60分閾値なので発火
        """
        config = PositionManagerConfig(
            range_day_stagnation_enabled=True,
            range_day_stagnation_stage1_minutes=45.0,
            range_day_stagnation_stage1_min_mfe_r=0.05,
            range_day_stagnation_stage2_minutes=60.0,
            range_day_stagnation_stage2_min_mfe_r=0.10,
        )
        manager = PositionManager(config)
        self._register(manager, self.trend_day_plan)

        # 先に0.25Rまで上昇させてhighest_rを記録（超早期exitを回避）
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.125, t1, atr=0.5)

        # 55分経過（TRENDは60分閾値なので未到達）
        now = self.entry_time + timedelta(minutes=55)
        action = manager.evaluate(
            "pos1", 150.05, now, atr=0.5,
        )
        # TRENDで55分 < 60分閾値 → HOLD
        assert action.action_type == (
            ManagementActionType.HOLD
        )

    def test_range_day_stage1_stagnation(self) -> None:
        """RANGE×DAY: 48分MFE=0.03R→Stage1 STAGNATION"""
        config = PositionManagerConfig(
            range_day_stagnation_enabled=True,
            range_day_stagnation_stage1_minutes=45.0,
            range_day_stagnation_stage1_min_mfe_r=0.05,
            range_day_stagnation_stage2_minutes=60.0,
            range_day_stagnation_stage2_min_mfe_r=0.10,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)
        now = self.entry_time + timedelta(minutes=48)

        # MFE=0.03R < 0.05R → Stage1発火
        # entry=150.0, sl=149.5 → 1R=0.5
        # 0.03R = 0.015 → price=150.015
        action = manager.evaluate(
            "pos1", 150.015, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.FULL_CLOSE
        )
        assert action.exit_reason == ExitReason.STAGNATION

    def test_range_day_stage1_survives_with_mfe(
        self,
    ) -> None:
        """RANGE×DAY: 48分MFE=0.25R→Stage1不発、Stage2未到達

        Note: MFE >= 0.2R にして超早期exitを回避する
        """
        config = PositionManagerConfig(
            range_day_stagnation_enabled=True,
            range_day_stagnation_stage1_minutes=45.0,
            range_day_stagnation_stage1_min_mfe_r=0.05,
            range_day_stagnation_stage2_minutes=60.0,
            range_day_stagnation_stage2_min_mfe_r=0.10,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # まず0.25Rまで上昇させてhighest_rを記録（超早期exit回避）
        # 0.25R = 0.125 → price=150.125
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.125, t1, atr=0.5)

        # 48分後に戻る → Stage1: MFE=0.25R >= 0.05R 不発
        now = self.entry_time + timedelta(minutes=48)
        action = manager.evaluate(
            "pos1", 150.0, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.HOLD
        )

    def test_range_day_stage2_stagnation(self) -> None:
        """RANGE×DAY: 65分MFE=0.08R→Stage2 STAGNATION"""
        config = PositionManagerConfig(
            range_day_stagnation_enabled=True,
            range_day_stagnation_stage1_minutes=45.0,
            range_day_stagnation_stage1_min_mfe_r=0.05,
            range_day_stagnation_stage2_minutes=60.0,
            range_day_stagnation_stage2_min_mfe_r=0.10,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # まず0.08Rまで上昇させてhighest_rを記録
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.04, t1, atr=0.5)

        # 65分後 → Stage2: MFE=0.08R < 0.10R 発火
        now = self.entry_time + timedelta(minutes=65)
        action = manager.evaluate(
            "pos1", 150.0, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.FULL_CLOSE
        )
        assert action.exit_reason == ExitReason.STAGNATION

    def test_range_day_stage2_survives_with_mfe(
        self,
    ) -> None:
        """RANGE×DAY: 65分MFE=0.25R→両Stage不発

        Note: MFE >= 0.2R にして超早期exitを回避する
        """
        config = PositionManagerConfig(
            range_day_stagnation_enabled=True,
            range_day_stagnation_stage1_minutes=45.0,
            range_day_stagnation_stage1_min_mfe_r=0.05,
            range_day_stagnation_stage2_minutes=60.0,
            range_day_stagnation_stage2_min_mfe_r=0.10,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # まず0.25Rまで上昇させてhighest_rを記録（超早期exit回避）
        # 0.25R = 0.125 → price=150.125
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.125, t1, atr=0.5)

        # 65分後 → Stage1: 0.25R>=0.05R 不発
        # Stage2: 0.25R>=0.10R 不発
        now = self.entry_time + timedelta(minutes=65)
        action = manager.evaluate(
            "pos1", 150.0, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.HOLD
        )


class TestRangeDayInsurance:
    """RANGE×DAY軽い保険テスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.entry_time = datetime(2024, 1, 1, 10, 0, 0)
        self.range_day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="RANGE",
        )
        self.trend_day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="TREND",
        )

    def _register(
        self,
        manager: PositionManager,
        plan: TradingPlan,
        pos_id: str = "pos1",
    ) -> None:
        """ヘルパー: ポジション登録"""
        manager.register_position(
            position_id=pos_id,
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=plan,
        )

    def test_insurance_sl_triggered(self) -> None:
        """RANGE×DAY: 20分で0.3R到達→SL引き上げ"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)
        now = self.entry_time + timedelta(minutes=20)

        # 0.3R = 0.15 → price=150.15
        action = manager.evaluate(
            "pos1", 150.15, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.UPDATE_SL
        )
        # SL → entry - 0.1 * r_value(0.5) = 149.95
        pos = manager._positions["pos1"]
        assert abs(pos.current_sl - 149.95) < 0.001

    def test_insurance_sl_not_triggered_slow(
        self,
    ) -> None:
        """RANGE×DAY: 40分で0.3R到達→時間超過でHOLD"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)
        now = self.entry_time + timedelta(minutes=40)

        action = manager.evaluate(
            "pos1", 150.15, now, atr=0.5,
        )
        # 時間超過 → 保険不発、早期BEが発火する可能性
        assert action.action_type != (
            ManagementActionType.PARTIAL_CLOSE
        )

    def test_insurance_partial_triggered(self) -> None:
        """保険SL適用済み + 0.5R到達→部分利確+BE"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            range_day_insurance_partial_ratio=0.20,
            insurance_trigger_r=0.5,
            insurance_min_holding_minutes=0.0,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # Step1: 20分で0.3R到達→SL引き上げ
        t1 = self.entry_time + timedelta(minutes=20)
        action1 = manager.evaluate(
            "pos1", 150.15, t1, atr=0.5,
        )
        assert action1.action_type == (
            ManagementActionType.UPDATE_SL
        )

        # Step2: 25分で0.5R到達→部分利確+BE
        t2 = self.entry_time + timedelta(minutes=25)
        action2 = manager.evaluate(
            "pos1", 150.25, t2, atr=0.5,
        )
        assert action2.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action2.exit_reason == (
            ExitReason.TAKE_PROFIT_EARLY
        )
        assert abs(action2.close_ratio - 0.20) < 0.001

    def test_insurance_not_for_trend(self) -> None:
        """TREND×DAY: 20分で0.3R到達→保険不適用"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
        )
        manager = PositionManager(config)
        self._register(manager, self.trend_day_plan)
        now = self.entry_time + timedelta(minutes=20)

        action = manager.evaluate(
            "pos1", 150.15, now, atr=0.5,
        )
        # TRENDなので保険SLは不適用
        assert "pos1" not in manager._insurance_sl_applied

    def test_insurance_skips_fast_be(self) -> None:
        """保険SL適用済み→既存fast_beは不発"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            range_day_be_disabled=True,
            range_day_fast_be_enabled=True,
            range_day_fast_be_minutes=90.0,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # Step1: 20分で0.3R到達→保険SL
        t1 = self.entry_time + timedelta(minutes=20)
        action1 = manager.evaluate(
            "pos1", 150.15, t1, atr=0.5,
        )
        assert action1.action_type == (
            ManagementActionType.UPDATE_SL
        )
        assert "pos1" in manager._insurance_sl_applied

        # Step2: 0.3Rに戻る（0.5R未到達）→HOLD
        # 保険適用済みなので早期BEはスキップ
        t2 = self.entry_time + timedelta(minutes=30)
        action2 = manager.evaluate(
            "pos1", 150.15, t2, atr=0.5,
        )
        assert action2.action_type == (
            ManagementActionType.HOLD
        )

    def test_insurance_partial_trigger_price_buy(
        self,
    ) -> None:
        """BUY保険SL→0.5R: trigger_price==150.25"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            range_day_insurance_partial_ratio=0.20,
            insurance_trigger_r=0.5,
            insurance_min_holding_minutes=0.0,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # Step1: 20分で0.3R→SL引き上げ
        t1 = self.entry_time + timedelta(minutes=20)
        manager.evaluate("pos1", 150.15, t1, atr=0.5)

        # Step2: 0.5R到達→部分利確
        t2 = self.entry_time + timedelta(minutes=25)
        action = manager.evaluate(
            "pos1", 150.25, t2, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        # entry=150.0, r_value=0.5, 0.5R=150.25
        assert action.trigger_price == pytest.approx(
            150.25, abs=0.001,
        )

    def test_insurance_immediate_trigger_price_buy(
        self,
    ) -> None:
        """BUY 0.6R一発到達: trigger_price==150.25"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            range_day_insurance_partial_ratio=0.20,
            insurance_trigger_r=0.5,
            insurance_min_holding_minutes=0.0,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # 10分で0.6R一発到達（0.3Rと0.5R同時超過）
        t1 = self.entry_time + timedelta(minutes=10)
        action = manager.evaluate(
            "pos1", 150.30, t1, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        # 0.5Rレベル = 150.25
        assert action.trigger_price == pytest.approx(
            150.25, abs=0.001,
        )

    def test_insurance_partial_trigger_price_sell(
        self,
    ) -> None:
        """SELL保険SL→0.5R: trigger_price==149.75"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            range_day_insurance_partial_ratio=0.20,
            insurance_trigger_r=0.5,
            insurance_min_holding_minutes=0.0,
        )
        manager = PositionManager(config)
        # SELLポジション登録
        manager.register_position(
            position_id="pos1",
            direction=SignalType.SELL,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=150.5,
            tp=149.0,
            volume=0.1,
            plan=self.range_day_plan,
        )

        # Step1: 20分で0.3R→SL引き上げ
        t1 = self.entry_time + timedelta(minutes=20)
        manager.evaluate("pos1", 149.85, t1, atr=0.5)

        # Step2: 0.5R到達→部分利確
        t2 = self.entry_time + timedelta(minutes=25)
        action = manager.evaluate(
            "pos1", 149.75, t2, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        # SELL: entry=150.0, 0.5R = 150.0 - 0.25 = 149.75
        assert action.trigger_price == pytest.approx(
            149.75, abs=0.001,
        )

    def test_insurance_trigger_r_raised(self) -> None:
        """0.5Rで不発、0.75Rで発火（trigger_r引き上げ）"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            insurance_trigger_r=0.7,
            insurance_min_holding_minutes=0.0,
            range_day_half_r_partial_enabled=False,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # Step1: 20分で0.3R→SL引き上げ
        t1 = self.entry_time + timedelta(minutes=20)
        manager.evaluate("pos1", 150.15, t1, atr=0.5)

        # Step2: 0.5R到達→まだ不発（trigger=0.7）
        t2 = self.entry_time + timedelta(minutes=25)
        action2 = manager.evaluate(
            "pos1", 150.25, t2, atr=0.5,
        )
        assert action2.action_type != (
            ManagementActionType.PARTIAL_CLOSE
        )

        # Step3: 0.75R到達→発火（浮動小数点誤差回避）
        t3 = self.entry_time + timedelta(minutes=28)
        # 150.0 + 0.75*0.5 = 150.375
        action3 = manager.evaluate(
            "pos1", 150.375, t3, atr=0.5,
        )
        assert action3.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action3.exit_reason == (
            ExitReason.TAKE_PROFIT_EARLY
        )
        # trigger_price = 150.0 + 0.7*0.5 = 150.35
        assert action3.trigger_price == pytest.approx(
            150.35, abs=0.001,
        )

    def test_insurance_blocked_by_high_mfe(self) -> None:
        """MFE 0.9R到達後→0.5R戻りでもブロック"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            insurance_trigger_r=0.5,
            insurance_block_high_mfe_r=0.8,
            insurance_min_holding_minutes=0.0,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # Step1: 10分で0.3R→SL引き上げ
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.15, t1, atr=0.5)

        # Step2: 0.9Rまで上昇（MFE記録）
        t2 = self.entry_time + timedelta(minutes=15)
        manager.evaluate("pos1", 150.45, t2, atr=0.5)

        # Step3: 0.5Rに戻る→MFE>=0.8でブロック
        t3 = self.entry_time + timedelta(minutes=20)
        action3 = manager.evaluate(
            "pos1", 150.25, t3, atr=0.5,
        )
        assert action3.action_type != (
            ManagementActionType.PARTIAL_CLOSE
        )

    def test_insurance_blocked_by_min_holding(
        self,
    ) -> None:
        """10分でHOLD、20分で発火（最低保有時間）"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            insurance_trigger_r=0.5,
            insurance_min_holding_minutes=15.0,
            range_day_half_r_partial_enabled=False,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # Step1: 5分で0.3R→SL引き上げ
        t1 = self.entry_time + timedelta(minutes=5)
        manager.evaluate("pos1", 150.15, t1, atr=0.5)

        # Step2: 10分で0.5R到達→保有時間不足でHOLD
        t2 = self.entry_time + timedelta(minutes=10)
        action2 = manager.evaluate(
            "pos1", 150.25, t2, atr=0.5,
        )
        assert action2.action_type != (
            ManagementActionType.PARTIAL_CLOSE
        )

        # Step3: 20分で再度0.5R→発火
        t3 = self.entry_time + timedelta(minutes=20)
        action3 = manager.evaluate(
            "pos1", 150.25, t3, atr=0.5,
        )
        assert action3.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action3.exit_reason == (
            ExitReason.TAKE_PROFIT_EARLY
        )

    def test_insurance_legacy_behavior(self) -> None:
        """全ガードOFF→従来通り0.5R発火"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            range_day_insurance_partial_ratio=0.20,
            insurance_trigger_r=0.5,
            insurance_block_high_mfe_r=999.0,
            insurance_min_holding_minutes=0.0,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # Step1: 0.3R→SL引き上げ
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.15, t1, atr=0.5)

        # Step2: 0.5R→全ガードOFFで即発火
        t2 = self.entry_time + timedelta(minutes=15)
        action = manager.evaluate(
            "pos1", 150.25, t2, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action.exit_reason == (
            ExitReason.TAKE_PROFIT_EARLY
        )

    def test_insurance_immediate_blocked_mfe(
        self,
    ) -> None:
        """即時経路もMFEブロック適用"""
        config = PositionManagerConfig(
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            insurance_trigger_r=0.5,
            insurance_block_high_mfe_r=0.8,
            insurance_min_holding_minutes=0.0,
            range_day_half_r_partial_enabled=False,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # 0.9R一発到達（即時経路）→MFE>=0.8でブロック
        t1 = self.entry_time + timedelta(minutes=10)
        action = manager.evaluate(
            "pos1", 150.45, t1, atr=0.5,
        )
        # SL引き上げのみ（部分利確はブロック）
        assert action.action_type == (
            ManagementActionType.UPDATE_SL
        )


class TestRangeDayHalfRPartial:
    """RANGE×DAY 0.5R部分利確テスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.entry_time = datetime(2024, 1, 1, 10, 0, 0)
        self.range_day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="RANGE",
        )
        self.trend_day_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="TREND",
        )

    def _register(
        self,
        manager: PositionManager,
        plan: TradingPlan,
        pos_id: str = "pos1",
    ) -> None:
        """ヘルパー: ポジション登録"""
        manager.register_position(
            position_id=pos_id,
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,
            tp=151.0,
            volume=0.1,
            plan=plan,
        )

    def test_half_r_fires_at_05r(self) -> None:
        """有効時、0.5R到達で20%部分利確+BE"""
        config = PositionManagerConfig(
            range_day_half_r_partial_enabled=True,
            range_day_half_r_partial_ratio=0.20,
            range_day_half_r_trigger=0.5,
            range_day_insurance_enabled=False,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)
        now = self.entry_time + timedelta(minutes=15)

        # 0.5R到達: 150.0 + 0.5*0.5 = 150.25
        action = manager.evaluate(
            "pos1", 150.25, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action.exit_reason == (
            ExitReason.TAKE_PROFIT_EARLY
        )
        assert abs(action.close_ratio - 0.20) < 0.001
        # BE移動
        assert action.new_sl == pytest.approx(150.04)
        # trigger_price = 150.0 + 0.5*0.5 = 150.25
        assert action.trigger_price == pytest.approx(
            150.25, abs=0.001,
        )

    def test_half_r_disabled_explicitly(self) -> None:
        """明示OFF時、0.5Rで発火しない"""
        config = PositionManagerConfig(
            range_day_half_r_partial_enabled=False,
            range_day_insurance_enabled=False,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)
        now = self.entry_time + timedelta(minutes=15)

        action = manager.evaluate(
            "pos1", 150.25, now, atr=0.5,
        )
        # デフォルトOFF → 早期BEまたはHOLD
        assert action.action_type != (
            ManagementActionType.PARTIAL_CLOSE
        )

    def test_half_r_not_for_trend(self) -> None:
        """TREND×DAYでは発火しない"""
        config = PositionManagerConfig(
            range_day_half_r_partial_enabled=True,
            range_day_half_r_partial_ratio=0.20,
            range_day_half_r_trigger=0.5,
            range_day_insurance_enabled=False,
        )
        manager = PositionManager(config)
        self._register(manager, self.trend_day_plan)
        now = self.entry_time + timedelta(minutes=15)

        action = manager.evaluate(
            "pos1", 150.25, now, atr=0.5,
        )
        # TRENDでは0.5R部分利確は発火しない
        # 早期BEが発火するはず
        assert action.exit_reason != (
            ExitReason.TAKE_PROFIT_EARLY
        ) or "RANGE" not in (action.reason or "")

    def test_half_r_blocks_insurance_partial(
        self,
    ) -> None:
        """0.5R利確後、保険部分利確はスキップ"""
        config = PositionManagerConfig(
            range_day_half_r_partial_enabled=True,
            range_day_half_r_partial_ratio=0.20,
            range_day_half_r_trigger=0.5,
            range_day_insurance_enabled=True,
            range_day_insurance_max_minutes=30.0,
            range_day_insurance_sl_offset_r=-0.1,
            insurance_trigger_r=1.0,
            insurance_min_holding_minutes=0.0,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # Step1: 0.5R到達→0.5R部分利確
        t1 = self.entry_time + timedelta(minutes=10)
        action1 = manager.evaluate(
            "pos1", 150.25, t1, atr=0.5,
        )
        assert action1.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action1.exit_reason == (
            ExitReason.TAKE_PROFIT_EARLY
        )

        # Step2: 1.0R到達→保険ではなく1R部分利確
        t2 = self.entry_time + timedelta(minutes=20)
        action2 = manager.evaluate(
            "pos1", 150.50, t2, atr=0.5,
        )
        assert action2.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action2.exit_reason == (
            ExitReason.TAKE_PROFIT_1R
        )

    def test_half_r_not_after_1r(self) -> None:
        """1R利確済みの場合、0.5Rは発火しない"""
        config = PositionManagerConfig(
            range_day_half_r_partial_enabled=True,
            range_day_half_r_partial_ratio=0.20,
            range_day_half_r_trigger=0.5,
            range_day_insurance_enabled=False,
        )
        manager = PositionManager(config)
        self._register(manager, self.range_day_plan)

        # Step1: 一気に1R到達→1R部分利確
        t1 = self.entry_time + timedelta(minutes=15)
        action1 = manager.evaluate(
            "pos1", 150.50, t1, atr=0.5,
        )
        assert action1.action_type == (
            ManagementActionType.PARTIAL_CLOSE
        )
        assert action1.exit_reason == (
            ExitReason.TAKE_PROFIT_1R
        )

        # Step2: 価格が0.5Rに戻る→0.5R部分利確は不発
        t2 = self.entry_time + timedelta(minutes=30)
        action2 = manager.evaluate(
            "pos1", 150.25, t2, atr=0.5,
        )
        # 1R済み → 0.5Rは pos_id in _partial_closed_1r
        assert action2.action_type != (
            ManagementActionType.PARTIAL_CLOSE
        ) or action2.exit_reason != (
            ExitReason.TAKE_PROFIT_EARLY
        )


class Test2021RangeImprovements:
    """2021年レンジ相場対策テスト

    - 超早期exit（MFE<0.2R + 30分経過）
    - レジームベースSTAGNATION（TREND:60分、RANGE:90分、CHOPPY:120分）
    """

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.entry_time = datetime(2024, 1, 1, 10, 0, 0)
        self.trend_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="TREND",
        )
        self.range_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="RANGE",
        )
        self.choppy_plan = TradingPlan(
            mode="UNIVERSAL",
            primary_tf="M15",
            entry_tf="M5",
            confirm_tfs=["H1"],
            manage_tf="M15",
            max_holding_bars=32,
            tp_sl_ratio_range=(1.5, 2.5),
            regime="CHOPPY",
        )

    def _register(
        self,
        manager: PositionManager,
        plan: TradingPlan,
        pos_id: str = "pos1",
    ) -> None:
        """ヘルパー: ポジション登録"""
        manager.register_position(
            position_id=pos_id,
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=self.entry_time,
            sl=149.5,  # r_value=0.5
            tp=151.0,
            volume=0.1,
            plan=plan,
        )

    def test_very_early_exit_mfe_below_02r(self) -> None:
        """超早期exit: 有効時MFE<0.2R + 30分経過で撤退"""
        # デフォルトOFF: 明示的にONにしてテスト
        config = PositionManagerConfig(
            very_early_exit_enabled=True,
        )
        manager = PositionManager(config)
        self._register(manager, self.trend_plan)

        # 30分経過、MFE<0.2R（価格=entry付近で上昇なし）
        now = self.entry_time + timedelta(minutes=35)
        action = manager.evaluate(
            "pos1", 150.0, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.FULL_CLOSE
        )
        assert action.exit_reason == ExitReason.STAGNATION
        assert "超早期exit" in action.reason

    def test_very_early_exit_disabled_by_default(self) -> None:
        """超早期exit: デフォルトOFFで発火しない"""
        manager = PositionManager()
        self._register(manager, self.trend_plan)

        # 30分経過、MFE<0.2Rでも超早期exitは発火しない
        now = self.entry_time + timedelta(minutes=35)
        action = manager.evaluate(
            "pos1", 150.0, now, atr=0.5,
        )
        assert action.action_type == ManagementActionType.HOLD

    def test_very_early_exit_skipped_with_mfe(self) -> None:
        """超早期exit: MFE>=0.2R なら発火しない"""
        config = PositionManagerConfig(
            very_early_exit_enabled=True,
        )
        manager = PositionManager(config)
        self._register(manager, self.trend_plan)

        # まず0.25Rまで上昇させてhighest_rを記録
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.125, t1, atr=0.5)

        # 35分経過、MFE=0.25R >= 0.2R → 超早期exitは発火しない
        now = self.entry_time + timedelta(minutes=35)
        action = manager.evaluate(
            "pos1", 150.0, now, atr=0.5,
        )
        # TREND 90分閾値未満 → HOLD
        assert action.action_type == ManagementActionType.HOLD

    def test_regime_stagnation_trend_90min(self) -> None:
        """TREND: 90分でSTAGNATION発火

        条件: elapsed >= 90分 AND highest_r < stagnation_min_mfe_r
        テスト: stagnation_min_mfe_r=0.30 に設定し、MFE=0.25R でテスト
        （0.25R >= 0.2R なので超早期exitは回避）
        TREND fallback: 90分（stag_trend_minutes未設定時）
        """
        config = PositionManagerConfig(
            stagnation_min_mfe_r=0.30,  # 閾値引き上げ
        )
        manager = PositionManager(config)
        self._register(manager, self.trend_plan)

        # MFE=0.25R（0.30R未満、0.2R以上なので超早期exit回避）
        # 0.25R = 0.125 → price=150.125
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.125, t1, atr=0.5)

        # 85分経過、TREND→90分閾値未到達 → HOLD
        t2 = self.entry_time + timedelta(minutes=85)
        action2 = manager.evaluate(
            "pos1", 150.0, t2, atr=0.5,
        )
        assert action2.action_type == ManagementActionType.HOLD

        # 95分経過、TREND→90分閾値を超過
        # highest_r=0.25R < 0.30R → STAGNATION発火
        now = self.entry_time + timedelta(minutes=95)
        action = manager.evaluate(
            "pos1", 150.0, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.FULL_CLOSE
        )
        assert action.exit_reason == ExitReason.STAGNATION
        assert "TREND" in action.reason

    def test_regime_stagnation_range_120min(self) -> None:
        """RANGE: 120分でSTAGNATION発火（90分では不発）

        条件: elapsed >= 120分 AND highest_r < stagnation_min_mfe_r
        RANGE fallback: 120分（stag_range_minutes未設定時）
        """
        config = PositionManagerConfig(
            stagnation_min_mfe_r=0.30,
            range_day_stagnation_enabled=False,  # 個別stagnationをOFF
        )
        manager = PositionManager(config)
        self._register(manager, self.range_plan)

        # MFE=0.25R（超早期exit回避）
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.125, t1, atr=0.5)

        # 95分経過、RANGE→120分閾値未到達 → HOLD
        t2 = self.entry_time + timedelta(minutes=95)
        action2 = manager.evaluate(
            "pos1", 150.0, t2, atr=0.5,
        )
        assert action2.action_type == ManagementActionType.HOLD

        # 125分経過、RANGE→120分閾値を超過
        now = self.entry_time + timedelta(minutes=125)
        action = manager.evaluate(
            "pos1", 150.0, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.FULL_CLOSE
        )
        assert action.exit_reason == ExitReason.STAGNATION
        assert "RANGE" in action.reason

    def test_regime_stagnation_choppy_120min(self) -> None:
        """CHOPPY: 120分でSTAGNATION発火（90分では不発）

        条件: elapsed >= 120分 AND highest_r < stagnation_min_mfe_r
        """
        config = PositionManagerConfig(
            stagnation_min_mfe_r=0.30,
        )
        manager = PositionManager(config)
        self._register(manager, self.choppy_plan)

        # MFE=0.25R（超早期exit回避）
        t1 = self.entry_time + timedelta(minutes=10)
        manager.evaluate("pos1", 150.125, t1, atr=0.5)

        # 95分経過、CHOPPY→120分閾値未到達 → HOLD
        t2 = self.entry_time + timedelta(minutes=95)
        action2 = manager.evaluate(
            "pos1", 150.0, t2, atr=0.5,
        )
        assert action2.action_type == ManagementActionType.HOLD

        # 125分経過、CHOPPY→120分閾値を超過
        now = self.entry_time + timedelta(minutes=125)
        action = manager.evaluate(
            "pos1", 150.0, now, atr=0.5,
        )
        assert action.action_type == (
            ManagementActionType.FULL_CLOSE
        )
        assert action.exit_reason == ExitReason.STAGNATION
        assert "CHOPPY" in action.reason
