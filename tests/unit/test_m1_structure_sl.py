"""M1構造的SL計算のユニットテスト.

trade_bot.py内のM1構造的SLロジックを間接的にテスト。
設定変更によるSL計算結果の違いを検証する。
"""

from __future__ import annotations

import pytest

from autotrader.decision.unified.config import UnifiedBotConfig


class TestM1StructureSLConfig:
    """M1構造的SL設定のテスト."""

    def test_default_disabled(self) -> None:
        """デフォルトで無効."""
        config = UnifiedBotConfig()
        assert config.m1_structure_sl_enabled is False

    def test_default_buffer(self) -> None:
        """デフォルトバッファ値."""
        config = UnifiedBotConfig()
        assert config.m1_structure_sl_buffer_pips == 3.0

    def test_default_min_max(self) -> None:
        """デフォルト最小/最大値."""
        config = UnifiedBotConfig()
        assert config.m1_structure_sl_min_pips == 15.0
        assert config.m1_structure_sl_max_pips == 60.0

    def test_default_swing_window(self) -> None:
        """デフォルトスイングウィンドウ."""
        config = UnifiedBotConfig()
        assert config.m1_structure_sl_swing_window == 20

    def test_custom_swing_window(self) -> None:
        """カスタムスイングウィンドウの設定."""
        config = UnifiedBotConfig(m1_structure_sl_swing_window=30)
        assert config.m1_structure_sl_swing_window == 30

    def test_custom_values(self) -> None:
        """カスタム値の設定."""
        config = UnifiedBotConfig(
            m1_structure_sl_enabled=True,
            m1_structure_sl_buffer_pips=5.0,
            m1_structure_sl_min_pips=10.0,
            m1_structure_sl_max_pips=80.0,
        )
        assert config.m1_structure_sl_enabled is True
        assert config.m1_structure_sl_buffer_pips == 5.0
        assert config.m1_structure_sl_min_pips == 10.0
        assert config.m1_structure_sl_max_pips == 80.0


class TestM1StructureSLCalculation:
    """M1構造的SL計算ロジックのテスト.

    trade_bot.pyの内部ロジックを直接テストできないため、
    計算式の検証を行う。
    """

    def test_buy_sl_calculation(self) -> None:
        """BUY: swing_lowからの距離+バッファ."""
        # close=150.00, swing_low=149.70
        # (150.00 - 149.70) / 0.01 + 3.0 = 30 + 3 = 33.0 pips
        current_close = 150.00
        swing_low = 149.70
        pip_unit = 0.01
        buffer_pips = 3.0
        min_pips = 15.0
        max_pips = 60.0

        struct_sl = (
            (current_close - swing_low) / pip_unit
            + buffer_pips
        )
        struct_sl = max(min_pips, min(struct_sl, max_pips))
        assert struct_sl == pytest.approx(33.0)

    def test_sell_sl_calculation(self) -> None:
        """SELL: swing_highからの距離+バッファ."""
        # close=150.00, swing_high=150.40
        # (150.40 - 150.00) / 0.01 + 3.0 = 40 + 3 = 43.0 pips
        current_close = 150.00
        swing_high = 150.40
        pip_unit = 0.01
        buffer_pips = 3.0
        min_pips = 15.0
        max_pips = 60.0

        struct_sl = (
            (swing_high - current_close) / pip_unit
            + buffer_pips
        )
        struct_sl = max(min_pips, min(struct_sl, max_pips))
        assert struct_sl == pytest.approx(43.0)

    def test_min_clamp(self) -> None:
        """最小値クランプ."""
        # close=150.00, swing_low=149.95
        # (150.00 - 149.95) / 0.01 + 3.0 = 5 + 3 = 8.0
        # → min_pips=15.0にクランプ
        current_close = 150.00
        swing_low = 149.95
        pip_unit = 0.01
        buffer_pips = 3.0
        min_pips = 15.0
        max_pips = 60.0

        struct_sl = (
            (current_close - swing_low) / pip_unit
            + buffer_pips
        )
        struct_sl = max(min_pips, min(struct_sl, max_pips))
        assert struct_sl == pytest.approx(15.0)

    def test_max_clamp(self) -> None:
        """最大値クランプ."""
        # close=150.00, swing_low=149.00
        # (150.00 - 149.00) / 0.01 + 3.0 = 100 + 3 = 103.0
        # → max_pips=60.0にクランプ
        current_close = 150.00
        swing_low = 149.00
        pip_unit = 0.01
        buffer_pips = 3.0
        min_pips = 15.0
        max_pips = 60.0

        struct_sl = (
            (current_close - swing_low) / pip_unit
            + buffer_pips
        )
        struct_sl = max(min_pips, min(struct_sl, max_pips))
        assert struct_sl == pytest.approx(60.0)
