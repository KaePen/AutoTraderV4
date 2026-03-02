"""データプロバイダーインターフェース"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from autotrader.core.enums import Timeframe


@runtime_checkable
class DataProvider(Protocol):
    """データプロバイダーProtocol"""

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
        ...

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
        ...

    def get_spread(self, symbol: str) -> float:
        """現在のスプレッドを取得

        Args:
            symbol: シンボル

        Returns:
            float: スプレッド（pips）
        """
        ...
