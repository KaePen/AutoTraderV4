"""MarketRegimeDetectorのユニットテスト"""

from __future__ import annotations

import pandas as pd

from autotrader.core.enums import MarketRegime
from autotrader.calculator.features.regime_detector import (
    MarketRegimeDetector,
    RegimeDetectorConfig,
    RegimeResult,
)


class TestMarketRegimeDetector:
    """MarketRegimeDetectorのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.detector = MarketRegimeDetector()

    def test_high_vol_detection(self) -> None:
        """高ボラティリティ検出"""
        result = self.detector.detect(
            normalized_atr=2.0,  # 高ATR
            adx=15.0,           # 低ADX
            ma_alignment=0.1,
        )

        assert result.regime == MarketRegime.HIGH_VOL
        assert result.volatility_level == 2.0
        assert "高ボラ" in result.reasoning

    def test_trend_detection_strong(self) -> None:
        """強トレンド検出"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=35.0,           # 高ADX
            ma_alignment=0.5,   # MA整列
        )

        assert result.regime == MarketRegime.TREND
        assert "強トレンド" in result.reasoning

    def test_trend_detection_moderate(self) -> None:
        """中程度トレンド検出"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=25.0,           # 中程度ADX
            ma_alignment=0.4,   # MA整列
        )

        assert result.regime == MarketRegime.TREND
        assert "トレンド" in result.reasoning

    def test_low_vol_detection(self) -> None:
        """低ボラティリティ検出"""
        result = self.detector.detect(
            normalized_atr=0.5,  # 低ATR
            adx=18.0,
            ma_alignment=0.1,
        )

        assert result.regime == MarketRegime.LOW_VOL
        assert "低ボラ" in result.reasoning

    def test_range_detection(self) -> None:
        """レンジ検出"""
        result = self.detector.detect(
            normalized_atr=1.0,  # 通常ATR
            adx=18.0,           # 低ADX
            ma_alignment=0.1,   # MA非整列
        )

        assert result.regime == MarketRegime.RANGE
        assert "レンジ" in result.reasoning

    def test_nan_handling(self) -> None:
        """NaN値のハンドリング"""
        result = self.detector.detect(
            normalized_atr=float("nan"),
            adx=20.0,
            ma_alignment=0.5,
        )

        assert result.regime == MarketRegime.RANGE
        assert result.confidence == 0.0
        assert "データ不足" in result.reasoning

    def test_detect_from_row(self) -> None:
        """DataFrameの行からの検出"""
        row = pd.Series({
            "normalized_atr": 1.0,
            "adx": 30.0,
            "ma_alignment": 0.5,
        })

        result = self.detector.detect_from_row(row)

        assert result.regime == MarketRegime.TREND

    def test_detect_from_row_alt_columns(self) -> None:
        """代替カラム名での検出"""
        row = pd.Series({
            "norm_atr": 0.5,     # 代替名
            "ADX": 15.0,        # 代替名
            "trend_alignment": 0.1,  # 代替名
        })

        result = self.detector.detect_from_row(row)

        assert result.regime == MarketRegime.LOW_VOL

    def test_detect_from_row_missing_columns(self) -> None:
        """カラム欠損時のデフォルト値"""
        row = pd.Series({
            "close": 150.0,
        })

        result = self.detector.detect_from_row(row)

        # デフォルト値で判定
        assert result.regime is not None

    def test_trend_strength_calculation(self) -> None:
        """トレンド強度計算"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=40.0,  # 高ADX
            ma_alignment=0.5,
        )

        assert result.trend_strength == 1.0  # max

        result2 = self.detector.detect(
            normalized_atr=1.0,
            adx=20.0,  # 中ADX
            ma_alignment=0.5,
        )

        assert result2.trend_strength == 0.5  # 20/40

    def test_custom_config(self) -> None:
        """カスタム設定"""
        config = RegimeDetectorConfig(
            high_vol_atr_threshold=2.0,
            trend_adx_threshold=25.0,
        )
        detector = MarketRegimeDetector(config)

        # デフォルトでは HIGH_VOL になる値がTRENDに
        result = detector.detect(
            normalized_atr=1.8,  # 新閾値以下
            adx=30.0,
            ma_alignment=0.5,
        )

        assert result.regime == MarketRegime.TREND

    def test_confidence_values(self) -> None:
        """確度値の範囲"""
        for atr in [0.3, 0.8, 1.0, 1.5, 2.0]:
            for adx in [10.0, 20.0, 30.0, 40.0]:
                for ma in [0.0, 0.3, 0.6]:
                    result = self.detector.detect(atr, adx, ma)
                    assert 0.0 <= result.confidence <= 1.0


