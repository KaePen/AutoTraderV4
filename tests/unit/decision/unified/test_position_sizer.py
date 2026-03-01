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
        """スリッページバッファの影響が比例的（2025年版）"""
        config = PositionSizerConfig(slippage_buffer_pips=2.0)
        sizer = PositionSizer(config)

        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.7,  # 0.3 + 0.7*0.7 = 0.79x (区分線形)
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
        )

        result = sizer.calculate(context)

        # 手計算: conf_adjust = 0.3 + 0.7*0.7 = 0.79
        # 1M * 0.025 * 0.79 / ((20+2) * 1000) ≈ 0.897 → 0.90
        conf_adjust = 0.3 + 0.7 * 0.7
        expected_lot = (
            1_000_000 * 0.025 * conf_adjust
        ) / (22.0 * 1000.0)
        # max_risk_pct_absoluteによる上限
        max_lot_risk = (
            1_000_000 * 0.07
        ) / (22.0 * 1000.0)
        # max_lot_per_trade=2.5
        expected = min(expected_lot, 2.5, max_lot_risk)
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
        """start未満は減額なし（2025年版: start=2）"""
        sizer = PositionSizer()
        # 1連敗（start=2未満）
        adjust = sizer._calculate_consecutive_loss_adjust(1)
        assert adjust == 1.0

    def test_gradual_reduction(self) -> None:
        """start〜maxで段階的減額（2025年版: 2-5）"""
        sizer = PositionSizer()

        adj_2 = sizer._calculate_consecutive_loss_adjust(2)
        adj_3 = sizer._calculate_consecutive_loss_adjust(3)
        adj_4 = sizer._calculate_consecutive_loss_adjust(4)

        # 段階的に減少
        assert adj_2 == 1.0  # ちょうどstartは減額開始直前
        assert adj_3 < 1.0
        assert adj_4 < adj_3

    def test_max_reduction_at_max_losses(self) -> None:
        """max以上で最大減額（2025年版: max=5, 0.2x）"""
        sizer = PositionSizer()

        adj_5 = sizer._calculate_consecutive_loss_adjust(5)
        adj_6 = sizer._calculate_consecutive_loss_adjust(6)

        assert adj_5 == pytest.approx(0.2, abs=0.01)
        assert adj_6 == pytest.approx(0.2, abs=0.01)

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
        """同方向エクスポージャー上限で取引停止（2025年版: 4.0*0.6=2.4）"""
        sizer = PositionSizer()

        # max_total=4.0 * ratio=0.6 = 2.4上限
        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
            open_same_direction_lot=2.4,  # 上限到達
        )

        result = sizer.calculate(context)
        assert result.blocked is True
        assert "同方向" in result.reasoning

    def test_same_direction_limits_lot(self) -> None:
        """同方向エクスポージャーがロットを制限（2025年版）"""
        sizer = PositionSizer()

        # 同方向2.0lot → 残り0.4lot
        context = SizingContext(
            equity=1_000_000,
            sl_pips=20.0,
            confidence=0.6,
            regime=MarketRegime.TREND,
            consecutive_losses=0,
            current_dd_pct=0.0,
            open_same_direction_lot=2.0,
        )

        result = sizer.calculate(context)
        # max_same_dir = 4.0 * 0.6 = 2.4、残り0.4lot
        assert result.lot <= 0.4
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
        """早期DD域（2025年版: 1%→2%で1.0→0.7）"""
        sizer = PositionSizer()

        adj_1_5pct = sizer._calculate_dd_adjust(0.015)
        adj_1_8pct = sizer._calculate_dd_adjust(0.018)

        # 1%〜2%間で1.0→0.7の減額
        assert 0.7 < adj_1_5pct < 1.0
        assert adj_1_8pct < adj_1_5pct

    def test_main_dd_stronger_reduction(self) -> None:
        """本格DD域（2025年版: 2%超で0.7→0.5）"""
        sizer = PositionSizer()

        adj_2pct = sizer._calculate_dd_adjust(0.02)
        adj_5pct = sizer._calculate_dd_adjust(0.05)
        adj_10pct = sizer._calculate_dd_adjust(0.10)

        # 2%でちょうど0.7
        assert adj_2pct == pytest.approx(0.7, abs=0.01)
        # 5%で更に減額
        assert adj_5pct < adj_2pct
        # 10%で更に強い減額
        assert adj_10pct < adj_5pct

    def test_dd_monotonic_decrease(self) -> None:
        """DD増加で単調減少"""
        sizer = PositionSizer()

        dd_values = [0.0, 0.01, 0.02, 0.03, 0.05, 0.10, 0.15, 0.20]
        adjustments = [sizer._calculate_dd_adjust(d) for d in dd_values]

        for i in range(1, len(adjustments)):
            assert adjustments[i] <= adjustments[i - 1]


