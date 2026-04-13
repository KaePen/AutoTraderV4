"""EntryGateChecker ユニットテスト"""

from __future__ import annotations

import pytest

from autotrader.constraint.entry_gate import (
    EntryGateChecker,
    EntryGateContext,
)
from autotrader.core.entities import SignalType


def _make_ctx(**overrides) -> EntryGateContext:
    """デフォルト値でEntryGateContextを生成するヘルパー"""
    defaults = dict(
        signal_direction=SignalType.BUY,
        consensus_score=10.0,
        symbol_position_count=0,
        global_position_count=0,
        global_exposure_lot=0.0,
        jpy_same_direction_count=0,
        max_positions=3,
        bonus_max_positions=0,
        bonus_score_threshold=7.0,
        global_max_positions=0,
        global_max_exposure_lot=0.0,
        max_same_direction_jpy=0,
        is_jpy_pair=False,
        current_spread_pips=1.0,
        spread_threshold_pips=None,
        dd_emergency_active=False,
        margin_usage_pct=0.0,
        margin_limit_pct=0.0,
    )
    defaults.update(overrides)
    return EntryGateContext(**defaults)


class TestEntryGateChecker:
    """EntryGateChecker の全7ゲートテスト"""

    def setup_method(self) -> None:
        self.checker = EntryGateChecker()

    # --- Gate 1: DD緊急停止 ---

    def test_dd_emergency_denies(self) -> None:
        ctx = _make_ctx(dd_emergency_active=True)
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "dd_emergency"

    def test_dd_emergency_inactive_allows(self) -> None:
        ctx = _make_ctx(dd_emergency_active=False)
        result = self.checker.evaluate(ctx)
        assert result.allowed

    # --- Gate 2: シンボルポジション上限 ---

    def test_symbol_position_limit_denies(self) -> None:
        ctx = _make_ctx(symbol_position_count=3, max_positions=3)
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "symbol_position_limit"

    def test_symbol_position_under_limit_allows(self) -> None:
        ctx = _make_ctx(symbol_position_count=2, max_positions=3)
        result = self.checker.evaluate(ctx)
        assert result.allowed

    def test_bonus_positions_expand_limit(self) -> None:
        ctx = _make_ctx(
            symbol_position_count=3,
            max_positions=3,
            bonus_max_positions=2,
            bonus_score_threshold=7.0,
            consensus_score=8.0,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    def test_bonus_positions_not_triggered_below_threshold(self) -> None:
        ctx = _make_ctx(
            symbol_position_count=3,
            max_positions=3,
            bonus_max_positions=2,
            bonus_score_threshold=7.0,
            consensus_score=6.0,
        )
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "symbol_position_limit"

    def test_bonus_positions_skipped_when_consensus_none(self) -> None:
        ctx = _make_ctx(
            symbol_position_count=3,
            max_positions=3,
            bonus_max_positions=2,
            consensus_score=None,
        )
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "symbol_position_limit"

    # --- Gate 3: グローバルポジション上限 ---

    def test_global_position_limit_denies(self) -> None:
        ctx = _make_ctx(
            global_position_count=6,
            global_max_positions=6,
        )
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "global_position_limit"

    def test_global_position_limit_zero_means_unlimited(self) -> None:
        ctx = _make_ctx(
            global_position_count=100,
            global_max_positions=0,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    def test_global_position_under_limit_allows(self) -> None:
        ctx = _make_ctx(
            global_position_count=5,
            global_max_positions=6,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    # --- Gate 4: グローバルエクスポージャー上限 ---

    def test_global_exposure_limit_denies(self) -> None:
        ctx = _make_ctx(
            global_exposure_lot=10.0,
            global_max_exposure_lot=10.0,
        )
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "global_exposure_limit"

    def test_global_exposure_zero_means_unlimited(self) -> None:
        ctx = _make_ctx(
            global_exposure_lot=100.0,
            global_max_exposure_lot=0.0,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    # --- Gate 5: JPY同方向制限 ---

    def test_jpy_direction_limit_denies(self) -> None:
        ctx = _make_ctx(
            is_jpy_pair=True,
            max_same_direction_jpy=3,
            jpy_same_direction_count=3,
        )
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "jpy_direction_limit"

    def test_jpy_direction_limit_not_jpy_pair_allows(self) -> None:
        ctx = _make_ctx(
            is_jpy_pair=False,
            max_same_direction_jpy=3,
            jpy_same_direction_count=3,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    def test_jpy_direction_limit_zero_means_unlimited(self) -> None:
        ctx = _make_ctx(
            is_jpy_pair=True,
            max_same_direction_jpy=0,
            jpy_same_direction_count=10,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    # --- Gate 6: スプレッドゲート ---

    def test_spread_gate_denies(self) -> None:
        ctx = _make_ctx(
            current_spread_pips=3.5,
            spread_threshold_pips=3.0,
        )
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "spread_gate"

    def test_spread_gate_at_threshold_allows(self) -> None:
        ctx = _make_ctx(
            current_spread_pips=3.0,
            spread_threshold_pips=3.0,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    def test_spread_gate_none_threshold_allows(self) -> None:
        ctx = _make_ctx(
            current_spread_pips=100.0,
            spread_threshold_pips=None,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    # --- Gate 7: マージンチェック ---

    def test_margin_limit_denies(self) -> None:
        ctx = _make_ctx(
            margin_usage_pct=85.0,
            margin_limit_pct=80.0,
        )
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "insufficient_margin"

    def test_margin_limit_zero_means_disabled(self) -> None:
        ctx = _make_ctx(
            margin_usage_pct=99.0,
            margin_limit_pct=0.0,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    # --- ゲート優先順序テスト ---

    def test_first_failing_gate_wins(self) -> None:
        """DD緊急停止とポジション上限の両方が該当する場合、DD緊急停止が優先"""
        ctx = _make_ctx(
            dd_emergency_active=True,
            symbol_position_count=3,
            max_positions=3,
        )
        result = self.checker.evaluate(ctx)
        assert result.deny_code == "dd_emergency"

    def test_all_gates_pass_allows(self) -> None:
        """全ゲート通過時はallowed"""
        ctx = _make_ctx(
            dd_emergency_active=False,
            symbol_position_count=1,
            max_positions=3,
            global_position_count=2,
            global_max_positions=6,
            global_exposure_lot=1.0,
            global_max_exposure_lot=10.0,
            is_jpy_pair=True,
            max_same_direction_jpy=3,
            jpy_same_direction_count=1,
            current_spread_pips=1.0,
            spread_threshold_pips=3.0,
            margin_usage_pct=50.0,
            margin_limit_pct=80.0,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    # --- 境界値テスト ---

    def test_position_count_exactly_at_limit_denies(self) -> None:
        ctx = _make_ctx(symbol_position_count=3, max_positions=3)
        result = self.checker.evaluate(ctx)
        assert not result.allowed

    def test_position_count_one_below_limit_allows(self) -> None:
        ctx = _make_ctx(symbol_position_count=2, max_positions=3)
        result = self.checker.evaluate(ctx)
        assert result.allowed

    def test_consensus_exactly_at_bonus_threshold(self) -> None:
        """consensus_score がちょうど bonus_score_threshold の場合、ボーナス発動"""
        ctx = _make_ctx(
            symbol_position_count=3,
            max_positions=3,
            bonus_max_positions=1,
            bonus_score_threshold=7.0,
            consensus_score=7.0,
        )
        result = self.checker.evaluate(ctx)
        assert result.allowed

    def test_spread_just_above_threshold_denies(self) -> None:
        ctx = _make_ctx(
            current_spread_pips=3.01,
            spread_threshold_pips=3.0,
        )
        result = self.checker.evaluate(ctx)
        assert not result.allowed
        assert result.deny_code == "spread_gate"
