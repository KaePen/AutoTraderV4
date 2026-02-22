"""戦略クラスのユニットテスト"""

from __future__ import annotations


from autotrader.core.enums import MarketRegime
from autotrader.decision.unified.strategies import (
    ScalpStrategy,
    ShortMidStrategy,
    SwingStrategy,
    StrategyId,
    StrategyTimeframes,
    StrategyConfig,
)


class TestStrategyTimeframes:
    """StrategyTimeframesのテスト"""

    def test_all_tfs(self) -> None:
        """all_tfsプロパティテスト"""
        tf = StrategyTimeframes(
            primary_tf="M5",
            entry_tf="M1",
            confirm_tfs=("M15",),
            htf_refs=("H1",),
            htf_weight=0.5,
            tp_sl_ratio_range=(1.0, 1.3),
        )
        all_tfs = tf.all_tfs
        assert "M5" in all_tfs
        assert "M1" in all_tfs
        assert "M15" in all_tfs


class TestScalpStrategy:
    """ScalpStrategyのテスト"""

    def test_init(self) -> None:
        """初期化テスト"""
        strategy = ScalpStrategy()
        assert strategy.strategy_id == StrategyId.SCALP
        assert strategy.timeframes.primary_tf == "M5"

    def test_timeframes(self) -> None:
        """時間足設定テスト"""
        strategy = ScalpStrategy()
        assert strategy.timeframes.entry_tf == "M1"
        assert "M15" in strategy.timeframes.confirm_tfs

    def test_tp_sl_ratio(self) -> None:
        """TP/SL比率テスト"""
        strategy = ScalpStrategy()
        ratio_range = strategy.timeframes.tp_sl_ratio_range
        assert ratio_range[0] >= 0.5
        assert ratio_range[1] <= 2.0

    def test_config(self) -> None:
        """設定テスト"""
        strategy = ScalpStrategy()
        assert strategy.config.min_edge_score > 0
        assert MarketRegime.TREND in strategy.config.regime_weights


class TestShortMidStrategy:
    """ShortMidStrategyのテスト"""

    def test_init(self) -> None:
        """初期化テスト"""
        strategy = ShortMidStrategy()
        assert strategy.strategy_id == StrategyId.SHORT_MID
        assert strategy.timeframes.primary_tf == "M15"

    def test_timeframes(self) -> None:
        """時間足設定テスト"""
        strategy = ShortMidStrategy()
        assert strategy.timeframes.entry_tf == "H1"
        assert "H4" in strategy.timeframes.confirm_tfs

    def test_regime_weights(self) -> None:
        """レジーム重みテスト"""
        strategy = ShortMidStrategy()
        weights = strategy.config.regime_weights
        assert weights[MarketRegime.TREND] > weights[MarketRegime.RANGE]


class TestSwingStrategy:
    """SwingStrategyのテスト"""

    def test_init(self) -> None:
        """初期化テスト"""
        strategy = SwingStrategy()
        assert strategy.strategy_id == StrategyId.SWING
        assert strategy.timeframes.primary_tf == "H1"

    def test_timeframes(self) -> None:
        """時間足設定テスト"""
        strategy = SwingStrategy()
        assert strategy.timeframes.entry_tf == "H4"
        assert "D1" in strategy.timeframes.confirm_tfs

    def test_htf_weight(self) -> None:
        """HTF重みテスト"""
        strategy = SwingStrategy()
        assert strategy.timeframes.htf_weight >= 0.5


class TestStrategyId:
    """StrategyIdのテスト"""

    def test_enum_values(self) -> None:
        """Enum値テスト"""
        assert StrategyId.SCALP.value == "scalp"
        assert StrategyId.SHORT_MID.value == "short_mid"
        assert StrategyId.SWING.value == "swing"


class TestStrategyConfig:
    """StrategyConfigのテスト"""

    def test_default_values(self) -> None:
        """デフォルト値テスト"""
        config = StrategyConfig()
        assert config.min_edge_score > 0
        assert config.max_spread_atr_ratio > 0

    def test_custom_values(self) -> None:
        """カスタム値テスト"""
        config = StrategyConfig(
            min_edge_score=0.2,
            max_spread_atr_ratio=0.5,
            allowed_hours_utc=(8, 16),
        )
        assert config.min_edge_score == 0.2
        assert config.allowed_hours_utc == (8, 16)

    def test_regime_weights(self) -> None:
        """レジーム重みテスト"""
        config = StrategyConfig(
            regime_weights={
                MarketRegime.HIGH_VOL: 1.5,
                MarketRegime.TREND: 1.2,
                MarketRegime.RANGE: 0.8,
                MarketRegime.LOW_VOL: 0.6,
            }
        )
        assert config.regime_weights[MarketRegime.HIGH_VOL] == 1.5
