"""PositionSizerのユニットテスト"""

from __future__ import annotations

import pytest

from autotrader.core.enums import MarketRegime
from autotrader.core.interfaces.position_sizing import SizingContext
from autotrader.decision.unified.position_sizer import (
    PositionSizer,
    PositionSizerConfig,
)


class TestPositionSizer:
    """PositionSizerのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.sizer = PositionSizer()

    def test_basic_calculation(self) -> None:
        """基本的なロット計算"""
        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        result = self.sizer.calculate(context)

        # equity * 0.02 * adjust / ((sl_pips + 2.0) * pip_value)
        assert result.lot > 0
        assert result.lot <= 10.0
        assert result.risk_budget > 0
        assert result.reasoning

    def test_high_confidence_increases_lot(self) -> None:
        """高確度でロット増加"""
        base_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.5,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        high_conf_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.8,  # 高確度
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        base_result = self.sizer.calculate(base_context)
        high_result = self.sizer.calculate(high_conf_context)

        assert high_result.lot > base_result.lot

    def test_low_confidence_decreases_lot(self) -> None:
        """低確度でロット減少"""
        base_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        low_conf_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.3,  # 低確度
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        base_result = self.sizer.calculate(base_context)
        low_result = self.sizer.calculate(low_conf_context)

        assert low_result.lot < base_result.lot

    def test_regime_adjustment(self) -> None:
        """レジーム別調整"""
        base_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        high_vol_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.HIGH_VOL,  # 高ボラ
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        trend_result = self.sizer.calculate(base_context)
        high_vol_result = self.sizer.calculate(high_vol_context)

        # HIGH_VOLはTRENDより小さいロット
        assert high_vol_result.lot < trend_result.lot

    def test_drawdown_reduction(self) -> None:
        """ドローダウン時の減額"""
        base_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        dd_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.15,  # 15%DD
        )

        base_result = self.sizer.calculate(base_context)
        dd_result = self.sizer.calculate(dd_context)

        assert dd_result.lot < base_result.lot

    def test_consecutive_losses_reduction(self) -> None:
        """連敗時の減額（3連敗から段階的）"""
        base_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        # 4連敗（段階的減額域の途中）
        loss_context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=4,
            current_dd_pct=0.0,
        )

        base_result = self.sizer.calculate(base_context)
        loss_result = self.sizer.calculate(loss_context)

        assert loss_result.lot < base_result.lot

    def test_minimum_lot(self) -> None:
        """最小ロット制限"""
        context = SizingContext(
            equity=10_000,  # 小額
            sl_pips=50.0,   # 大きなSL
            confidence=0.3,
            regime=MarketRegime.HIGH_VOL,
            consecutive_losses=10,
            current_dd_pct=0.2,
            initial_equity=10_000,  # フロア制約回避
        )

        result = self.sizer.calculate(context)

        assert result.lot >= self.sizer.config.min_lot

    def test_maximum_lot(self) -> None:
        """最大ロット制限"""
        context = SizingContext(
            equity=100_000_000,  # 大額
            sl_pips=5.0,         # 小さなSL
            confidence=0.9,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        result = self.sizer.calculate(context)

        assert result.lot <= self.sizer.config.max_lot

    def test_zero_sl_handling(self) -> None:
        """SLが0の場合のハンドリング"""
        context = SizingContext(
            equity=1_000_000,
            sl_pips=0.0,  # SLなし
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        result = self.sizer.calculate(context)

        assert result.lot == self.sizer.config.min_lot
        assert "SL距離が0" in result.reasoning

    def test_custom_config(self) -> None:
        """カスタム設定"""
        config = PositionSizerConfig(
            base_risk_pct=0.01,  # 1%リスク
            min_lot=0.1,
            max_lot=5.0,
        )
        sizer = PositionSizer(config)

        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        result = sizer.calculate(context)

        assert result.lot >= 0.1
        assert result.lot <= 5.0


class TestSlippageBuffer:
    """SLスリッページバッファのテスト"""

    def test_slippage_buffer_reduces_lot(self) -> None:
        """スリッページバッファありでロット減少"""
        # バッファなし
        config_no_buf = PositionSizerConfig(slippage_buffer_pips=0.0)
        sizer_no_buf = PositionSizer(config_no_buf)

        # バッファあり（デフォルト2.0pips）
        config_buf = PositionSizerConfig(slippage_buffer_pips=2.0)
        sizer_buf = PositionSizer(config_buf)

        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        result_no_buf = sizer_no_buf.calculate(context)
        result_buf = sizer_buf.calculate(context)

        # SL20 vs SL22: バッファありの方がロット小さい
        assert result_buf.lot < result_no_buf.lot

    def test_slippage_buffer_proportional(self) -> None:
        """スリッページバッファの影響が比例的"""
        config = PositionSizerConfig(slippage_buffer_pips=2.0)
        sizer = PositionSizer(config)

        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.7,  # ちょうど1.2x
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        result = sizer.calculate(context)

        # 手計算: 1M * 0.02 * 1.2 / ((20+2) * 1000) = 1.09
        # ただしmax_lot_per_trade=2.0で制限される場合あり
        # risk_budget上限チェックも考慮
        expected_lot = (
            1_000_000 * 0.02 * 1.2
        ) / (22.0 * 1000.0)
        # max_risk_pct_absoluteによる上限
        max_lot_risk = (
            1_000_000 * 0.03
        ) / (22.0 * 1000.0)
        expected = min(expected_lot, 2.0, max_lot_risk)
        assert abs(result.lot - round(expected, 2)) < 0.02


class TestGradualCautionZone:
    """注意域の段階的減衰テスト"""

    def test_above_caution_no_reduction(self) -> None:
        """注意域上限を超えている場合は減額なし"""
        sizer = PositionSizer()

        # 資金比率60%（caution_pct=50%を超過）
        context = SizingContext(
            equity=600_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
            initial_equity=1_000_000,
        )

        result = sizer.calculate(context)
        # 注意域外なので"注意域"が理由に含まれない
        assert "注意域" not in result.reasoning

    def test_gradual_reduction_in_caution_zone(self) -> None:
        """注意域内で段階的に減額"""
        sizer = PositionSizer()

        # 資金比率45%（caution域の上の方）
        ctx_45 = SizingContext(
            equity=450_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
            initial_equity=1_000_000,
        )

        # 資金比率35%（caution域の下の方）
        ctx_35 = SizingContext(
            equity=350_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
            initial_equity=1_000_000,
        )

        result_45 = sizer.calculate(ctx_45)
        result_35 = sizer.calculate(ctx_35)

        # 45%の方が35%よりロットが大きい（段階的）
        assert result_45.lot > result_35.lot
        # 両方とも注意域内
        assert "注意域" in result_45.reasoning
        assert "注意域" in result_35.reasoning

    def test_floor_blocks_trading(self) -> None:
        """フロア以下で取引停止"""
        sizer = PositionSizer()

        context = SizingContext(
            equity=250_000,  # 25% < floor 30%
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
            initial_equity=1_000_000,
        )

        result = sizer.calculate(context)
        assert result.blocked is True


class TestGradualConsecutiveLoss:
    """連敗の段階的減額テスト"""

    def test_no_reduction_below_start(self) -> None:
        """start未満は減額なし"""
        sizer = PositionSizer()
        # 2連敗（start=3未満）
        adjust = sizer._calculate_consecutive_loss_adjust(2)
        assert adjust == 1.0

    def test_gradual_reduction(self) -> None:
        """start〜maxで段階的減額"""
        sizer = PositionSizer()

        adj_3 = sizer._calculate_consecutive_loss_adjust(3)
        adj_5 = sizer._calculate_consecutive_loss_adjust(5)
        adj_7 = sizer._calculate_consecutive_loss_adjust(7)

        # 段階的に減少
        assert adj_3 == 1.0  # ちょうどstartは減額開始直前
        assert adj_5 < 1.0
        assert adj_7 < adj_5

    def test_max_reduction_at_max_losses(self) -> None:
        """max以上で最大減額"""
        sizer = PositionSizer()

        adj_8 = sizer._calculate_consecutive_loss_adjust(8)
        adj_10 = sizer._calculate_consecutive_loss_adjust(10)

        assert adj_8 == pytest.approx(0.3, abs=0.01)
        assert adj_10 == pytest.approx(0.3, abs=0.01)

    def test_monotonic_decrease(self) -> None:
        """連敗数増加で単調減少"""
        sizer = PositionSizer()

        adjustments = [
            sizer._calculate_consecutive_loss_adjust(i)
            for i in range(0, 12)
        ]

        for i in range(1, len(adjustments)):
            assert adjustments[i] <= adjustments[i - 1]


class TestSameDirectionExposure:
    """同方向エクスポージャー制限テスト"""

    def test_same_direction_blocks_at_limit(self) -> None:
        """同方向エクスポージャー上限で取引停止"""
        sizer = PositionSizer()

        # max_total=5.0 * ratio=0.6 = 3.0上限
        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
            open_same_direction_lot=3.0,  # 上限到達
        )

        result = sizer.calculate(context)
        assert result.blocked is True
        assert "同方向" in result.reasoning

    def test_same_direction_limits_lot(self) -> None:
        """同方向エクスポージャーがロットを制限"""
        sizer = PositionSizer()

        # 同方向2.5lot → 残り0.5lot
        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
            open_same_direction_lot=2.5,
        )

        result = sizer.calculate(context)
        # max_same_dir = 5.0 * 0.6 = 3.0、残り0.5lot
        assert result.lot <= 0.5
        assert not result.blocked

    def test_no_same_direction_no_limit(self) -> None:
        """同方向ロット0なら制限なし"""
        sizer = PositionSizer()

        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
            open_same_direction_lot=0.0,
        )

        result = sizer.calculate(context)
        assert not result.blocked
        assert result.lot > 0


class TestSmoothDdAdjustment:
    """DD調整の平滑化テスト"""

    def test_no_dd_no_reduction(self) -> None:
        """DD=0で減額なし"""
        sizer = PositionSizer()
        assert sizer._calculate_dd_adjust(0.0) == 1.0

    def test_early_dd_slight_reduction(self) -> None:
        """早期DD域（2%→5%）で軽い減額"""
        sizer = PositionSizer()

        adj_3pct = sizer._calculate_dd_adjust(0.03)
        adj_4pct = sizer._calculate_dd_adjust(0.04)

        # 2%〜5%間で1.0→0.9の軽い減額
        assert 0.9 < adj_3pct < 1.0
        assert adj_4pct < adj_3pct

    def test_main_dd_stronger_reduction(self) -> None:
        """本格DD域（5%超）でより強い減額"""
        sizer = PositionSizer()

        adj_5pct = sizer._calculate_dd_adjust(0.05)
        adj_10pct = sizer._calculate_dd_adjust(0.10)
        adj_15pct = sizer._calculate_dd_adjust(0.15)

        # 5%でちょうど0.9
        assert adj_5pct == pytest.approx(0.9, abs=0.01)
        # 10%で更に減額
        assert adj_10pct < adj_5pct
        # 15%で更に強い減額
        assert adj_15pct < adj_10pct

    def test_dd_monotonic_decrease(self) -> None:
        """DD増加で単調減少"""
        sizer = PositionSizer()

        dd_values = [0.0, 0.01, 0.02, 0.03, 0.05, 0.10, 0.15, 0.20]
        adjustments = [sizer._calculate_dd_adjust(d) for d in dd_values]

        for i in range(1, len(adjustments)):
            assert adjustments[i] <= adjustments[i - 1]


class TestDynamicMaxLotFromRisk:
    """リスクベース動的ロット上限テスト"""

    def test_risk_based_limit_applied(self) -> None:
        """リスクベース上限が適用される"""
        # 小さなSLで大きなロットが計算されるケース
        config = PositionSizerConfig(
            max_lot_per_trade=10.0,  # 静的上限を大きく
            max_risk_pct_absolute=0.03,
            slippage_buffer_pips=2.0,
        )
        sizer = PositionSizer(config)

        context = SizingContext(
            equity=1_000_000,
            sl_pips=5.0,  # 小さなSL
            confidence=0.9,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        result = sizer.calculate(context)

        # リスク上限: 1M * 0.03 / ((5+2) * 1000) = 4.28lot
        max_from_risk = (
            1_000_000 * 0.03 / (7.0 * 1000.0)
        )
        assert result.lot <= round(max_from_risk, 2) + 0.01

    def test_static_limit_wins_when_smaller(self) -> None:
        """静的上限がリスク上限より小さい場合は静的上限"""
        config = PositionSizerConfig(
            max_lot_per_trade=1.0,  # 静的上限を小さく
            max_risk_pct_absolute=0.03,
            slippage_buffer_pips=2.0,
        )
        sizer = PositionSizer(config)

        context = SizingContext(
            equity=10_000_000,
            sl_pips=5.0,
            confidence=0.9,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        result = sizer.calculate(context)
        assert result.lot <= 1.0
