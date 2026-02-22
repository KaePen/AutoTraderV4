"""MT5データ変換テスト"""

from __future__ import annotations

from datetime import timezone

import pandas as pd
import pytest

from autotrader.adapters.mt5.converters import (
    mt5_account_to_entity,
    mt5_position_to_entity,
    mt5_rates_to_dataframe,
    mt5_symbol_to_entity,
    signal_to_mt5_request,
)
from autotrader.core.entities import Signal
from autotrader.core.enums import SignalType


class TestMt5AccountToEntity:
    """mt5_account_to_entity テスト"""

    def test_正常変換(self) -> None:
        """全フィールドが正しく変換される"""
        data = {
            "balance": 1000000.0,
            "equity": 1050000.0,
            "margin": 50000.0,
            "margin_free": 1000000.0,
            "margin_level": 2100.0,
            "profit": 50000.0,
        }
        result = mt5_account_to_entity(data)
        assert result.balance == 1000000.0
        assert result.equity == 1050000.0
        assert result.margin == 50000.0
        assert result.free_margin == 1000000.0
        assert result.margin_level == 2100.0
        assert result.profit == 50000.0

    def test_空の辞書(self) -> None:
        """空の辞書でもデフォルト値で変換される"""
        result = mt5_account_to_entity({})
        assert result.balance == 0.0
        assert result.equity == 0.0

    def test_イミュータブル(self) -> None:
        """返却値がイミュータブル"""
        result = mt5_account_to_entity({"balance": 100.0})
        with pytest.raises(Exception):
            result.balance = 200.0  # type: ignore[misc]


class TestMt5SymbolToEntity:
    """mt5_symbol_to_entity テスト"""

    def test_USDJPY変換(self) -> None:
        """USDJPYシンボル情報が正しく変換される"""
        data = {
            "name": "USDJPY",
            "point": 0.001,
            "digits": 3,
            "spread": 15,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "trade_contract_size": 100000,
        }
        result = mt5_symbol_to_entity(data)
        assert result.symbol == "USDJPY"
        assert result.point == 0.001
        assert result.digits == 3
        assert result.spread == 15
        assert result.min_lot == 0.01
        assert result.max_lot == 100.0

    def test_空の辞書でデフォルト値(self) -> None:
        """空辞書でもデフォルト値が設定される"""
        result = mt5_symbol_to_entity({})
        assert result.min_lot == 0.01


class TestMt5PositionToEntity:
    """mt5_position_to_entity テスト"""

    def test_BUYポジション変換(self) -> None:
        """BUYポジションが正しく変換される"""
        data = {
            "ticket": 12345678,
            "symbol": "USDJPY",
            "type": 0,  # BUY
            "volume": 1.5,
            "price_open": 150.123,
            "sl": 149.800,
            "tp": 150.500,
            "time": 1700000000,
            "profit": 3750.0,
        }
        result = mt5_position_to_entity(data)
        assert result.ticket == 12345678
        assert result.symbol == "USDJPY"
        assert result.signal_type == SignalType.BUY
        assert result.volume == 1.5
        assert result.entry_price == 150.123
        assert result.stop_loss == 149.800
        assert result.take_profit == 150.500
        assert result.unrealized_pnl == 3750.0

    def test_SELLポジション変換(self) -> None:
        """SELLポジションのtype=1が正しく変換される"""
        data = {
            "ticket": 99999,
            "type": 1,  # SELL
            "symbol": "USDJPY",
            "volume": 0.5,
            "price_open": 150.0,
            "sl": 0,
            "tp": 0,
            "time": 1700000000,
            "profit": -500.0,
        }
        result = mt5_position_to_entity(data)
        assert result.signal_type == SignalType.SELL
        assert result.stop_loss is None  # sl=0 → None
        assert result.take_profit is None

    def test_タイムスタンプUTC変換(self) -> None:
        """UNIXタイムスタンプがUTC datetimeに変換される"""
        data = {
            "ticket": 1,
            "type": 0,
            "symbol": "USDJPY",
            "volume": 0.1,
            "price_open": 150.0,
            "time": 1700000000,
            "profit": 0,
        }
        result = mt5_position_to_entity(data)
        assert result.opened_at.tzinfo == timezone.utc


