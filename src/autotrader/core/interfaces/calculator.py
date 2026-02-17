"""計算機インターフェース

テクニカル指標・特徴量計算の抽象インターフェース。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from autotrader.core.enums import Timeframe


class IndicatorCalculator(ABC):
    """テクニカル指標計算インターフェース"""

    @abstractmethod
    def calculate(
        self,
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        volume: pd.Series | None = None,
    ) -> pd.DataFrame:
        """指標を計算

        Args:
            high: 高値系列
            low: 安値系列
            close: 終値系列
            volume: 出来高系列（オプション）

        Returns:
            pd.DataFrame: 計算結果
        """
        ...

    @abstractmethod
    def get_required_periods(self) -> int:
        """計算に必要な最小期間を取得

        Returns:
            int: 必要な期間数
        """
        ...


class FeatureCalculator(ABC):
    """特徴量計算インターフェース"""

    @abstractmethod
    def calculate(
        self,
        indicators: pd.DataFrame,
        ohlcv: pd.DataFrame,
    ) -> pd.DataFrame:
        """特徴量を計算

        Args:
            indicators: テクニカル指標DataFrame
            ohlcv: OHLCVデータ

        Returns:
            pd.DataFrame: 特徴量DataFrame
        """
        ...

    @abstractmethod
    def get_feature_names(self) -> list[str]:
        """特徴量名リストを取得

        Returns:
            list[str]: 特徴量名リスト
        """
        ...


class PrecomputeEngineInterface(ABC):
    """事前計算エンジンインターフェース"""

    @abstractmethod
    def precompute(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: "Timeframe",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """事前計算を実行

        Args:
            df: OHLCVデータ
            symbol: シンボル
            timeframe: 時間足
            use_cache: キャッシュ使用フラグ

        Returns:
            pd.DataFrame: 計算済みデータ
        """
        ...

    @abstractmethod
    def clear_cache(self, symbol: str | None = None) -> int:
        """キャッシュをクリア

        Args:
            symbol: シンボル（Noneで全クリア）

        Returns:
            int: 削除ファイル数
        """
        ...
