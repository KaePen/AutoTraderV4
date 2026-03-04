"""M1実行ゲートフィルタのユニットテスト."""

from __future__ import annotations

import pandas as pd
import pytest

from autotrader.constraint.filters.m1_execution_gate import (
    M1ExecutionGate,
    M1ExecutionGateConfig,
)
from autotrader.core.enums import SignalType


def _make_row(**kwargs: object) -> pd.Series:
    """テスト用M1行データを生成."""
    defaults = {
        "close": 150.0,
        "open": 149.9,
        "ema_12": 150.1,
        "ema_26": 149.8,
        "sma_20": 150.0,
        "bb_width": 1.0,
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


class TestM1ExecutionGateDisabled:
    """無効時のテスト."""

    def test_disabled_always_passes(self) -> None:
        """無効時は常にpassする."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(enabled=False),
        )
        result = gate.check(
            SignalType.BUY, _make_row(),
        )
        assert result.passed is True
        assert result.reason == "ゲート無効"


class TestM1ExecutionGateNoneData:
    """Noneデータのテスト."""

    def test_none_row_passes(self) -> None:
        """M1データなしの場合はpassする."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(enabled=True),
        )
        result = gate.check(SignalType.BUY, None)
        assert result.passed is True
        assert result.reason == "M1データなし"

    def test_hold_direction_passes(self) -> None:
        """HOLD方向の場合はpassする."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(enabled=True),
        )
        result = gate.check(
            SignalType.HOLD, _make_row(),
        )
        assert result.passed is True


class TestM1ExecutionGateBuy:
    """BUYシグナルのテスト."""

    def test_buy_all_conditions_met(self) -> None:
        """BUY: 全条件一致 → pass."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True, threshold=1.0,
            ),
        )
        # close(150) > ema_26(149.8) ✓
        # ema_12(150.1) > ema_26(149.8) ✓
        # close(150) > open(149.9) → 陽線 ✓
        # pct_b=(150-150+0.5)/1.0=0.5 in [0.3, 0.7] ✓
        row = _make_row(
            close=150.0,
            open=149.9,
            ema_12=150.1,
            ema_26=149.8,
            sma_20=150.0,
            bb_width=1.0,
        )
        result = gate.check(SignalType.BUY, row)
        assert result.passed is True
        assert result.ema_aligned is True
        assert result.bar_momentum is True
        assert result.bb_healthy is True
        # スコアは1.0+0.5+0.5=2.0
        assert result.score == pytest.approx(2.0)

    def test_buy_no_conditions_met(self) -> None:
        """BUY: 条件不一致 → fail."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True, threshold=1.0,
            ),
        )
        # close(149.5) < ema_26(150.0) ✗
        # ema_12(149.7) < ema_26(150.0) ✗
        # close(149.5) < open(150.0) → 陰線 ✗
        # pct_b=(149.5-150+0.5)/1.0=0.0 < 0.3 ✗
        row = _make_row(
            close=149.5,
            open=150.0,
            ema_12=149.7,
            ema_26=150.0,
            sma_20=150.0,
            bb_width=1.0,
        )
        result = gate.check(SignalType.BUY, row)
        assert result.passed is False
        assert result.score == pytest.approx(0.0)

    def test_buy_ema_only(self) -> None:
        """BUY: EMAのみ一致 → スコア1.0."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True, threshold=1.0,
            ),
        )
        row = _make_row(
            close=150.0,
            open=150.1,  # 陰線
            ema_12=150.1,
            ema_26=149.8,
            sma_20=151.0,  # pct_b=(150-151+0.5)/1.0=-0.5 BB外
            bb_width=1.0,
        )
        result = gate.check(SignalType.BUY, row)
        assert result.passed is True
        assert result.ema_aligned is True
        assert result.bar_momentum is False
        assert result.bb_healthy is False
        assert result.score == pytest.approx(1.0)


class TestM1ExecutionGateSell:
    """SELLシグナルのテスト."""

    def test_sell_all_conditions_met(self) -> None:
        """SELL: 全条件一致 → pass."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True, threshold=1.0,
            ),
        )
        # close(149.5) < ema_26(150.0) ✓
        # ema_12(149.7) < ema_26(150.0) ✓
        # close(149.5) < open(150.0) → 陰線 ✓
        # pct_b=(149.5-149.5+0.5)/1.0=0.5 in [0.3, 0.7] ✓
        row = _make_row(
            close=149.5,
            open=150.0,
            ema_12=149.7,
            ema_26=150.0,
            sma_20=149.5,
            bb_width=1.0,
        )
        result = gate.check(SignalType.SELL, row)
        assert result.passed is True
        assert result.ema_aligned is True
        assert result.bar_momentum is True
        assert result.bb_healthy is True

    def test_sell_misaligned(self) -> None:
        """SELL: EMA上向き → EMA不一致."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True, threshold=1.0,
            ),
        )
        row = _make_row(
            close=150.5,
            open=150.0,
            ema_12=150.3,
            ema_26=149.8,
            sma_20=150.5,
            bb_width=1.0,
        )
        result = gate.check(SignalType.SELL, row)
        assert result.ema_aligned is False


class TestM1ExecutionGateThreshold:
    """閾値のテスト."""

    def test_threshold_exact(self) -> None:
        """閾値ちょうど → pass."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True,
                threshold=1.5,
                ema_weight=1.0,
                bar_weight=0.5,
                bb_weight=0.5,
            ),
        )
        # EMA(1.0) + bar(0.5) = 1.5 == threshold
        row = _make_row(
            close=150.0,
            open=149.9,
            ema_12=150.1,
            ema_26=149.8,
            sma_20=151.0,  # pct_b=-0.5 BB外
            bb_width=1.0,
        )
        result = gate.check(SignalType.BUY, row)
        assert result.passed is True
        assert result.score == pytest.approx(1.5)

    def test_threshold_just_below(self) -> None:
        """閾値未満 → fail."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True,
                threshold=1.6,
                ema_weight=1.0,
                bar_weight=0.5,
            ),
        )
        # EMA(1.0) + bar(0.5) = 1.5 < 1.6
        row = _make_row(
            close=150.0,
            open=149.9,
            ema_12=150.1,
            ema_26=149.8,
            sma_20=151.0,  # pct_b=-0.5 BB外
            bb_width=1.0,
        )
        result = gate.check(SignalType.BUY, row)
        assert result.passed is False


class TestM1ExecutionGateNaN:
    """NaNデータのテスト."""

    def test_nan_ema(self) -> None:
        """EMAがNaN → EMA不一致."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True, threshold=0.1,
            ),
        )
        row = _make_row(
            ema_12=float("nan"),
        )
        result = gate.check(SignalType.BUY, row)
        assert result.ema_aligned is False

    def test_nan_bb(self) -> None:
        """BBがNaN → BB不健全."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True, threshold=0.1,
            ),
        )
        row = _make_row(
            bb_width=float("nan"),
        )
        result = gate.check(SignalType.BUY, row)
        assert result.bb_healthy is False

    def test_missing_columns(self) -> None:
        """列欠損 → 各条件False."""
        gate = M1ExecutionGate(
            M1ExecutionGateConfig(
                enabled=True, threshold=0.1,
            ),
        )
        row = pd.Series({"close": 150.0})
        result = gate.check(SignalType.BUY, row)
        assert result.ema_aligned is False
        assert result.bar_momentum is False
        assert result.bb_healthy is False