class TestBreakoutDetection:
    """BREAKOUT検出のテスト"""

    def setup_method(self) -> None:
        """テストセットアップ（BREAKOUT有効）"""
        self.detector = MarketRegimeDetector(
            RegimeDetectorConfig(breakout_enabled=True)
        )

    def test_breakout_up_detected(self) -> None:
        """上方ブレイクアウト検出"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,  # TREND閾値未満
            ma_alignment=0.1,
            breakout_up=True,
            atr_change_rate=0.3,  # ATR拡大中
        )
        assert result.regime == MarketRegime.BREAKOUT
        assert result.is_breakout is True
        assert "上方ブレイクアウト" in result.reasoning

    def test_breakout_down_detected(self) -> None:
        """下方ブレイクアウト検出"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=-0.1,
            breakout_down=True,
            atr_change_rate=0.2,
        )
        assert result.regime == MarketRegime.BREAKOUT
        assert result.is_breakout is True
        assert "下方ブレイクアウト" in result.reasoning

    def test_breakout_not_triggered_high_adx(self) -> None:
        """ADXが高い場合はBREAKOUTではなくTREND"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=25.0,  # TREND閾値以上
            ma_alignment=0.5,
            breakout_up=True,
            atr_change_rate=0.3,
        )
        # ADX >= 20 + MA整列 → TREND
        assert result.regime == MarketRegime.TREND

    def test_breakout_not_triggered_atr_shrinking(
        self,
    ) -> None:
        """ATR縮小中はBREAKOUT不成立"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            breakout_up=True,
            atr_change_rate=-0.1,  # ATR縮小中
        )
        # ATR拡大なし → RANGE
        assert result.regime == MarketRegime.RANGE

    def test_breakout_not_triggered_no_price_break(
        self,
    ) -> None:
        """価格ブレイクなしはBREAKOUT不成立"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            breakout_up=False,
            breakout_down=False,
            atr_change_rate=0.3,
        )
        assert result.regime == MarketRegime.RANGE

    def test_breakout_disabled_by_default(self) -> None:
        """デフォルトではBREAKOUT無効"""
        detector = MarketRegimeDetector()
        result = detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            breakout_up=True,
            atr_change_rate=0.3,
        )
        # 無効時はRANGE
        assert result.regime == MarketRegime.RANGE

    def test_breakout_confidence(self) -> None:
        """BREAKOUT確度計算"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=10.0,  # ADX低め → _adx_conf = 0.5
            ma_alignment=0.1,
            breakout_up=True,
            atr_change_rate=0.5,  # _atr_conf = 1.0
        )
        assert result.regime == MarketRegime.BREAKOUT
        # 確度 = (1.0 + 0.5) / 2 = 0.75
        assert abs(result.confidence - 0.75) < 0.01

    def test_breakout_atr_change_rate_in_result(
        self,
    ) -> None:
        """ATR変化率がRegimeResultに保存される"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            breakout_up=True,
            atr_change_rate=0.25,
        )
        assert result.atr_change_rate == 0.25

    def test_breakout_from_row(self) -> None:
        """detect_from_rowでBREAKOUT検出"""
        row = pd.Series({
            "normalized_atr": 1.0,
            "adx": 15.0,
            "ma_alignment": 0.1,
            "breakout_up": 1.0,
            "breakout_down": 0.0,
            "atr_change_rate": 0.3,
        })
        result = self.detector.detect_from_row(row)
        assert result.regime == MarketRegime.BREAKOUT

    def test_breakout_from_row_no_columns(self) -> None:
        """ブレイクアウトカラムなしでも動作"""
        row = pd.Series({
            "normalized_atr": 1.0,
            "adx": 15.0,
            "ma_alignment": 0.1,
        })
        result = self.detector.detect_from_row(row)
        # ブレイクアウトカラムなし → RANGE
        assert result.regime == MarketRegime.RANGE


class TestVolatilityDirection:
    """ボラティリティ方向検出のテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.detector = MarketRegimeDetector()

    def test_expanding_detected(self) -> None:
        """ATR拡大中をexpandingと判定"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            atr_change_rate=0.4,  # > 0.3 threshold
        )
        assert result.volatility_direction == "expanding"

    def test_compressing_detected(self) -> None:
        """ATR縮小中をcompressingと判定"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            atr_change_rate=-0.3,  # < -0.2 threshold
        )
        assert result.volatility_direction == "compressing"

    def test_neutral_detected(self) -> None:
        """中間域をneutralと判定"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            atr_change_rate=0.1,  # between thresholds
        )
        assert result.volatility_direction == "neutral"

    def test_neutral_default_no_atr_change(self) -> None:
        """ATR変化率0はneutral"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
        )
        assert result.volatility_direction == "neutral"

    def test_custom_thresholds(self) -> None:
        """カスタム閾値でのボラ方向判定"""
        config = RegimeDetectorConfig(
            vol_expanding_threshold=0.1,
            vol_compressing_threshold=-0.1,
        )
        detector = MarketRegimeDetector(config)
        result = detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            atr_change_rate=0.15,
        )
        assert result.volatility_direction == "expanding"

    def test_vol_direction_with_breakout(self) -> None:
        """BREAKOUTレジームでもボラ方向は設定される"""
        config = RegimeDetectorConfig(
            breakout_enabled=True,
        )
        detector = MarketRegimeDetector(config)
        result = detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            breakout_up=True,
            atr_change_rate=0.5,
        )
        assert result.regime == MarketRegime.BREAKOUT
        assert result.volatility_direction == "expanding"

    def test_vol_direction_boundary(self) -> None:
        """閾値境界でneutral"""
        result = self.detector.detect(
            normalized_atr=1.0,
            adx=15.0,
            ma_alignment=0.1,
            atr_change_rate=0.3,  # == threshold → not >
        )
        assert result.volatility_direction == "neutral"
