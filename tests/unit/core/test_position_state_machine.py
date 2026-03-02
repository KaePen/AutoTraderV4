"""PositionStateMachine テスト

有効遷移・不正遷移・終端状態の検証。
"""

from __future__ import annotations

import pytest

from autotrader.core.entities import (
    PositionState,
    PositionStateMachine,
    VALID_TRANSITIONS,
)
from autotrader.core.exceptions import InvalidTransitionError


class TestPositionState:
    """PositionState Enum テスト"""

    def test_values(self) -> None:
        """全ての状態値が正しいこと"""
        assert PositionState.PENDING == "pending"
        assert PositionState.OPEN == "open"
        assert PositionState.TRAILING == "trailing"
        assert PositionState.PARTIAL_CLOSED == "partial_closed"
        assert PositionState.CLOSED == "closed"

    def test_is_str_enum(self) -> None:
        """str Enumであること"""
        assert isinstance(PositionState.OPEN, str)


class TestValidTransitions:
    """VALID_TRANSITIONS マップテスト"""

    def test_all_states_have_entry(self) -> None:
        """全状態に遷移マップが存在すること"""
        for state in PositionState:
            assert state in VALID_TRANSITIONS

    def test_closed_is_terminal(self) -> None:
        """CLOSED状態からは遷移不可"""
        assert VALID_TRANSITIONS[PositionState.CLOSED] == set()

    def test_pending_transitions(self) -> None:
        """PENDING → OPEN or CLOSED のみ"""
        expected = {PositionState.OPEN, PositionState.CLOSED}
        assert VALID_TRANSITIONS[PositionState.PENDING] == expected

    def test_open_transitions(self) -> None:
        """OPEN → TRAILING, PARTIAL_CLOSED, CLOSED"""
        expected = {
            PositionState.TRAILING,
            PositionState.PARTIAL_CLOSED,
            PositionState.CLOSED,
        }
        assert VALID_TRANSITIONS[PositionState.OPEN] == expected

    def test_trailing_transitions(self) -> None:
        """TRAILING → PARTIAL_CLOSED, CLOSED"""
        expected = {
            PositionState.PARTIAL_CLOSED,
            PositionState.CLOSED,
        }
        assert (
            VALID_TRANSITIONS[PositionState.TRAILING]
            == expected
        )

    def test_partial_closed_transitions(self) -> None:
        """PARTIAL_CLOSED → CLOSED のみ"""
        expected = {PositionState.CLOSED}
        assert (
            VALID_TRANSITIONS[PositionState.PARTIAL_CLOSED]
            == expected
        )


class TestPositionStateMachine:
    """PositionStateMachine テスト"""

    def test_initial_state_default(self) -> None:
        """デフォルト初期状態はPENDING"""
        sm = PositionStateMachine()
        assert sm.state == PositionState.PENDING

    def test_initial_state_custom(self) -> None:
        """カスタム初期状態を指定可能"""
        sm = PositionStateMachine(PositionState.OPEN)
        assert sm.state == PositionState.OPEN

    def test_valid_transition_pending_to_open(self) -> None:
        """PENDING → OPEN は有効"""
        sm = PositionStateMachine(PositionState.PENDING)
        sm.transition(PositionState.OPEN)
        assert sm.state == PositionState.OPEN

    def test_valid_transition_open_to_trailing(self) -> None:
        """OPEN → TRAILING は有効"""
        sm = PositionStateMachine(PositionState.OPEN)
        sm.transition(PositionState.TRAILING)
        assert sm.state == PositionState.TRAILING

    def test_valid_transition_open_to_closed(self) -> None:
        """OPEN → CLOSED は有効"""
        sm = PositionStateMachine(PositionState.OPEN)
        sm.transition(PositionState.CLOSED)
        assert sm.state == PositionState.CLOSED

    def test_valid_transition_trailing_to_closed(
        self,
    ) -> None:
        """TRAILING → CLOSED は有効"""
        sm = PositionStateMachine(PositionState.TRAILING)
        sm.transition(PositionState.CLOSED)
        assert sm.state == PositionState.CLOSED

    def test_valid_transition_partial_to_closed(
        self,
    ) -> None:
        """PARTIAL_CLOSED → CLOSED は有効"""
        sm = PositionStateMachine(
            PositionState.PARTIAL_CLOSED,
        )
        sm.transition(PositionState.CLOSED)
        assert sm.state == PositionState.CLOSED

    def test_invalid_transition_closed_to_open(
        self,
    ) -> None:
        """CLOSED → OPEN は不正"""
        sm = PositionStateMachine(PositionState.CLOSED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(PositionState.OPEN)

    def test_invalid_transition_closed_to_trailing(
        self,
    ) -> None:
        """CLOSED → TRAILING は不正"""
        sm = PositionStateMachine(PositionState.CLOSED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(PositionState.TRAILING)

    def test_invalid_transition_pending_to_trailing(
        self,
    ) -> None:
        """PENDING → TRAILING は不正"""
        sm = PositionStateMachine(PositionState.PENDING)
        with pytest.raises(InvalidTransitionError):
            sm.transition(PositionState.TRAILING)

    def test_invalid_transition_partial_to_open(
        self,
    ) -> None:
        """PARTIAL_CLOSED → OPEN は不正"""
        sm = PositionStateMachine(
            PositionState.PARTIAL_CLOSED,
        )
        with pytest.raises(InvalidTransitionError):
            sm.transition(PositionState.OPEN)

    def test_can_transition_true(self) -> None:
        """can_transition: 遷移可能"""
        sm = PositionStateMachine(PositionState.OPEN)
        assert sm.can_transition(PositionState.TRAILING)

    def test_can_transition_false(self) -> None:
        """can_transition: 遷移不可"""
        sm = PositionStateMachine(PositionState.CLOSED)
        assert not sm.can_transition(PositionState.OPEN)

    def test_is_terminal_closed(self) -> None:
        """CLOSEDは終端状態"""
        sm = PositionStateMachine(PositionState.CLOSED)
        assert sm.is_terminal

    def test_is_terminal_open(self) -> None:
        """OPENは終端ではない"""
        sm = PositionStateMachine(PositionState.OPEN)
        assert not sm.is_terminal

    def test_full_lifecycle(self) -> None:
        """PENDING → OPEN → TRAILING → PARTIAL_CLOSED → CLOSED"""
        sm = PositionStateMachine(PositionState.PENDING)
        sm.transition(PositionState.OPEN)
        sm.transition(PositionState.TRAILING)
        sm.transition(PositionState.PARTIAL_CLOSED)
        sm.transition(PositionState.CLOSED)
        assert sm.state == PositionState.CLOSED
        assert sm.is_terminal

    def test_error_message_content(self) -> None:
        """エラーメッセージに状態情報が含まれること"""
        sm = PositionStateMachine(PositionState.CLOSED)
        with pytest.raises(
            InvalidTransitionError, match="closed",
        ):
            sm.transition(PositionState.OPEN)
