"""流動性ゾーン連動TP計算のテスト"""

from __future__ import annotations

import pytest

from autotrader.decision.unified.position_sizer import (
    calculate_tp_with_liquidity,
    LiquidityTPResult,
)


class TestCalculateTPWithLiquidity:
    """calculate_tp_with_liquidity関数のテスト"""

    def test_buy_with_liquidity_zone(self) -> None:
        """買いの場合、上の流動性ゾーンをターゲット"""
        entry_price = 150.000
        sl_price = 149.500
        atr = 0.50
        # SL距離 = 0.5、基本TP = 150.75
        # liquidity_tp = buy_side * 0.99 が entry_price より大きく、
        # base_tp * 1.5 = 226.125 より小さい必要あり
        # つまり buy_side * 0.99 > 150.0 → buy_side > 151.515
        buy_side_liquidity = 151.600  # 妥当な範囲内
        sell_side_liquidity = None

        tp = calculate_tp_with_liquidity(
            direction=1,
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            buy_side_liquidity=buy_side_liquidity,
            sell_side_liquidity=sell_side_liquidity,
            default_rr=1.5,
        )

        # 流動性ゾーン手前（99%）をTP
        expected_tp = buy_side_liquidity * 0.99
        assert tp == pytest.approx(expected_tp, abs=0.001)

    def test_buy_fallback_to_base_tp(self) -> None:
        """買いで流動性ゾーンがない場合、基本TPを使用"""
        entry_price = 150.000
        sl_price = 149.500
        atr = 0.50
        buy_side_liquidity = None
        sell_side_liquidity = None

        tp = calculate_tp_with_liquidity(
            direction=1,
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            buy_side_liquidity=buy_side_liquidity,
            sell_side_liquidity=sell_side_liquidity,
            default_rr=1.5,
        )

        # 基本TP = entry + sl_distance * default_rr
        sl_distance = entry_price - sl_price  # 0.5
        expected_tp = entry_price + sl_distance * 1.5  # 150.75
        assert tp == pytest.approx(expected_tp, abs=0.001)

    def test_buy_liquidity_too_far(self) -> None:
        """買いで流動性ゾーンが遠すぎる場合、基本TPを使用"""
        entry_price = 150.000
        sl_price = 149.500
        atr = 0.50
        # SL距離 = 0.5、基本TP = 150.75
        # base_tp * 1.5 = 226.125
        # liquidity_tp = buy_side * 0.99 > 226.125 なら遠すぎる
        # → buy_side > 228.41
        buy_side_liquidity = 230.000  # 遠すぎる
        sell_side_liquidity = None

        tp = calculate_tp_with_liquidity(
            direction=1,
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            buy_side_liquidity=buy_side_liquidity,
            sell_side_liquidity=sell_side_liquidity,
            default_rr=1.5,
        )

        # 基本TPを使用
        sl_distance = entry_price - sl_price
        expected_tp = entry_price + sl_distance * 1.5
        assert tp == pytest.approx(expected_tp, abs=0.001)

    def test_sell_with_liquidity_zone(self) -> None:
        """売りの場合、下の流動性ゾーンをターゲット"""
        entry_price = 150.000
        sl_price = 150.500
        atr = 0.50
        # SL距離 = 0.5、基本TP = 149.25
        # liquidity_tp = sell_side * 1.01 が entry_price より小さく、
        # base_tp * 0.67 = 99.9975 より大きい必要あり
        buy_side_liquidity = None
        sell_side_liquidity = 148.500  # 妥当な範囲内

        tp = calculate_tp_with_liquidity(
            direction=-1,
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            buy_side_liquidity=buy_side_liquidity,
            sell_side_liquidity=sell_side_liquidity,
            default_rr=1.5,
        )

        # 流動性ゾーン手前（101%）をTP
        expected_tp = sell_side_liquidity * 1.01
        assert tp == pytest.approx(expected_tp, abs=0.001)

    def test_sell_fallback_to_base_tp(self) -> None:
        """売りで流動性ゾーンがない場合、基本TPを使用"""
        entry_price = 150.000
        sl_price = 150.500
        atr = 0.50
        buy_side_liquidity = None
        sell_side_liquidity = None

        tp = calculate_tp_with_liquidity(
            direction=-1,
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            buy_side_liquidity=buy_side_liquidity,
            sell_side_liquidity=sell_side_liquidity,
            default_rr=1.5,
        )

        # 基本TP = entry - sl_distance * default_rr
        sl_distance = sl_price - entry_price  # 0.5
        expected_tp = entry_price - sl_distance * 1.5  # 149.25
        assert tp == pytest.approx(expected_tp, abs=0.001)

    def test_sell_liquidity_too_close(self) -> None:
        """売りで流動性ゾーンが近すぎる場合、基本TPを使用"""
        entry_price = 150.000
        sl_price = 150.500
        atr = 0.50
        buy_side_liquidity = None
        sell_side_liquidity = 149.900  # 近すぎる

        tp = calculate_tp_with_liquidity(
            direction=-1,
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            buy_side_liquidity=buy_side_liquidity,
            sell_side_liquidity=sell_side_liquidity,
            default_rr=1.5,
        )

        # 基本TPを使用（流動性TPは基本TPの0.67倍未満で却下）
        sl_distance = sl_price - entry_price
        expected_tp = entry_price - sl_distance * 1.5
        assert tp == pytest.approx(expected_tp, abs=0.001)

    def test_custom_rr_ratio(self) -> None:
        """カスタムRR比率"""
        entry_price = 150.000
        sl_price = 149.500
        atr = 0.50

        tp = calculate_tp_with_liquidity(
            direction=1,
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            buy_side_liquidity=None,
            sell_side_liquidity=None,
            default_rr=2.0,  # カスタムRR
        )

        sl_distance = entry_price - sl_price
        expected_tp = entry_price + sl_distance * 2.0
        assert tp == pytest.approx(expected_tp, abs=0.001)

    def test_custom_margin_pct(self) -> None:
        """カスタムマージン率"""
        entry_price = 150.000
        sl_price = 149.500
        atr = 0.50
        # liquidity_tp = buy_side * 0.98 > entry_price → buy_side > 153.06
        buy_side_liquidity = 154.000

        tp = calculate_tp_with_liquidity(
            direction=1,
            entry_price=entry_price,
            sl_price=sl_price,
            atr=atr,
            buy_side_liquidity=buy_side_liquidity,
            sell_side_liquidity=None,
            default_rr=1.5,
            liquidity_margin_pct=0.02,  # 2%マージン
        )

        expected_tp = buy_side_liquidity * 0.98
        assert tp == pytest.approx(expected_tp, abs=0.001)


class TestLiquidityTPResult:
    """LiquidityTPResultのテスト"""

    def test_default_values(self) -> None:
        """デフォルト値のテスト"""
        result = LiquidityTPResult(tp_price=150.5)
        assert result.tp_price == 150.5
        assert result.used_liquidity is False
        assert result.liquidity_zone is None
        assert result.base_tp == 0.0

    def test_with_liquidity(self) -> None:
        """流動性ゾーン使用時"""
        result = LiquidityTPResult(
            tp_price=150.6,
            used_liquidity=True,
            liquidity_zone=150.7,
            base_tp=150.75,
        )
        assert result.tp_price == 150.6
        assert result.used_liquidity is True
        assert result.liquidity_zone == 150.7
        assert result.base_tp == 150.75
