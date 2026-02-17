"""データプロバイダーインターフェース"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from autotrader.core.enums import Timeframe


class DataProvider(ABC):
    """データプロバイダー抽象クラス"""

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
    def get_spread(self, symbol: str) -> float:
        """現在のスプレッドを取得

        Args:
            symbol: シンボル

        Returns:
            float: スプレッド（pips）
        """
        ...
