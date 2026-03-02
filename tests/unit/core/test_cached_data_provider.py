"""CachedDataProvider ユニットテスト"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from autotrader.core.cached_data_provider import CachedDataProvider
from autotrader.core.enums import Timeframe
from autotrader.core.interfaces.data_provider import DataProvider


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """テスト用OHLCVデータ"""
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=3, freq="h"),
        "open": [100.0, 101.0, 102.0],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "close": [100.5, 101.5, 102.5],
        "volume": [1000, 1100, 1200],
    })


@pytest.fixture
def mock_inner(sample_df: pd.DataFrame) -> MagicMock:
    """モック DataProvider"""
    mock = MagicMock(spec=DataProvider)
    mock.get_candles.return_value = sample_df
    mock.get_latest_candle.return_value = sample_df.iloc[-1]
    mock.get_spread.return_value = 1.5
    return mock


@pytest.fixture
def cached(mock_inner: MagicMock) -> CachedDataProvider:
    """テスト用CachedDataProvider"""
    return CachedDataProvider(inner=mock_inner)


class TestProtocolConformance:
    """Protocol準拠テスト"""

    def test_isinstance_check(self, cached: CachedDataProvider) -> None:
        """runtime_checkable DataProvider Protocol に適合"""
        assert isinstance(cached, DataProvider)


class TestGetCandlesCache:
    """get_candles キャッシュテスト"""

    def test_first_call_delegates(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
        sample_df: pd.DataFrame,
    ) -> None:
        """初回呼び出しは inner に委譲"""
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        result = cached.get_candles(
            "USDJPY", Timeframe.H1, start, end,
        )

        mock_inner.get_candles.assert_called_once_with(
            "USDJPY", Timeframe.H1, start, end,
        )
        pd.testing.assert_frame_equal(result, sample_df)

    def test_second_call_uses_cache(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
    ) -> None:
        """2回目呼び出しはキャッシュを使用"""
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        cached.get_candles("USDJPY", Timeframe.H1, start, end)
        cached.get_candles("USDJPY", Timeframe.H1, start, end)

        # inner は1回しか呼ばれない
        assert mock_inner.get_candles.call_count == 1

    def test_different_params_no_cache(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
    ) -> None:
        """異なるパラメータはキャッシュしない"""
        start1 = datetime(2024, 1, 1)
        end1 = datetime(2024, 1, 2)
        start2 = datetime(2024, 2, 1)
        end2 = datetime(2024, 2, 2)

        cached.get_candles("USDJPY", Timeframe.H1, start1, end1)
        cached.get_candles("USDJPY", Timeframe.H1, start2, end2)

        assert mock_inner.get_candles.call_count == 2


class TestGetLatestCandleCache:
    """get_latest_candle キャッシュテスト"""

    def test_caches_latest_candle(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
    ) -> None:
        """最新ローソク足をキャッシュ"""
        cached.get_latest_candle("USDJPY", Timeframe.H1)
        cached.get_latest_candle("USDJPY", Timeframe.H1)

        assert mock_inner.get_latest_candle.call_count == 1


class TestGetSpreadCache:
    """get_spread キャッシュテスト"""

    def test_caches_spread(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
    ) -> None:
        """スプレッドをキャッシュ"""
        result1 = cached.get_spread("USDJPY")
        result2 = cached.get_spread("USDJPY")

        assert result1 == 1.5
        assert result2 == 1.5
        assert mock_inner.get_spread.call_count == 1

    def test_different_symbol_no_cache(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
    ) -> None:
        """異なるシンボルはキャッシュしない"""
        cached.get_spread("USDJPY")
        cached.get_spread("EURUSD")

        assert mock_inner.get_spread.call_count == 2


class TestCacheManagement:
    """キャッシュ管理テスト"""

    def test_clear_cache(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
    ) -> None:
        """clear_cache で全キャッシュクリア"""
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        cached.get_candles("USDJPY", Timeframe.H1, start, end)
        cached.get_spread("USDJPY")
        cached.get_latest_candle("USDJPY", Timeframe.H1)
        assert cached.cache_size == 3

        cached.clear_cache()
        assert cached.cache_size == 0

    def test_clear_candle_cache(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
    ) -> None:
        """clear_candle_cache はローソク足のみクリア"""
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        cached.get_candles("USDJPY", Timeframe.H1, start, end)
        cached.get_spread("USDJPY")
        cached.get_latest_candle("USDJPY", Timeframe.H1)
        assert cached.cache_size == 3

        cached.clear_candle_cache()
        # spread_cache のみ残る
        assert cached.cache_size == 1

    def test_cache_size_property(
        self, cached: CachedDataProvider,
    ) -> None:
        """cache_size プロパティ"""
        assert cached.cache_size == 0

    def test_inner_property(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
    ) -> None:
        """inner プロパティ"""
        assert cached.inner is mock_inner

    def test_clear_then_refetch(
        self,
        cached: CachedDataProvider,
        mock_inner: MagicMock,
    ) -> None:
        """クリア後に再フェッチ"""
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        cached.get_candles("USDJPY", Timeframe.H1, start, end)
        cached.clear_cache()
        cached.get_candles("USDJPY", Timeframe.H1, start, end)

        # クリア後なので2回呼ばれる
        assert mock_inner.get_candles.call_count == 2
