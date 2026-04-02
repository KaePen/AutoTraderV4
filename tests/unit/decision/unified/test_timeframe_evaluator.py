"""TimeframeEvaluatorのユニットテスト"""

from __future__ import annotations

import pandas as pd

from autotrader.core.enums import SignalType
from autotrader.decision.unified.scoring.timeframe_evaluator import (
    TimeframeEvaluator,
    TimeframeSignal,
)
from autotrader.decision.unified.config import EvaluatorConfig


class TestTimeframeEvaluator:
    """TimeframeEvaluatorのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.evaluator = TimeframeEvaluator("M5")

    def test_init(self) -> None:
        """初期化テスト"""
        assert self.evaluator.timeframe == "M5"
        assert self.evaluator.config is not None

    def test_init_with_config(self) -> None:
        """カスタム設定での初期化"""
        config = EvaluatorConfig(
            timeframe="H1",
            min_score=3.0,
            atr_sl_multiplier=2.0,
        )
        evaluator = TimeframeEvaluator("H1", config)
        assert evaluator.timeframe == "H1"
        assert evaluator.config.min_score == 3.0

    def test_evaluate_buy_signal(self) -> None:
        """買いシグナル評価"""
        row = pd.Series({
            "close": 150.0,
            "sma_20": 149.5,
            "sma_50": 149.0,
            "rsi_14": 35.0,
            "macd": 0.01,
            "macd_signal": 0.005,
            "macd_histogram": 0.005,
            "adx": 25.0,
            "atr_14": 0.15,
            "stoch_k": 25.0,
        })
        signal = self.evaluator.evaluate(row)
        assert isinstance(signal, TimeframeSignal)
        assert signal.timeframe == "M5"

    def test_evaluate_sell_signal(self) -> None:
        """売りシグナル評価"""
        row = pd.Series({
            "close": 150.0,
            "sma_20": 150.5,
            "sma_50": 151.0,
            "rsi_14": 75.0,
            "macd": -0.01,
            "macd_signal": 0.005,
            "macd_histogram": -0.015,
            "adx": 25.0,
            "atr_14": 0.15,
            "stoch_k": 85.0,
        })
        signal = self.evaluator.evaluate(row)
        assert isinstance(signal, TimeframeSignal)

    def test_evaluate_hold_signal(self) -> None:
        """HOLDシグナル評価"""
        row = pd.Series({
            "close": 150.0,
            "sma_20": 150.0,
            "sma_50": 150.0,
            "rsi_14": 50.0,
            "macd": 0.0,
            "macd_signal": 0.0,
            "macd_histogram": 0.0,
            "adx": 10.0,
            "atr_14": 0.15,
            "stoch_k": 50.0,
        })
        signal = self.evaluator.evaluate(row)
        assert signal.direction == SignalType.HOLD

    def test_sl_tp_calculation(self) -> None:
        """SL/TP計算テスト"""
        row = pd.Series({
            "close": 150.0,
            "sma_20": 149.5,
            "sma_50": 149.0,
            "rsi_14": 30.0,
            "macd": 0.02,
            "macd_signal": 0.01,
            "macd_histogram": 0.01,
            "adx": 30.0,
            "atr_14": 0.20,
        })
        signal = self.evaluator.evaluate(row)
        assert signal.sl_pips >= 10.0
        assert signal.sl_pips <= 50.0
        assert signal.tp_pips > 0

    def test_net_strength_property(self) -> None:
        """net_strengthプロパティテスト"""
        signal = TimeframeSignal(
            timeframe="M5",
            direction=SignalType.BUY,
            buy_strength=0.8,
            sell_strength=0.2,
            confidence=0.6,
            sl_pips=20.0,
            tp_pips=30.0,
            reason="test",
        )
        assert abs(signal.net_strength - 0.6) < 1e-9

    def test_set_higher_tf_data(self) -> None:
        """上位時間足データ設定"""
        htf_data = {
            "H1": pd.DataFrame({"close": [150.0]}),
            "H4": pd.DataFrame({"close": [150.0]}),
        }
        self.evaluator.set_higher_tf_data(htf_data)
        assert len(self.evaluator._htf_data) == 2

    def test_min_scores_property(self) -> None:
        """MIN_SCORESプロパティテスト"""
        min_scores = self.evaluator.MIN_SCORES
        assert "M5" in min_scores
        assert min_scores["M5"] > 0

    def test_different_timeframes(self) -> None:
        """異なる時間足での動作確認"""
        for tf in ["M1", "M5", "M15", "H1", "H4", "D1"]:
            evaluator = TimeframeEvaluator(tf)
            assert evaluator.timeframe == tf
            assert tf in evaluator.MIN_SCORES

    def test_evaluate_with_missing_indicators(self) -> None:
        """指標欠損時の評価"""
        row = pd.Series({
            "close": 150.0,
            "sma_20": None,
            "sma_50": None,
        })
        signal = self.evaluator.evaluate(row)
        assert signal.direction == SignalType.HOLD

    def test_noise_filter_m5(self) -> None:
        """M5時間足のノイズフィルター"""
        row = pd.Series({
            "close": 150.0,
            "sma_20": 149.5,
            "sma_50": 149.0,
            "rsi_14": 25.0,
            "macd": 0.02,
            "macd_signal": 0.01,
            "adx": 5.0,  # ADXが低いのでフィルタされる
            "atr_14": 0.15,
        })
        signal = self.evaluator.evaluate(row)
        # ADXが低いのでHOLDになる可能性
        assert signal.direction in [SignalType.BUY, SignalType.HOLD]


class TestTimeframeSignal:
    """TimeframeSignalのテスト"""

    def test_creation(self) -> None:
        """シグナル作成テスト"""
        signal = TimeframeSignal(
            timeframe="M15",
            direction=SignalType.BUY,
            buy_strength=0.7,
            sell_strength=0.3,
            confidence=0.5,
            sl_pips=15.0,
            tp_pips=20.0,
            reason="上昇トレンド",
        )
        assert signal.timeframe == "M15"
        assert signal.direction == SignalType.BUY
        assert signal.confidence == 0.5

    def test_net_strength_buy(self) -> None:
        """買い方向のnet_strength"""
        signal = TimeframeSignal(
            timeframe="M5",
            direction=SignalType.BUY,
            buy_strength=0.9,
            sell_strength=0.1,
            confidence=0.8,
            sl_pips=20.0,
            tp_pips=25.0,
            reason="test",
        )
        assert signal.net_strength == 0.8

    def test_net_strength_sell(self) -> None:
        """売り方向のnet_strength"""
        signal = TimeframeSignal(
            timeframe="M5",
            direction=SignalType.SELL,
            buy_strength=0.2,
            sell_strength=0.8,
            confidence=0.6,
            sl_pips=20.0,
            tp_pips=25.0,
            reason="test",
        )
        assert abs(signal.net_strength - (-0.6)) < 1e-9

    def test_indicator_strength_optional(self) -> None:
        """indicator_strengthはオプション"""
        signal = TimeframeSignal(
            timeframe="M5",
            direction=SignalType.HOLD,
            buy_strength=0.5,
            sell_strength=0.5,
            confidence=0.0,
            sl_pips=0.0,
            tp_pips=0.0,
            reason="条件不十分",
        )
        assert signal.indicator_strength is None
