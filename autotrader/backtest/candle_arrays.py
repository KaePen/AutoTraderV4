"""CandleArraysヘルパー

iterrows()を排除するためのnumpy配列ベースCandleアクセサ。
DataFrameの列をnumpy配列として保持し、インデックスで
高速アクセスを提供する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from autotrader.core.entities import Candle
from autotrader.core.enums import Timeframe


@dataclass(frozen=True)
class CandleArrays:
    """numpy配列ベースのキャンドルデータ

    DataFrameの各列をnumpy配列として保持し、
    インデックスベースの高速アクセスを提供する。

    Attributes:
        times: 時刻配列（datetime64）
        opens: 始値配列（float64）
        highs: 高値配列（float64）
        lows: 安値配列（float64）
        closes: 終値配列（float64）
        volumes: 出来高配列（float64）
        n_rows: 行数
    """

    times: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    n_rows: int

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> CandleArrays:
        """DataFrameからCandleArraysを生成

        Args:
            df: OHLCVデータフレーム

        Returns:
            CandleArrays: 配列ベースのキャンドルデータ
        """
        times = df["time"].values
        opens = df["open"].values.astype(np.float64)
        highs = df["high"].values.astype(np.float64)
        lows = df["low"].values.astype(np.float64)
        closes = df["close"].values.astype(np.float64)
        volumes = df["volume"].values.astype(np.float64)

        return cls(
            times=times,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            n_rows=len(df),
        )

    def get_candle(
        self,
        idx: int,
        symbol: str,
        timeframe: Timeframe,
    ) -> Candle:
        """指定インデックスのCandleオブジェクトを生成

        Args:
            idx: 配列インデックス
            symbol: 通貨ペア
            timeframe: 時間足

        Returns:
            Candle: ローソク足データ
        """
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            time=pd.Timestamp(self.times[idx]),
            open=float(self.opens[idx]),
            high=float(self.highs[idx]),
            low=float(self.lows[idx]),
            close=float(self.closes[idx]),
            volume=float(self.volumes[idx]),
        )

    def get_time(self, idx: int) -> datetime:
        """指定インデックスの時刻を取得

        Args:
            idx: 配列インデックス

        Returns:
            datetime: 時刻
        """
        return pd.Timestamp(self.times[idx]).to_pydatetime()

    def get_timestamp(self, idx: int) -> pd.Timestamp:
        """指定インデックスのpd.Timestampを取得

        Args:
            idx: 配列インデックス

        Returns:
            pd.Timestamp: タイムスタンプ
        """
        return pd.Timestamp(self.times[idx])
