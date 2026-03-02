"""計算機インターフェース

事前計算エンジンのProtocolインターフェース。
未使用の IndicatorCalculator / FeatureCalculator は削除済み。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd

if TYPE_CHECKING:
    from autotrader.core.enums import Timeframe


@runtime_checkable
class PrecomputeEngineProtocol(Protocol):
    """事前計算エンジンProtocol"""

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

    def clear_cache(self, symbol: str | None = None) -> int:
        """キャッシュをクリア

        Args:
            symbol: シンボル（Noneで全クリア）

        Returns:
            int: 削除ファイル数
        """
        ...
