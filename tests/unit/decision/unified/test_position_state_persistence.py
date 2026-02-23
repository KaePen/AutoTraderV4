"""PositionManager export/import 状態永続化テスト"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autotrader.core.enums import SignalType, TradingStrategyMode
from autotrader.decision.unified.mode_selector import TradingPlan
from autotrader.decision.unified.position_manager import (
    ManagementActionType,
    PositionManager,
    PositionManagerConfig,
)


def _make_plan() -> TradingPlan:
    """テスト用プランを作成"""
    return TradingPlan(
        mode=TradingStrategyMode.UNIVERSAL,
        primary_tf="M15",
        entry_tf="M15",
        confirm_tfs=["H1", "H4"],
        manage_tf="M15",
        max_holding_bars=32,
        tp_sl_ratio_range=(1.1, 1.4),
        selection_reason="test",
    )


class TestExportImportRoundTrip:
    """export_state / import_state のラウンドトリップ検証"""

    def test_export_returns_none_for_unknown(self) -> None:
        """未登録ポジションのexportはNone"""
        pm = PositionManager()
        assert pm.export_state("unknown") is None

    def test_import_ignores_unknown(self) -> None:
        """未登録ポジションへのimportは無視"""
        pm = PositionManager()
        # エラーにならないことを確認
        pm.import_state("unknown", {"highest_r": 2.0})

    def test_basic_round_trip(self) -> None:
        """基本的なexport→importラウンドトリップ"""
        pm1 = PositionManager(PositionManagerConfig(
            breakeven_at_1r=True,
            disable_tp_after_partial=True,
        ))
        pm1.register_position(
            position_id="100",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=datetime(
                2024, 1, 1, tzinfo=timezone.utc,
            ),
            sl=149.0,
            tp=153.0,
            volume=0.1,
            plan=_make_plan(),
        )

        # 1R到達で部分利確を発生させる
        pm1.evaluate(
            "100", 151.0,
            datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
            atr=0.3,
        )
        state = pm1.export_state("100")

        assert state is not None
        assert state["position_id"] == "100"
        assert state["highest_price"] == 151.0
        assert state["highest_r"] >= 1.0
        assert state["partial_closed_1r"] is True
        assert state["tp_disabled"] is True
        assert state["bars_held"] == 1

        # 新PMでインポート
        pm2 = PositionManager(PositionManagerConfig(
            breakeven_at_1r=True,
            disable_tp_after_partial=True,
        ))
        pm2.register_position(
            position_id="100",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=datetime(
                2024, 1, 1, tzinfo=timezone.utc,
            ),
            sl=149.0,
            tp=153.0,
            volume=0.1,
            plan=_make_plan(),
        )
        pm2.import_state("100", state)

        # インポート後に1Rの部分利確が再発しないことを確認
        action = pm2.evaluate(
            "100", 151.0,
            datetime(2024, 1, 1, 2, tzinfo=timezone.utc),
            atr=0.3,
        )
        # 1Rは既にフラグ済みのため、部分利確ではない
        assert action.action_type != ManagementActionType.PARTIAL_CLOSE \
            or "1R" not in action.reason

    def test_all_seven_flags_round_trip(self) -> None:
        """全7フラグのラウンドトリップ検証"""
        pm = PositionManager()
        pm.register_position(
            position_id="200",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=datetime(
                2024, 1, 1, tzinfo=timezone.utc,
            ),
            sl=149.0,
            tp=153.0,
            volume=0.1,
            plan=_make_plan(),
        )

        # 全フラグを手動セット
        pm._partial_closed_1r.add("200")
        pm._partial_closed_2r.add("200")
        pm._tp_disabled.add("200")
        pm._early_be_applied.add("200")
        pm._insurance_sl_applied.add("200")
        pm._insurance_partial_applied.add("200")
        pm._half_r_partial_applied.add("200")

        # 追跡値を手動設定
        pos = pm.get_position("200")
        pos.highest_price = 152.5
        pos.lowest_price = 149.5
        pos.highest_r = 2.5
        pos.bars_held = 42
        pos.trailing_activated = True

        state = pm.export_state("200")
        assert state is not None
        # 全フラグがTrue
        assert state["partial_closed_1r"] is True
        assert state["partial_closed_2r"] is True
        assert state["tp_disabled"] is True
        assert state["early_be_applied"] is True
        assert state["insurance_sl_applied"] is True
        assert state["insurance_partial_applied"] is True
        assert state["half_r_partial_applied"] is True
        # 追跡値
        assert state["highest_price"] == 152.5
        assert state["lowest_price"] == 149.5
        assert state["highest_r"] == 2.5
        assert state["bars_held"] == 42
        assert state["trailing_activated"] is True

        # 新PMにインポート
        pm2 = PositionManager()
        pm2.register_position(
            position_id="200",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=datetime(
                2024, 1, 1, tzinfo=timezone.utc,
            ),
            sl=149.0,
            tp=153.0,
            volume=0.1,
            plan=_make_plan(),
        )
        pm2.import_state("200", state)

        # フラグ確認
        assert "200" in pm2._partial_closed_1r
        assert "200" in pm2._partial_closed_2r
        assert "200" in pm2._tp_disabled
        assert "200" in pm2._early_be_applied
        assert "200" in pm2._insurance_sl_applied
        assert "200" in pm2._insurance_partial_applied
        assert "200" in pm2._half_r_partial_applied

        # 追跡値確認
        pos2 = pm2.get_position("200")
        assert pos2.highest_price == 152.5
        assert pos2.lowest_price == 149.5
        assert pos2.highest_r == 2.5
        assert pos2.bars_held == 42
        assert pos2.trailing_activated is True

    def test_sell_direction_export(self) -> None:
        """SELL方向のexport確認"""
        pm = PositionManager()
        pm.register_position(
            position_id="300",
            direction=SignalType.SELL,
            entry_price=150.0,
            entry_time=datetime(
                2024, 1, 1, tzinfo=timezone.utc,
            ),
            sl=151.0,
            tp=147.0,
            volume=0.1,
            plan=_make_plan(),
        )

        # 価格が下がる → highest_rが上がる
        pm.evaluate(
            "300", 149.0,
            datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
            atr=0.3,
        )
        state = pm.export_state("300")
        assert state is not None
        assert state["lowest_price"] == 149.0
        assert state["highest_r"] >= 1.0


class TestUnregisterBugFix:
    """unregister_positionのバグ修正検証"""

    def test_unregister_clears_all_sets(self) -> None:
        """全setからdiscardされることを確認"""
        pm = PositionManager()
        pm.register_position(
            position_id="400",
            direction=SignalType.BUY,
            entry_price=150.0,
            entry_time=datetime(
                2024, 1, 1, tzinfo=timezone.utc,
            ),
            sl=149.0,
            tp=153.0,
            volume=0.1,
            plan=_make_plan(),
        )
        # 全setに追加
        pm._partial_closed_1r.add("400")
        pm._partial_closed_2r.add("400")
        pm._early_be_applied.add("400")
        pm._tp_disabled.add("400")
        pm._insurance_sl_applied.add("400")
        pm._insurance_partial_applied.add("400")
        pm._half_r_partial_applied.add("400")

        pm.unregister_position("400")

        # 全setから削除されていること
        assert "400" not in pm._partial_closed_1r
        assert "400" not in pm._partial_closed_2r
        assert "400" not in pm._early_be_applied
        assert "400" not in pm._tp_disabled
        assert "400" not in pm._insurance_sl_applied
        assert "400" not in pm._insurance_partial_applied
        assert "400" not in pm._half_r_partial_applied
        assert pm.get_position("400") is None
