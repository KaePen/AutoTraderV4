"""キャッシュ付きDataProviderデコレータ

DataProvider の呼び出し結果をメモリキャッシュする
Decorator パターン実装。
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from autotrader.core.enums import Timeframe
from autotrader.core.interfaces.data_provider import DataProvider

logger = logging.getLogger(__name__)


class CachedDataProvider:
    """DataProvider のキャッシュラッパー（Decorator パターン）

    DataProvider Protocol に準拠。内部の DataProvider への
    呼び出し結果をメモリキャッシュする。

    Attributes:
        _inner: 委譲先 DataProvider
        _candle_cache: get_candles のキャッシュ
        _spread_cache: get_spread のキャッシュ
    """

    def __init__(self, inner: DataProvider) -> None:
        """初期化

        Args:
            inner: 委譲先 DataProvider
        """
        self._inner = inner
        self._candle_cache: dict[str, pd.DataFrame] = {}
        self._spread_cache: dict[str, float] = {}
        self._latest_cache: dict[str, pd.Series] = {}

    def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """キャッシュ付きローソク足データ取得

        Args:
            symbol: シンボル
            timeframe: 時間足
            start: 開始日時
            end: 終了日時

        Returns:
            pd.DataFrame: OHLCVデータ
        """
        key = (
            f"{symbol}_{timeframe.value}"
            f"_{start.isoformat()}_{end.isoformat()}"
        )
        if key not in self._candle_cache:
            self._candle_cache[key] = self._inner.get_candles(
                symbol, timeframe, start, end,
            )
        return self._candle_cache[key]

    def get_latest_candle(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> pd.Series:
        """キャッシュ付き最新ローソク足取得

        Args:
            symbol: シンボル
            timeframe: 時間足

        Returns:
            pd.Series: 最新のOHLCVデータ
        """
        key = f"{symbol}_{timeframe.value}_latest"
        if key not in self._latest_cache:
            self._latest_cache[key] = (
                self._inner.get_latest_candle(symbol, timeframe)
            )
        return self._latest_cache[key]

    def get_spread(self, symbol: str) -> float:
        """キャッシュ付きスプレッド取得

        Args:
            symbol: シンボル

        Returns:
            float: スプレッド（pips）
        """
        if symbol not in self._spread_cache:
            self._spread_cache[symbol] = (
                self._inner.get_spread(symbol)
            )
        return self._spread_cache[symbol]

    def clear_cache(self) -> None:
        """全キャッシュをクリア"""
        self._candle_cache.clear()
        self._spread_cache.clear()
        self._latest_cache.clear()
        logger.debug("DataProvider キャッシュクリア")

    def clear_candle_cache(self) -> None:
        """ローソク足キャッシュのみクリア"""
        self._candle_cache.clear()
        self._latest_cache.clear()

    @property
    def inner(self) -> DataProvider:
        """委譲先 DataProvider"""
        return self._inner

    @property
    def cache_size(self) -> int:
        """キャッシュエントリ数"""
        return (
            len(self._candle_cache)
            + len(self._spread_cache)
            + len(self._latest_cache)
        )
