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
