"""MT5データプロバイダー

DataProvider ABCのMT5実装。
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from autotrader.adapters.mt5.connection import MT5ConnectionManager
from autotrader.adapters.mt5.constants import TIMEFRAME_MAP
from autotrader.adapters.mt5.converters import (
    mt5_account_to_entity,
    mt5_rates_to_dataframe,
    mt5_symbol_to_entity,
)
from autotrader.adapters.mt5.exceptions import MT5DataError
from autotrader.core.entities import AccountInfo, SymbolInfo
from autotrader.core.enums import Timeframe
from autotrader.core.interfaces.data_provider import DataProvider

logger = logging.getLogger(__name__)


class MT5DataProvider(DataProvider):
    """MT5データプロバイダー

    MT5接続経由でローソク足・ティック・口座情報を取得。

    Attributes:
        _conn: MT5接続マネージャ
    """

    def __init__(self, conn: MT5ConnectionManager) -> None:
        """初期化

        Args:
            conn: MT5接続マネージャ
        """
        self._conn = conn

    def _tf_to_mt5(self, timeframe: Timeframe) -> int:
        """TimeframeをMT5内部IDに変換

        Args:
            timeframe: 時間足enum

        Returns:
            int: MT5時間足ID
        """
        mt5_tf = TIMEFRAME_MAP.get(timeframe.value)
        if mt5_tf is None:
            raise MT5DataError(
                f"未サポートの時間足: {timeframe.value}"
            )
        return mt5_tf

    def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """ローソク足データを取得

        Args:
            symbol: シンボル
            timeframe: 時間足
            start: 開始日時
            end: 終了日時

        Returns:
            pd.DataFrame: OHLCVデータ
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.get_candles_async(symbol, timeframe, start, end)
        )

    async def get_candles_async(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """ローソク足データを非同期取得

        Args:
            symbol: シンボル
            timeframe: 時間足
            start: 開始日時
            end: 終了日時

        Returns:
            pd.DataFrame: OHLCVデータ
        """
        mt5_tf = self._tf_to_mt5(timeframe)
        date_from = int(start.timestamp())
        date_to = int(end.timestamp())

        async with self._conn.session() as transport:
            rates = await transport.copy_rates_range(
                symbol, mt5_tf, date_from, date_to
            )

        if not rates:
            logger.warning(
                "ローソク足データなし: %s %s %s-%s",
                symbol, timeframe.value, start, end,
            )
            return pd.DataFrame(
                columns=["time", "open", "high", "low",
                         "close", "volume"]
            )

        return mt5_rates_to_dataframe(rates)

    def get_latest_candle(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> pd.Series:
        """最新のローソク足を取得

        Args:
            symbol: シンボル
            timeframe: 時間足

        Returns:
            pd.Series: 最新のOHLCVデータ
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.get_latest_candle_async(symbol, timeframe)
        )

    async def get_latest_candle_async(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> pd.Series:
        """最新のローソク足を非同期取得

        Args:
            symbol: シンボル
            timeframe: 時間足

        Returns:
            pd.Series: 最新のOHLCVデータ
        """
        mt5_tf = self._tf_to_mt5(timeframe)

        async with self._conn.session() as transport:
            rates = await transport.copy_rates_from_pos(
                symbol, mt5_tf, 0, 1
            )

        if not rates:
            raise MT5DataError(
                f"最新ローソク足取得失敗: {symbol} {timeframe.value}"
            )

        df = mt5_rates_to_dataframe(rates)
        return df.iloc[-1]

    def get_spread(self, symbol: str) -> float:
        """現在のスプレッドを取得

        Args:
            symbol: シンボル

        Returns:
            float: スプレッド（pips）
        """
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.get_spread_async(symbol)
        )

    async def get_spread_async(self, symbol: str) -> float:
        """現在のスプレッドを非同期取得

        Args:
            symbol: シンボル

        Returns:
            float: スプレッド（pips）
        """
        async with self._conn.session() as transport:
            tick = await transport.symbol_info_tick(symbol)

        if not tick:
            raise MT5DataError(
                f"ティック取得失敗: {symbol}"
            )

        ask = float(tick.get("ask", 0))
        bid = float(tick.get("bid", 0))
        # USDJPYの場合: 1pip = 0.01
        # EURUSD等の場合: 1pip = 0.0001
        spread_raw = ask - bid
        if "JPY" in symbol.upper():
            return round(spread_raw / 0.01, 1)
        return round(spread_raw / 0.0001, 1)

    async def get_tick(self, symbol: str) -> dict:
        """ティック情報取得

        Args:
            symbol: シンボル

        Returns:
            dict: ティック情報（ask, bid等）
        """
        async with self._conn.session() as transport:
            return await transport.symbol_info_tick(symbol)

    async def get_account_info(self) -> AccountInfo:
        """口座情報取得

        Returns:
            AccountInfo: 口座情報エンティティ
        """
        async with self._conn.session() as transport:
            data = await transport.account_info()

        if not data:
            raise MT5DataError("口座情報取得失敗")
        return mt5_account_to_entity(data)

    async def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """シンボル情報取得

        Args:
            symbol: シンボル

        Returns:
            SymbolInfo: シンボル情報エンティティ
        """
        async with self._conn.session() as transport:
            data = await transport.symbol_info(symbol)

        if not data:
            raise MT5DataError(
                f"シンボル情報取得失敗: {symbol}"
            )
        return mt5_symbol_to_entity(data)

    async def get_candles_from_pos(
        self,
        symbol: str,
        timeframe: Timeframe,
        count: int,
    ) -> pd.DataFrame:
        """直近N本のローソク足を非同期取得

        Args:
            symbol: シンボル
            timeframe: 時間足
            count: 取得本数

        Returns:
            pd.DataFrame: OHLCVデータ
        """
        mt5_tf = self._tf_to_mt5(timeframe)

        async with self._conn.session() as transport:
            rates = await transport.copy_rates_from_pos(
                symbol, mt5_tf, 0, count
            )

        if not rates:
            return pd.DataFrame(
                columns=["time", "open", "high", "low",
                         "close", "volume"]
            )
        return mt5_rates_to_dataframe(rates)
