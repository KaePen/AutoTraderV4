"""SoftGuard ペア別スプレッド閾値のユニットテスト"""

from __future__ import annotations

import pytest

from autotrader.constraint.soft_guard import (
    SoftGuard,
    SoftGuardConfig,
)


class TestSoftGuardSpreadThreshold:
    """SoftGuard ペア別スプレッド閾値テスト"""

    def test_default_threshold(self) -> None:
        """デフォルト閾値（2.0pips）"""
        sg = SoftGuard(SoftGuardConfig(spread_threshold_pips=2.0))
        # 2.0pips以下 → ペナルティなし
        pen, reason = sg.check_spread({"spread_pips": 1.5})
        assert pen == 0.0

        # 2.5pips → ペナルティあり
        pen, reason = sg.check_spread({"spread_pips": 2.5})
        assert pen > 0

    def test_per_pair_threshold_from_context(self) -> None:
        """コンテキストからペア別閾値を使用"""
        sg = SoftGuard(SoftGuardConfig(spread_threshold_pips=2.0))

        # GBPJPY: 通常スプレッド3.0pips、閾値4.5pips
        # 3.0pips → ペナルティなし（4.5閾値未満）
        pen, reason = sg.check_spread({
            "spread_pips": 3.0,
            "sg_spread_threshold_pips": 4.5,
        })
        assert pen == 0.0

        # 5.0pips → ペナルティあり（4.5閾値超過）
        pen, reason = sg.check_spread({
            "spread_pips": 5.0,
            "sg_spread_threshold_pips": 4.5,
        })
        assert pen > 0

    def test_default_threshold_without_context(self) -> None:
        """コンテキストに閾値なし → グローバルデフォルト使用"""
        sg = SoftGuard(SoftGuardConfig(spread_threshold_pips=2.0))
        # sg_spread_threshold_pips なし → 2.0を使用
        pen, reason = sg.check_spread({"spread_pips": 2.5})
        assert pen > 0

    def test_gbpjpy_no_false_penalty(self) -> None:
        """GBPJPY: 通常スプレッド3.0pipsでペナルティなし（ペア別閾値4.5）"""
        sg = SoftGuard(SoftGuardConfig(spread_threshold_pips=2.0))
        # 旧: 3.0 > 2.0 → ペナルティ（不当）
        # 新: 3.0 < 4.5 → ペナルティなし（正しい）
        pen, reason = sg.check_spread({
            "spread_pips": 3.0,
            "sg_spread_threshold_pips": 4.5,
        })
        assert pen == 0.0

    def test_cadjpy_no_false_penalty(self) -> None:
        """CADJPY: 通常スプレッド2.5pipsでペナルティなし（ペア別閾値3.8）"""
        sg = SoftGuard(SoftGuardConfig(spread_threshold_pips=2.0))
        pen, reason = sg.check_spread({
            "spread_pips": 2.5,
            "sg_spread_threshold_pips": 3.8,
        })
        assert pen == 0.0

    def test_eurusd_tight_threshold(self) -> None:
        """EURUSD: 閾値1.5pips（スプレッド1.0pipsの1.5倍）"""
        sg = SoftGuard(SoftGuardConfig(spread_threshold_pips=2.0))
        # 1.0pips → ペナルティなし
        pen, _ = sg.check_spread({
            "spread_pips": 1.0,
            "sg_spread_threshold_pips": 1.5,
        })
        assert pen == 0.0

        # 2.0pips → ペナルティあり
        pen, _ = sg.check_spread({
            "spread_pips": 2.0,
            "sg_spread_threshold_pips": 1.5,
        })
        assert pen > 0
