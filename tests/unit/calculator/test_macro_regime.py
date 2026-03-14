"""MacroRegimeFilter のユニットテスト"""

from __future__ import annotations

import pytest

from autotrader.calculator.features.macro_regime import (
    MacroRegimeConfig,
    MacroRegimeFilter,
    MacroRegimeLevel,
)


class TestMacroRegimeFilter:
    """MacroRegimeFilter のテスト"""

    def test_initial_state(self) -> None:
        """初期状態"""
        mrf = MacroRegimeFilter()
        assert mrf.current_vix is None
        assert mrf.current_level == MacroRegimeLevel.NORMAL

    def test_normal_vix(self) -> None:
        """VIX < 20 → NORMAL"""
        mrf = MacroRegimeFilter(
            MacroRegimeConfig(enabled=True),
        )
        level = mrf.update_vix(15.0)
        assert level == MacroRegimeLevel.NORMAL
        block, reason = mrf.should_block_trade()
        assert block is False
        pen, _ = mrf.get_penalty()
        assert pen == 0.0

    def test_elevated_vix(self) -> None:
        """VIX 20-30 → ELEVATED"""
        mrf = MacroRegimeFilter(
            MacroRegimeConfig(enabled=True),
        )
        level = mrf.update_vix(25.0)
        assert level == MacroRegimeLevel.ELEVATED
        block, _ = mrf.should_block_trade()
        assert block is False
        pen, reason = mrf.get_penalty()
        assert pen == 0.1
        assert reason is not None

    def test_high_fear_vix(self) -> None:
        """VIX 30-40 → HIGH_FEAR"""
        mrf = MacroRegimeFilter(
            MacroRegimeConfig(enabled=True),
        )
        level = mrf.update_vix(35.0)
        assert level == MacroRegimeLevel.HIGH_FEAR
        block, _ = mrf.should_block_trade()
        assert block is False
        pen, _ = mrf.get_penalty()
        assert pen == 0.3

    def test_extreme_fear_vix(self) -> None:
        """VIX > 40 → EXTREME_FEAR（ブロック）"""
        mrf = MacroRegimeFilter(
            MacroRegimeConfig(enabled=True),
        )
        level = mrf.update_vix(45.0)
        assert level == MacroRegimeLevel.EXTREME_FEAR
        block, reason = mrf.should_block_trade()
        assert block is True
        assert "45.0" in reason

    def test_disabled(self) -> None:
        """無効化時はペナルティなし"""
        mrf = MacroRegimeFilter(
            MacroRegimeConfig(enabled=False),
        )
        mrf.update_vix(45.0)
        block, _ = mrf.should_block_trade()
        assert block is False
        pen, _ = mrf.get_penalty()
        assert pen == 0.0

    def test_custom_thresholds(self) -> None:
        """カスタム閾値"""
        mrf = MacroRegimeFilter(
            MacroRegimeConfig(
                enabled=True,
                vix_elevated_threshold=15.0,
                vix_high_fear_threshold=25.0,
                vix_extreme_fear_threshold=35.0,
            ),
        )
        assert mrf.update_vix(16.0) == MacroRegimeLevel.ELEVATED
        assert mrf.update_vix(26.0) == MacroRegimeLevel.HIGH_FEAR
        assert mrf.update_vix(36.0) == MacroRegimeLevel.EXTREME_FEAR

    def test_boundary_values(self) -> None:
        """境界値テスト"""
        mrf = MacroRegimeFilter(
            MacroRegimeConfig(enabled=True),
        )
        # 閾値ちょうど = そのレベル
        assert mrf.update_vix(20.0) == MacroRegimeLevel.ELEVATED
        assert mrf.update_vix(30.0) == MacroRegimeLevel.HIGH_FEAR
        assert mrf.update_vix(40.0) == MacroRegimeLevel.EXTREME_FEAR
        # 閾値未満 = 1段下
        assert mrf.update_vix(19.9) == MacroRegimeLevel.NORMAL

    def test_get_status_dict(self) -> None:
        """辞書変換"""
        mrf = MacroRegimeFilter(
            MacroRegimeConfig(enabled=True),
        )
        mrf.update_vix(25.0)
        d = mrf.get_status_dict()
        assert d["macro_vix"] == 25.0
        assert d["macro_regime_level"] == "elevated"

    def test_no_vix_data(self) -> None:
        """VIXデータなし時はブロックもペナルティもなし"""
        mrf = MacroRegimeFilter(
            MacroRegimeConfig(enabled=True),
        )
        # update_vix を呼ばない
        block, _ = mrf.should_block_trade()
        assert block is False
        pen, _ = mrf.get_penalty()
        assert pen == 0.0
