"""CSVDataProvider ユニットテスト"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from autotrader.backtest.csv_data_provider import CSVDataProvider
from autotrader.core.enums import Timeframe
from autotrader.core.interfaces.data_provider import DataProvider


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """テスト用OHLCVデータ"""
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=5, freq="h"),
        "open": [100.0, 101.0, 102.0, 103.0, 104.0],
        "high": [101.0, 102.0, 103.0, 104.0, 105.0],
        "low": [99.0, 100.0, 101.0, 102.0, 103.0],
        "close": [100.5, 101.5, 102.5, 103.5, 104.5],
        "volume": [1000, 1100, 1200, 1300, 1400],
    })


@pytest.fixture
def provider(tmp_path: Path) -> CSVDataProvider:
    """テスト用CSVDataProvider"""
    return CSVDataProvider(
        data_dir=tmp_path,
        symbol="USDJPY",
        default_spread=2.0,
    )


class TestCSVDataProviderProtocol:
    """Protocol準拠テスト"""

    def test_isinstance_check(self, provider: CSVDataProvider) -> None:
        """runtime_checkable DataProvider Protocol に適合"""
        assert isinstance(provider, DataProvider)

    def test_has_get_candles(self, provider: CSVDataProvider) -> None:
        """get_candles メソッドが存在"""
        assert hasattr(provider, "get_candles")
        assert callable(provider.get_candles)

    def test_has_get_latest_candle(
        self, provider: CSVDataProvider,
    ) -> None:
        """get_latest_candle メソッドが存在"""
        assert hasattr(provider, "get_latest_candle")
        assert callable(provider.get_latest_candle)

    def test_has_get_spread(self, provider: CSVDataProvider) -> None:
        """get_spread メソッドが存在"""
        assert hasattr(provider, "get_spread")
        assert callable(provider.get_spread)


class TestGetCandles:
    """get_candles テスト"""

    def test_delegates_to_data_loader(
        self, provider: CSVDataProvider, sample_df: pd.DataFrame,
    ) -> None:
        """DataLoader._load_raw_data に委譲"""
        provider._loader._load_raw_data = MagicMock(
            return_value=sample_df,
        )
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        result = provider.get_candles(
            "USDJPY", Timeframe.H1, start, end,
        )

        provider._loader._load_raw_data.assert_called_once_with(
            symbol="USDJPY",
            timeframe=Timeframe.H1,
            start_date=start,
            end_date=end,
        )
        pd.testing.assert_frame_equal(result, sample_df)

    def test_returns_empty_df_when_no_data(
        self, provider: CSVDataProvider,
    ) -> None:
        """データがない場合は空のDataFrame"""
        empty_df = pd.DataFrame(
            columns=["time", "open", "high", "low", "close", "volume"]
        )
        provider._loader._load_raw_data = MagicMock(
            return_value=empty_df,
        )

        result = provider.get_candles(
            "USDJPY", Timeframe.H1,
            datetime(2024, 1, 1), datetime(2024, 1, 2),
        )
        assert result.empty


class TestGetLatestCandle:
    """get_latest_candle テスト"""

    def test_returns_last_row(
        self, provider: CSVDataProvider, sample_df: pd.DataFrame,
    ) -> None:
        """最終行を返す"""
        provider._loader._load_raw_data = MagicMock(
            return_value=sample_df,
        )

        result = provider.get_latest_candle(
            "USDJPY", Timeframe.H1,
        )

        assert result["close"] == 104.5
        assert result["volume"] == 1400

    def test_raises_on_empty_data(
        self, provider: CSVDataProvider,
    ) -> None:
        """データが空の場合はValueError"""
        empty_df = pd.DataFrame(
            columns=["time", "open", "high", "low", "close", "volume"]
        )
        provider._loader._load_raw_data = MagicMock(
            return_value=empty_df,
        )

        with pytest.raises(ValueError, match="データなし"):
            provider.get_latest_candle("USDJPY", Timeframe.H1)


class TestGetSpread:
    """get_spread テスト"""

    def test_returns_default_spread(
        self, provider: CSVDataProvider,
    ) -> None:
        """デフォルトスプレッドを返す"""
        assert provider.get_spread("USDJPY") == 2.0

    def test_custom_default_spread(self, tmp_path: Path) -> None:
        """カスタムスプレッド設定"""
        p = CSVDataProvider(
            data_dir=tmp_path,
            default_spread=3.5,
        )
        assert p.get_spread("EURUSD") == 3.5


class TestProperties:
    """プロパティテスト"""

    def test_data_dir(
        self, provider: CSVDataProvider, tmp_path: Path,
    ) -> None:
        """data_dir プロパティ"""
        assert provider.data_dir == tmp_path

    def test_loader(self, provider: CSVDataProvider) -> None:
        """loader プロパティ"""
        from autotrader.backtest.data_loader import DataLoader
        assert isinstance(provider.loader, DataLoader)


class TestGetAvailableRange:
    """get_available_range テスト"""

    def test_delegates_to_loader(
        self, provider: CSVDataProvider,
    ) -> None:
        """DataLoader.get_available_range に委譲"""
        expected = (
            datetime(2020, 1, 1),
            datetime(2024, 12, 31),
        )
        provider._loader.get_available_range = MagicMock(
            return_value=expected,
        )

        result = provider.get_available_range(
            "USDJPY", Timeframe.H1,
        )

        assert result == expected
        provider._loader.get_available_range.assert_called_once_with(
            symbol="USDJPY",
            timeframe=Timeframe.H1,
        )