class TestMt5RatesToDataframe:
    """mt5_rates_to_dataframe テスト"""

    def test_正常変換(self) -> None:
        """レートリストがDataFrameに変換される"""
        rates = [
            {
                "time": 1700000000,
                "open": 150.0,
                "high": 150.5,
                "low": 149.5,
                "close": 150.3,
                "tick_volume": 1000,
            },
            {
                "time": 1700000060,
                "open": 150.3,
                "high": 150.8,
                "low": 150.1,
                "close": 150.6,
                "tick_volume": 800,
            },
        ]
        df = mt5_rates_to_dataframe(rates)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert "time" in df.columns
        assert "open" in df.columns
        assert "close" in df.columns

    def test_空リスト(self) -> None:
        """空リストで空DataFrameが返る"""
        df = mt5_rates_to_dataframe([])
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_timeがdatetimeに変換(self) -> None:
        """timeカラムがdatetimeに変換される"""
        rates = [
            {
                "time": 1700000000,
                "open": 150.0,
                "high": 150.5,
                "low": 149.5,
                "close": 150.3,
                "tick_volume": 1000,
            },
        ]
        df = mt5_rates_to_dataframe(rates)
        assert pd.api.types.is_datetime64_any_dtype(df["time"])


class TestSignalToMt5Request:
    """signal_to_mt5_request テスト"""

    def test_BUYシグナル変換(self) -> None:
        """BUYシグナルがMT5リクエストに変換される"""
        signal = Signal(
            signal_id="test-001",
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            confidence=0.8,
            stop_loss=149.5,
            take_profit=150.5,
        )
        tick = {"ask": 150.123, "bid": 150.120}
        result = signal_to_mt5_request(signal, 1.0, tick)

        assert result["action"] == 1  # TRADE_ACTION_DEAL
        assert result["type"] == 0   # ORDER_TYPE_BUY
        assert result["price"] == 150.123  # ask
        assert result["volume"] == 1.0
        assert result["sl"] == 149.5
        assert result["tp"] == 150.5
        assert "AT4_" in result["comment"]

    def test_SELLシグナル変換(self) -> None:
        """SELLシグナルではbid価格が使われる"""
        signal = Signal(
            signal_id="test-002",
            symbol="USDJPY",
            signal_type=SignalType.SELL,
            confidence=0.7,
        )
        tick = {"ask": 150.123, "bid": 150.120}
        result = signal_to_mt5_request(signal, 0.5, tick)

        assert result["type"] == 1   # ORDER_TYPE_SELL
        assert result["price"] == 150.120  # bid
        assert result["volume"] == 0.5

    def test_SL_TP_なし(self) -> None:
        """SL/TPなしの場合はリクエストに含まれない"""
        signal = Signal(
            signal_id="test-003",
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            confidence=0.6,
        )
        tick = {"ask": 150.0, "bid": 149.9}
        result = signal_to_mt5_request(signal, 0.1, tick)

        assert "sl" not in result
        assert "tp" not in result

    def test_BUY_pips変換(self) -> None:
        """BUYでpoint指定時にpipsから価格レベルに変換される"""
        # USDJPY: point=0.001, 1pip=0.01
        signal = Signal(
            signal_id="test-004",
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            confidence=0.8,
            stop_loss=15.0,
            take_profit=45.0,
        )
        tick = {"ask": 155.500, "bid": 155.490}
        result = signal_to_mt5_request(
            signal, 1.0, tick, point=0.001,
        )

        price = 155.500
        expected_sl = round(price - 15.0 * 0.001 * 10, 5)
        expected_tp = round(price + 45.0 * 0.001 * 10, 5)
        assert result["sl"] == pytest.approx(expected_sl, abs=1e-4)
        assert result["tp"] == pytest.approx(expected_tp, abs=1e-4)

    def test_SELL_pips変換(self) -> None:
        """SELLでpoint指定時にSLはbid+distance、TPはbid-distance"""
        signal = Signal(
            signal_id="test-005",
            symbol="USDJPY",
            signal_type=SignalType.SELL,
            confidence=0.8,
            stop_loss=20.0,
            take_profit=40.0,
        )
        tick = {"ask": 155.510, "bid": 155.500}
        result = signal_to_mt5_request(
            signal, 1.0, tick, point=0.001,
        )

        price = 155.500
        expected_sl = round(price + 20.0 * 0.001 * 10, 5)
        expected_tp = round(price - 40.0 * 0.001 * 10, 5)
        assert result["sl"] == pytest.approx(expected_sl, abs=1e-4)
        assert result["tp"] == pytest.approx(expected_tp, abs=1e-4)

    def test_point_なし_SL_TP_そのまま渡す(self) -> None:
        """point未指定時はSL/TPをそのまま渡す"""
        signal = Signal(
            signal_id="test-006",
            symbol="USDJPY",
            signal_type=SignalType.BUY,
            confidence=0.8,
            stop_loss=149.5,
            take_profit=151.5,
        )
        tick = {"ask": 150.5, "bid": 150.490}
        result = signal_to_mt5_request(signal, 1.0, tick)

        assert result["sl"] == 149.5
        assert result["tp"] == 151.5
