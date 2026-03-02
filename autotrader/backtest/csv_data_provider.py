"""バックテスト用 CSV DataProvider

DataProvider Protocol に準拠した CSV/Parquet データプロバイダー。
内部で既存の DataLoader を委譲使用する。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from autotrader.backtest.data_loader import DataLoader
from autotrader.core.enums import Timeframe

logger = logging.getLogger(__name__)


class CSVDataProvider:
    """CSVファイルからデータを提供するDataProvider

    DataProvider Protocol に準拠（構造的部分型）。
    内部で DataLoader に委譲してCSV/Parquetを読み込む。

    Attributes:
        _loader: DataLoader インスタンス
        _symbol: デフォルトシンボル
        _default_spread: デフォルトスプレッド（pips）
    """

    def __init__(
        self,
        data_dir: str | Path,
        symbol: str = "",
        default_spread: float = 1.5,
    ) -> None:
        """初期化

        Args:
            data_dir: データディレクトリパス
            symbol: デフォルトシンボル
            default_spread: デフォルトスプレッド（pips）
        """
        self._loader = DataLoader(data_dir=data_dir)
        self._symbol = symbol
        self._default_spread = default_spread

    def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """CSVからローソク足データを取得

        Args:
            symbol: シンボル
            timeframe: 時間足
            start: 開始日時
            end: 終了日時

        Returns:
            pd.DataFrame: OHLCVデータ
        """
        return self._loader._load_raw_data(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start,
            end_date=end,
        )

    def get_latest_candle(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> pd.Series:
        """最新のローソク足を取得

        CSVデータの末尾行を返す。

        Args:
            symbol: シンボル
            timeframe: 時間足

        Returns:
            pd.Series: 最新のOHLCVデータ

        Raises:
            ValueError: データが空の場合
        """
        # 広い範囲でロードして末尾を返す
        df = self._loader._load_raw_data(
            symbol=symbol,
            timeframe=timeframe,
            start_date=datetime(2000, 1, 1),
            end_date=datetime(2099, 12, 31),
        )
        if df.empty:
            raise ValueError(
                f"データなし: {symbol} {timeframe.value}"
            )
        return df.iloc[-1]

    def get_spread(self, symbol: str) -> float:
        """スプレッドを取得

        CSVにはリアルタイムスプレッドがないため、
        デフォルト値を返す。

        Args:
            symbol: シンボル

        Returns:
            float: スプレッド（pips）
        """
        return self._default_spread

    def get_available_range(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[datetime | None, datetime | None]:
        """利用可能なデータ範囲を取得

        DataLoader に委譲。

        Args:
            symbol: シンボル
            timeframe: 時間足

        Returns:
            tuple: (開始日時, 終了日時)
        """
        return self._loader.get_available_range(
            symbol=symbol,
            timeframe=timeframe,
        )

    @property
    def data_dir(self) -> Path:
        """データディレクトリパス"""
        return self._loader.data_dir

    @property
    def loader(self) -> DataLoader:
        """内部の DataLoader インスタンス"""
        return self._loader
