"""並列マルチタイムフレームバックテストのテスト"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import pytest

if TYPE_CHECKING:
    pass


class TestCandleEvent:
    """CandleEventのテスト"""

    def test_candle_event_creation(self) -> None:
        """CandleEventが正しく作成される"""
        from autotrader.backtest.events import CandleEvent

        event = CandleEvent(
            timestamp=datetime(2024, 1, 1, 10, 0),
            timeframe="H1",
            candle_data={
                "open": 150.0,
                "high": 150.5,
                "low": 149.5,
                "close": 150.2,
                "volume": 1000.0,
            },
            row_data={"rsi_14": 55.0, "macd": 0.1},
        )

        assert event.timestamp == datetime(2024, 1, 1, 10, 0)
        assert event.timeframe == "H1"
        assert event.candle_data["close"] == 150.2
        assert event.row_data["rsi_14"] == 55.0
        assert event.timeframe_minutes == 60

    def test_candle_event_comparison_same_timestamp(self) -> None:
        """同時刻イベントは長期足が優先される"""
        from autotrader.backtest.events import CandleEvent

        m15_event = CandleEvent(
            timestamp=datetime(2024, 1, 1, 10, 0),
            timeframe="M15",
            candle_data={"open": 150.0, "high": 150.0, "low": 150.0,
                         "close": 150.0, "volume": 0},
            row_data={},
        )

        h1_event = CandleEvent(
            timestamp=datetime(2024, 1, 1, 10, 0),
            timeframe="H1",
            candle_data={"open": 150.0, "high": 150.0, "low": 150.0,
                         "close": 150.0, "volume": 0},
            row_data={},
        )

        # H1 (60分) は M15 (15分) より優先されるべき
        assert h1_event < m15_event

    def test_candle_event_comparison_different_timestamp(self) -> None:
        """異なる時刻は時刻順"""
        from autotrader.backtest.events import CandleEvent

        earlier = CandleEvent(
            timestamp=datetime(2024, 1, 1, 9, 0),
            timeframe="M15",
            candle_data={"open": 150.0, "high": 150.0, "low": 150.0,
                         "close": 150.0, "volume": 0},
            row_data={},
        )

        later = CandleEvent(
            timestamp=datetime(2024, 1, 1, 10, 0),
            timeframe="H1",
            candle_data={"open": 150.0, "high": 150.0, "low": 150.0,
                         "close": 150.0, "volume": 0},
            row_data={},
        )

        assert earlier < later


class TestTimelineEventQueue:
    """TimelineEventQueueのテスト"""

    def test_timeline_event_queue_sorting(self) -> None:
        """イベントが時系列順にソートされる"""
        from autotrader.backtest.events import TimelineEventQueue

        # テスト用データ作成
        m15_data = pd.DataFrame({
            "time": [
                datetime(2024, 1, 1, 10, 0),
                datetime(2024, 1, 1, 10, 15),
                datetime(2024, 1, 1, 10, 30),
            ],
            "open": [150.0, 150.1, 150.2],
            "high": [150.1, 150.2, 150.3],
            "low": [149.9, 150.0, 150.1],
            "close": [150.05, 150.15, 150.25],
            "volume": [100, 100, 100],
        })

        h1_data = pd.DataFrame({
            "time": [datetime(2024, 1, 1, 10, 0)],
            "open": [150.0],
            "high": [150.3],
            "low": [149.9],
            "close": [150.25],
            "volume": [300],
        })

        market_data = {"M15": m15_data, "H1": h1_data}

        queue = TimelineEventQueue(market_data)

        # 最初のバッチは10:00の2イベント（H1とM15）
        batch1 = next(queue)
        assert len(batch1) == 2
        # H1が先（長期足優先）
        assert batch1[0].timeframe == "H1"
        assert batch1[1].timeframe == "M15"

        # 次は10:15のM15のみ
        batch2 = next(queue)
        assert len(batch2) == 1
        assert batch2[0].timeframe == "M15"
        assert batch2[0].timestamp == datetime(2024, 1, 1, 10, 15)

    def test_timeline_event_queue_empty_data(self) -> None:
        """空データでも正常動作"""
        from autotrader.backtest.events import TimelineEventQueue

        queue = TimelineEventQueue({})
        assert len(queue) == 0

        with pytest.raises(StopIteration):
            next(queue)


class TestEvaluationResult:
    """EvaluationResultのテスト"""

    def test_evaluation_result_creation(self) -> None:
        """EvaluationResultが正しく作成される"""
        from autotrader.backtest.parallel import EvaluationResult

        result = EvaluationResult(
            timeframe="H1",
            direction="BUY",
            buy_strength=0.8,
            sell_strength=0.2,
            confidence=0.7,
            sl_pips=30.0,
            tp_pips=30.0,
            reason="RSI低, 上昇トレンド",
        )

        assert result.timeframe == "H1"
        assert result.direction == "BUY"
        assert result.confidence == 0.7


class TestEvaluateTimeframeSignal:
    """evaluate_timeframe_signalのテスト"""

    def test_evaluate_buy_signal(self) -> None:
        """買いシグナルが生成される"""
        from autotrader.backtest.parallel import (
            evaluate_timeframe_signal,
            EvaluatorParams,
        )

        row_data = {
            "rsi_14": 20.0,  # 極低RSI
            "macd": 0.1,
            "macd_signal": 0.05,
            "sma_20": 149.0,
            "sma_50": 148.0,
            "adx": 35.0,
            "atr_14": 0.5,
        }

        candle_data = {
            "open": 150.0,
            "high": 150.5,
            "low": 149.5,
            "close": 150.2,
        }

        params = EvaluatorParams(timeframe="H1")

        result = evaluate_timeframe_signal(
            timeframe="H1",
            row_data=row_data,
            candle_data=candle_data,
            params=params,
        )

        assert result.direction == "BUY"
        assert result.confidence > 0

    def test_evaluate_sell_signal(self) -> None:
        """売りシグナルが生成される"""
        from autotrader.backtest.parallel import (
            evaluate_timeframe_signal,
            EvaluatorParams,
        )

        row_data = {
            "rsi_14": 85.0,  # 極高RSI
            "macd": -0.1,
            "macd_signal": -0.05,
            "sma_20": 151.0,
            "sma_50": 152.0,
            "adx": 35.0,
            "atr_14": 0.5,
        }

        candle_data = {
            "open": 150.0,
            "high": 150.5,
            "low": 149.5,
            "close": 150.2,
        }

        params = EvaluatorParams(timeframe="H1")

        result = evaluate_timeframe_signal(
            timeframe="H1",
            row_data=row_data,
            candle_data=candle_data,
            params=params,
        )

        assert result.direction == "SELL"
        assert result.confidence > 0

    def test_evaluate_hold_signal(self) -> None:
        """HOLDシグナルが生成される（条件不十分）"""
        from autotrader.backtest.parallel import (
            evaluate_timeframe_signal,
            EvaluatorParams,
        )

        row_data = {
            "rsi_14": 50.0,  # 中立RSI
            "macd": 0.0,
            "macd_signal": 0.0,
        }

        candle_data = {
            "open": 150.0,
            "high": 150.0,
            "low": 150.0,
            "close": 150.0,
        }

        params = EvaluatorParams(timeframe="H1")

        result = evaluate_timeframe_signal(
            timeframe="H1",
            row_data=row_data,
            candle_data=candle_data,
            params=params,
        )

        assert result.direction == "HOLD"
        assert result.confidence == 0.0


class TestParallelSignalEvaluator:
    """ParallelSignalEvaluatorのテスト"""

    def test_evaluator_sequential(self) -> None:
        """シーケンシャル評価が動作する"""
        from autotrader.backtest.parallel import ParallelSignalEvaluator
        from autotrader.backtest.events import CandleEvent

        evaluator = ParallelSignalEvaluator(max_workers=2)

        events = [
            CandleEvent(
                timestamp=datetime(2024, 1, 1, 10, 0),
                timeframe="H1",
                candle_data={
                    "open": 150.0, "high": 150.5,
                    "low": 149.5, "close": 150.2, "volume": 0,
                },
                row_data={"rsi_14": 20.0, "adx": 35.0},
            ),
            CandleEvent(
                timestamp=datetime(2024, 1, 1, 10, 0),
                timeframe="M15",
                candle_data={
                    "open": 150.0, "high": 150.5,
                    "low": 149.5, "close": 150.2, "volume": 0,
                },
                row_data={"rsi_14": 25.0, "adx": 30.0},
            ),
        ]

        results = evaluator.evaluate_sequential(events)

        assert "H1" in results
        assert "M15" in results

    def test_evaluator_batch_single(self) -> None:
        """単一イベントバッチが動作する"""
        from autotrader.backtest.parallel import ParallelSignalEvaluator
        from autotrader.backtest.events import CandleEvent

        evaluator = ParallelSignalEvaluator(max_workers=2)

        events = [
            CandleEvent(
                timestamp=datetime(2024, 1, 1, 10, 0),
                timeframe="H1",
                candle_data={
                    "open": 150.0, "high": 150.5,
                    "low": 149.5, "close": 150.2, "volume": 0,
                },
                row_data={"rsi_14": 20.0},
            ),
        ]

        results = evaluator.evaluate_batch(events)

        assert "H1" in results

    def test_evaluator_htf_trend_tracking(self) -> None:
        """上位足トレンドが追跡される"""
        from autotrader.backtest.parallel import ParallelSignalEvaluator

        evaluator = ParallelSignalEvaluator()

        evaluator.update_htf_trend("H4", "BUY")
        evaluator.update_htf_trend("D1", "BUY")

        # M15より上位のH4, D1がBUYなので、HTFトレンドはBUY
        trend = evaluator.get_htf_trend("M15")
        assert trend == "BUY"

        # D1より上位はないので、HTFトレンドはNone
        trend_d1 = evaluator.get_htf_trend("D1")
        assert trend_d1 is None


class TestParallelBacktestConfig:
    """ParallelBacktestConfigのテスト"""

    def test_default_config(self) -> None:
        """デフォルト設定が正しい"""
        from autotrader.backtest.config import ParallelBacktestConfig

        config = ParallelBacktestConfig()

        assert config.enable_parallel_tf is True
        assert config.max_tf_workers == 6
        assert config.use_sequential is False
        assert "M15" in config.timeframes
        assert "H1" in config.timeframes

    def test_custom_config(self) -> None:
        """カスタム設定が適用される"""
        from autotrader.backtest.config import ParallelBacktestConfig

        config = ParallelBacktestConfig(
            enable_parallel_tf=False,
            max_tf_workers=4,
            timeframes=["M5", "M15"],
        )

        assert config.enable_parallel_tf is False
        assert config.max_tf_workers == 4
        assert config.timeframes == ["M5", "M15"]


class TestParallelEngineConfig:
    """ParallelEngineConfigのテスト"""

    def test_default_config(self) -> None:
        """デフォルト設定が正しい"""
        from autotrader.backtest.engine import ParallelEngineConfig

        config = ParallelEngineConfig()

        assert config.symbol == "USDJPY"
        assert config.initial_balance == 1_000_000.0
        assert config.enable_parallel is True
        assert config.max_tf_workers == 6