class TestDd2025Reduction:
    """2025年DD対策のテスト"""

    def test_early_dd_threshold_1pct(self) -> None:
        """DD早期減額が1%から開始"""
        sizer = PositionSizer()

        adj_0pct = sizer._calculate_dd_adjust(0.0)
        adj_1pct = sizer._calculate_dd_adjust(0.01)
        adj_1_5pct = sizer._calculate_dd_adjust(0.015)

        # 0%: 減額なし
        assert adj_0pct == 1.0
        # 1%: 減額開始（ちょうど閾値なので1.0）
        assert adj_1pct == 1.0
        # 1.5%: 早期減額域
        assert adj_1_5pct < 1.0

    def test_main_dd_threshold_2pct(self) -> None:
        """DD本格減額が2%から開始"""
        sizer = PositionSizer()

        adj_1_9pct = sizer._calculate_dd_adjust(0.019)
        adj_2pct = sizer._calculate_dd_adjust(0.02)
        adj_2_5pct = sizer._calculate_dd_adjust(0.025)

        # 2%未満: 早期減額域
        assert adj_1_9pct > 0.7
        # 2%: 本格減額開始（0.7）
        assert adj_2pct == pytest.approx(0.7, abs=0.01)
        # 2.5%: 本格減額域
        assert adj_2_5pct < 0.7

    def test_consecutive_loss_2start(self) -> None:
        """連敗減額が2連敗から開始"""
        sizer = PositionSizer()

        adj_1 = sizer._calculate_consecutive_loss_adjust(1)
        adj_2 = sizer._calculate_consecutive_loss_adjust(2)
        adj_3 = sizer._calculate_consecutive_loss_adjust(3)

        # 1連敗: 減額なし
        assert adj_1 == 1.0
        # 2連敗: 減額開始（ちょうど閾値なので1.0）
        assert adj_2 == 1.0
        # 3連敗: 減額域
        assert adj_3 < 1.0

    def test_consecutive_loss_5max(self) -> None:
        """連敗が5で最大減額（0.2x）"""
        sizer = PositionSizer()

        adj_5 = sizer._calculate_consecutive_loss_adjust(5)
        adj_6 = sizer._calculate_consecutive_loss_adjust(6)

        # 5連敗: 最大減額
        assert adj_5 == pytest.approx(0.2, abs=0.01)
        # 6連敗: 最大減額維持
        assert adj_6 == pytest.approx(0.2, abs=0.01)

    def test_base_risk_2_5pct(self) -> None:
        """基本リスクが2.5%"""
        sizer = PositionSizer()
        assert sizer.config.base_risk_pct == 0.025

    def test_max_lot_per_trade_2_5(self) -> None:
        """最大ロットが2.5"""
        sizer = PositionSizer()
        assert sizer.config.max_lot_per_trade == 2.5

    def test_max_total_exposure_4(self) -> None:
        """合計ロット上限が4.0"""
        sizer = PositionSizer()
        assert sizer.config.max_total_exposure_lot == 4.0

    def test_dd_max_reduction_50pct(self) -> None:
        """DD最大減額が50%"""
        sizer = PositionSizer()
        assert sizer.config.dd_max_reduction == 0.5


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
