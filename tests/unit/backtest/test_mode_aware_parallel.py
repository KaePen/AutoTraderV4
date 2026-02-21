"""モード対応並列評価のテスト"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from autotrader.core.enums import MarketRegime, TradingStrategyMode, SignalType


@dataclass
class MockCandleEvent:
    """テスト用CandleEvent"""

    timestamp: datetime
    timeframe: str
    candle_data: dict[str, float]
    row_data: dict[str, float]


class TestEntryTimeframeResolver:
    """EntryTimeframeResolverのテスト"""

    def test_get_entry_config_universal(self) -> None:
        """UNIVERSALモードのエントリー設定取得"""
        from autotrader.decision.unified.entry_resolver import (
            EntryTimeframeResolver,
        )

        resolver = EntryTimeframeResolver()
        config = resolver.get_entry_config(TradingStrategyMode.UNIVERSAL)

        assert config.primary_tf is not None
        assert config.entry_tf is not None
        assert len(config.confirm_tfs) > 0
        assert config.min_score_threshold > 0

    def test_should_check_entry_on_entry_tf(self) -> None:
        """entry_tf確定時にチェックすべき"""
        from autotrader.decision.unified.entry_resolver import (
            EntryTimeframeResolver,
        )

        resolver = EntryTimeframeResolver()
        entry_tf = resolver.get_entry_config(
            TradingStrategyMode.UNIVERSAL,
        ).entry_tf

        # entry_tf確定 → チェックすべき
        assert resolver.should_check_entry(TradingStrategyMode.UNIVERSAL, entry_tf)

    def test_resolve_entry_decision(self) -> None:
        """エントリー判定の解決"""
        from autotrader.decision.unified.entry_resolver import (
            EntryTimeframeResolver,
        )

        resolver = EntryTimeframeResolver()
        entry_tf = resolver.get_entry_config(
            TradingStrategyMode.UNIVERSAL,
        ).entry_tf
        primary_tf = resolver.get_entry_config(
            TradingStrategyMode.UNIVERSAL,
        ).primary_tf

        # スコアを高めに設定（閾値を超えるように）
        tf_directions = {
            entry_tf: "BUY",
            primary_tf: "BUY",
        }
        tf_scores = {
            entry_tf: 1.0,
            primary_tf: 1.0,
        }

        decision = resolver.resolve(
            mode=TradingStrategyMode.UNIVERSAL,
            completed_tf=entry_tf,
            tf_directions=tf_directions,
            tf_scores=tf_scores,
        )

        assert decision.entry_tf == entry_tf

    def test_resolve_no_entry_on_direction_conflict(self) -> None:
        """方向不一致では見送り"""
        from autotrader.decision.unified.entry_resolver import (
            EntryTimeframeResolver,
        )

        resolver = EntryTimeframeResolver()
        entry_tf = resolver.get_entry_config(
            TradingStrategyMode.UNIVERSAL,
        ).entry_tf
        primary_tf = resolver.get_entry_config(
            TradingStrategyMode.UNIVERSAL,
        ).primary_tf

        decision = resolver.resolve(
            mode=TradingStrategyMode.UNIVERSAL,
            completed_tf=entry_tf,
            tf_directions={
                entry_tf: "BUY",
                primary_tf: "SELL",  # primary_tfが逆方向
            },
            tf_scores={entry_tf: 0.7, primary_tf: 0.8},
        )

        assert not decision.should_enter


class TestModeAwareScoreConsensus:
    """ModeAwareScoreConsensusのテスト"""

    def test_consolidate_buy_signal(self) -> None:
        """BUYシグナルの統合"""
        from autotrader.decision.unified.mode_aware_consensus import (
            ModeAwareScoreConsensus,
            TimeframeSignal,
        )
        from autotrader.decision.unified.mode_selector import (
            TradingModeSelector,
            TradingPlan,
        )

        consensus = ModeAwareScoreConsensus()
        selector = TradingModeSelector()
        plan = selector.get_plan_for_mode(TradingStrategyMode.UNIVERSAL)

        tf_signals = {
            "M5": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.95,
                sl_pips=20.0,
                tp_pips=30.0,
            ),
            "M15": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.90,
                sl_pips=25.0,
                tp_pips=40.0,
            ),
            "H1": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.85,
                sl_pips=30.0,
                tp_pips=50.0,
            ),
        }

        result = consensus.consolidate(tf_signals, plan)

        assert result.direction == SignalType.BUY
        assert result.score > 0
        assert len(result.aligned_tfs) > 0

    def test_check_entry_conditions_on_entry_tf(self) -> None:
        """entry_tf確定時のエントリー条件チェック"""
        from autotrader.decision.unified.mode_aware_consensus import (
            ModeAwareScoreConsensus,
            TimeframeSignal,
        )
        from autotrader.decision.unified.mode_selector import (
            TradingModeSelector,
        )

        consensus = ModeAwareScoreConsensus()
        selector = TradingModeSelector()
        plan = selector.get_plan_for_mode(TradingStrategyMode.UNIVERSAL)

        tf_signals = {
            "M5": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.8,
                sl_pips=20.0,
                tp_pips=30.0,
            ),
            "M15": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.7,
                sl_pips=25.0,
                tp_pips=40.0,
            ),
        }

        result = consensus.check_entry_conditions(
            tf_signals=tf_signals,
            plan=plan,
            completed_tf=plan.entry_tf,  # entry_tf
        )

        # entry_tf確定時はエントリー判断する
        assert result.direction in (SignalType.BUY, SignalType.HOLD)

    def test_check_entry_conditions_on_non_entry_tf(self) -> None:
        """entry_tf以外ではHOLD"""
        from autotrader.decision.unified.mode_aware_consensus import (
            ModeAwareScoreConsensus,
            TimeframeSignal,
        )
        from autotrader.decision.unified.mode_selector import (
            TradingModeSelector,
        )

        consensus = ModeAwareScoreConsensus()
        selector = TradingModeSelector()
        plan = selector.get_plan_for_mode(TradingStrategyMode.UNIVERSAL)

        tf_signals = {
            "M5": TimeframeSignal(
                direction=SignalType.BUY,
                strength=0.8,
                sl_pips=20.0,
                tp_pips=30.0,
            ),
        }

        # entry_tf以外（confirm_tfsのうちの1つ）を指定
        non_entry_tf = plan.confirm_tfs[0] if plan.confirm_tfs else "D1"

        result = consensus.check_entry_conditions(
            tf_signals=tf_signals,
            plan=plan,
            completed_tf=non_entry_tf,
        )

        # entry_tf以外（confirm_tf）では閾値未達のためHOLD
        assert result.direction == SignalType.HOLD


class TestPriorityBasedEvaluator:
    """PriorityBasedEvaluatorのテスト"""

    def test_evaluate_all_timeframes_returns_result(self) -> None:
        """全TF優先度ベース評価の実行"""
        from autotrader.backtest.parallel import PriorityBasedEvaluator

        evaluator = PriorityBasedEvaluator(max_workers=2)

        events = [
            MockCandleEvent(
                timestamp=datetime(2024, 1, 1, 10, 0),
                timeframe="M15",
                candle_data={
                    "open": 150.0,
                    "high": 150.5,
                    "low": 149.5,
                    "close": 150.2,
                    "volume": 1000.0,
                },
                row_data={
                    "rsi_14": 25.0,
                    "macd": 0.01,
                    "macd_signal": -0.01,
                    "sma_20": 149.0,
                    "sma_50": 148.0,
                    "adx": 30.0,
                    "atr_14": 0.5,
                    "normalized_atr": 1.0,
                },
            ),
            MockCandleEvent(
                timestamp=datetime(2024, 1, 1, 10, 0),
                timeframe="H1",
                candle_data={
                    "open": 150.0,
                    "high": 150.5,
                    "low": 149.5,
                    "close": 150.2,
                    "volume": 5000.0,
                },
                row_data={
                    "rsi_14": 35.0,
                    "macd": 0.02,
                    "macd_signal": 0.01,
                    "sma_20": 149.5,
                    "sma_50": 148.5,
                    "adx": 25.0,
                    "atr_14": 0.8,
                    "normalized_atr": 1.0,
                },
            ),
        ]

        result = evaluator.evaluate_all_timeframes(events)

        assert result is not None
        assert result.consensus_direction in ("BUY", "SELL", "HOLD")
        assert isinstance(result.tf_results, dict)
        assert isinstance(result.weighted_score, float)
        # best_entry_tfは条件次第でNoneの場合もある
        if result.should_enter:
            assert result.best_entry_tf is not None

    def test_evaluate_empty_events(self) -> None:
        """空イベントの評価"""
        from autotrader.backtest.parallel import PriorityBasedEvaluator

        evaluator = PriorityBasedEvaluator(max_workers=2)

        result = evaluator.evaluate_all_timeframes([])

        assert result.consensus_direction == "HOLD"
        assert not result.should_enter
        assert result.weighted_score == 0.0

    def test_weighted_score_calculation(self) -> None:
        """重み付けスコア計算のテスト"""
        from autotrader.backtest.parallel import PriorityBasedEvaluator, TF_WEIGHTS

        evaluator = PriorityBasedEvaluator(max_workers=2)

        # 強いBUYシグナルを持つイベント
        events = [
            MockCandleEvent(
                timestamp=datetime(2024, 1, 1, 10, 0),
                timeframe="M15",
                candle_data={
                    "open": 150.0,
                    "high": 151.0,
                    "low": 149.0,
                    "close": 150.8,
                    "volume": 1000.0,
                },
                row_data={
                    "rsi_14": 15.0,  # 極低RSI
                    "macd": 0.05,
                    "macd_signal": -0.02,
                    "macd_histogram": 0.07,
                    "sma_20": 149.0,
                    "sma_50": 148.0,
                    "adx": 35.0,  # 強トレンド
                    "atr_14": 0.5,
                    "stoch_k": 15.0,  # 売られすぎ
                },
            ),
            MockCandleEvent(
                timestamp=datetime(2024, 1, 1, 10, 0),
                timeframe="H1",
                candle_data={
                    "open": 150.0,
                    "high": 151.0,
                    "low": 149.0,
                    "close": 150.8,
                    "volume": 5000.0,
                },
                row_data={
                    "rsi_14": 20.0,  # 低RSI
                    "macd": 0.03,
                    "macd_signal": -0.01,
                    "macd_histogram": 0.04,
                    "sma_20": 149.5,
                    "sma_50": 148.5,
                    "adx": 30.0,
                    "atr_14": 0.8,
                    "stoch_k": 20.0,
                },
            ),
        ]

        result = evaluator.evaluate_all_timeframes(events)

        # 両TFがBUYなので重み付けスコアが正の値
        assert result.weighted_score > 0
        # H1の重みがM15より高いことを確認
        assert TF_WEIGHTS["H1"] > TF_WEIGHTS["M15"]


class TestModeAwareParallelEvaluator:
    """ModeAwareParallelEvaluator（後方互換）のテスト"""

    def test_evaluate_with_mode_delegates_to_priority(self) -> None:
        """evaluate_with_modeがPriorityBasedEvaluatorに委譲"""
        from autotrader.backtest.parallel import ModeAwareParallelEvaluator

        evaluator = ModeAwareParallelEvaluator(max_workers=2)

        events = [
            MockCandleEvent(
                timestamp=datetime(2024, 1, 1, 10, 0),
                timeframe="M15",
                candle_data={
                    "open": 150.0,
                    "high": 150.5,
                    "low": 149.5,
                    "close": 150.2,
                    "volume": 1000.0,
                },
                row_data={
                    "rsi_14": 25.0,
                    "macd": 0.01,
                    "macd_signal": -0.01,
                    "sma_20": 149.0,
                    "sma_50": 148.0,
                    "adx": 30.0,
                    "atr_14": 0.5,
                },
            ),
        ]

        result = evaluator.evaluate_with_mode(events)

        # PriorityEvaluationResultのフィールドを確認
        assert result is not None
        assert result.consensus_direction in ("BUY", "SELL", "HOLD")
        assert isinstance(result.tf_results, dict)
        assert isinstance(result.weighted_score, float)

    def test_evaluate_empty_events(self) -> None:
        """空イベントの評価"""
        from autotrader.backtest.parallel import ModeAwareParallelEvaluator

        evaluator = ModeAwareParallelEvaluator(max_workers=2)

        result = evaluator.evaluate_with_mode([])

        assert result.consensus_direction == "HOLD"
        assert not result.should_enter


class TestTradingModeSelector:
    """TradingModeSelectorのテスト"""

    def test_always_returns_universal(self) -> None:
        """全レジームでUNIVERSAL選択"""
        from autotrader.decision.unified.mode_selector import (
            TradingModeSelector,
        )

        selector = TradingModeSelector()

        for regime in [
            MarketRegime.HIGH_VOL,
            MarketRegime.TREND,
            MarketRegime.RANGE,
            MarketRegime.LOW_VOL,
        ]:
            plan = selector.select(
                regime=regime,
                volatility_level=1.0,
                htf_alignment=0.5,
            )
            assert plan.mode == TradingStrategyMode.UNIVERSAL
