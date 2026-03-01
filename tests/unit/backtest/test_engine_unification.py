"""BacktestEngine 統合テスト

BacktestEngine が SignalGeneratorProtocol を正しく使用できることを確認。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pandas as pd
import pytest

from autotrader.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    SignalGeneratorProtocol,
    UnifiedBotAdapter,
)
from autotrader.core.entities import Candle, Signal
from autotrader.core.enums import SignalType, Timeframe


def test_backtest_engine_with_protocol():
    """BacktestEngine が Protocol を使用できることを確認"""
    # モックシグナルジェネレータを作成
    mock_generator = Mock(spec=SignalGeneratorProtocol)
    mock_generator.generate_signal.return_value = None

    # BacktestEngine を作成
    config = BacktestConfig(
        symbol="USDJPY",
        timeframe=Timeframe.H1,
        start_date=datetime(2023, 1, 1),
        end_date=datetime(2023, 1, 2),
    )
    engine = BacktestEngine(config=config)

    # signal_generator を注入
    engine.signal_generator = mock_generator

    # run() は実際のデータが必要なのでスキップ
    # インスタンス化と signal_generator の注入が成功すればOK
    assert engine.signal_generator is not None
    assert isinstance(engine.signal_generator, SignalGeneratorProtocol)


def test_backtest_engine_without_signal_generator():
    """signal_generator がない場合でも正常に動作することを確認"""
    config = BacktestConfig(
        symbol="USDJPY",
        timeframe=Timeframe.H1,
    )
    engine = BacktestEngine(config=config)

    # signal_generator が None でも初期化成功
    assert engine.signal_generator is None


def test_backtest_config_no_legacy_fields():
    """BacktestConfig から重複設定（stop_loss_pips, take_profit_pips）が
    削除されていることを確認"""
    config = BacktestConfig()

    # stop_loss_pips, take_profit_pips は削除済み
    assert not hasattr(config, "stop_loss_pips")
    assert not hasattr(config, "take_profit_pips")

    # min_confidence は DB保存用に残っている
    assert hasattr(config, "min_confidence")


def test_backtest_engine_no_legacy_methods():
    """BacktestEngine から重複メソッドが削除されていることを確認"""
    config = BacktestConfig()
    engine = BacktestEngine(config=config)

    # 削除されたメソッドが存在しないことを確認
    assert not hasattr(engine, "_extract_indicators")
    assert not hasattr(engine, "_build_context")
    assert not hasattr(engine, "_calculate_stop_loss")
    assert not hasattr(engine, "_calculate_take_profit")

    # 必要なメソッドは残っている
    assert hasattr(engine, "_row_to_candle")
    assert hasattr(engine, "_create_empty_result")
    assert hasattr(engine, "run")


def test_unified_bot_adapter_conforms_to_protocol():
    """UnifiedBotAdapter が SignalGeneratorProtocol に準拠していることを確認"""
    # モック UnifiedTradeBot を作成
    mock_bot = Mock()

    # Mock ConsolidatedSignal を作成
    mock_consolidated = Mock()
    mock_consolidated.direction = SignalType.BUY
    mock_consolidated.confidence = 0.8
    mock_consolidated.sl_pips = 20.0
    mock_consolidated.tp_pips = 40.0
    mock_consolidated.rationale = "Test signal"

    mock_bot.generate_signal.return_value = mock_consolidated

    # UnifiedBotAdapter を作成
    adapter = UnifiedBotAdapter(
        bot=mock_bot,
        symbol="USDJPY",
        min_confidence=0.5,
    )

    # Protocol に準拠しているか確認
    assert isinstance(adapter, SignalGeneratorProtocol)

    # シグナル生成テスト
    test_candle = Candle(
        symbol="USDJPY",
        timeframe=Timeframe.H1,
        time=datetime(2023, 1, 1, 12, 0),
        open=130.0,
        high=130.5,
        low=129.5,
        close=130.2,
        volume=1000,
    )

    signal = adapter.generate_signal(
        current_time=datetime(2023, 1, 1, 12, 0),
        candle=test_candle,
    )

    # シグナルが生成されていることを確認
    assert signal is not None
    assert signal.signal_type == SignalType.BUY
    assert signal.symbol == "USDJPY"


def test_unified_bot_adapter_hold_signal():
    """UnifiedBotAdapter が HOLD シグナルを None として返すことを確認"""
    mock_bot = Mock()

    mock_consolidated = Mock()
    mock_consolidated.direction = SignalType.HOLD
    mock_consolidated.confidence = 0.0
    mock_bot.generate_signal.return_value = mock_consolidated

    adapter = UnifiedBotAdapter(
        bot=mock_bot,
        symbol="USDJPY",
        min_confidence=0.5,
    )

    test_candle = Candle(
        symbol="USDJPY",
        timeframe=Timeframe.H1,
        time=datetime(2023, 1, 1, 12, 0),
        open=130.0,
        high=130.5,
        low=129.5,
        close=130.2,
        volume=1000,
    )

    signal = adapter.generate_signal(
        current_time=datetime(2023, 1, 1, 12, 0),
        candle=test_candle,
    )

    # HOLD は None として返される
    assert signal is None


def test_unified_bot_adapter_low_confidence_filtered():
    """UnifiedBotAdapter が低確度シグナルをフィルタすることを確認"""
    mock_bot = Mock()

    mock_consolidated = Mock()
    mock_consolidated.direction = SignalType.BUY
    mock_consolidated.confidence = 0.3  # min_confidence(0.5) 未満
    mock_bot.generate_signal.return_value = mock_consolidated

    adapter = UnifiedBotAdapter(
        bot=mock_bot,
        symbol="USDJPY",
        min_confidence=0.5,
    )

    test_candle = Candle(
        symbol="USDJPY",
        timeframe=Timeframe.H1,
        time=datetime(2023, 1, 1, 12, 0),
        open=130.0,
        high=130.5,
        low=129.5,
        close=130.2,
        volume=1000,
    )

    signal = adapter.generate_signal(
        current_time=datetime(2023, 1, 1, 12, 0),
        candle=test_candle,
    )

    # 低確度シグナルは None
    assert signal is None
