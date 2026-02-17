"""ADXフィルターモジュール

ADXベースのトレンド強度フィルターを提供。
"""

from __future__ import annotations

from typing import Any

import pandas as pd


class ADXFilter:
    """ADXベースのトレンド強度フィルター

    ADX（Average Directional Index）を使用して
    トレンドの強度を判定するフィルター。

    ADX > 25: 強いトレンド
    ADX > 20: 中程度のトレンド
    ADX < 20: レンジ相場
    """

    def __init__(
        self,
        threshold: float = 25.0,
        check_timeframes: list[str] | None = None,
    ):
        """初期化

        Args:
            threshold: ADX閾値（この値以上で強いトレンドと判定）
            check_timeframes: チェックする時間足リスト
        """
        self._threshold = threshold
        self._check_timeframes = check_timeframes or ["H1", "H4"]

    @property
    def threshold(self) -> float:
        """ADX閾値を取得"""
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        """ADX閾値を設定"""
        self._threshold = value

    def is_strong_trend(self, adx_value: float | None) -> bool:
        """トレンド強度を確認

        Args:
            adx_value: ADX値

        Returns:
            bool: 強いトレンドかどうか
        """
        if adx_value is None:
            return False

        if pd.isna(adx_value):
            return False

        return adx_value >= self._threshold

    def check_multiple_timeframes(
        self,
        htf_data: dict[str, dict[str, Any]],
        require_all: bool = False,
    ) -> bool:
        """複数時間足でADXをチェック

        Args:
            htf_data: 時間足別データ {timeframe: {adx: value, ...}}
            require_all: 全時間足で条件を満たす必要があるか

        Returns:
            bool: ADX条件を満たすか
        """
        results = []

        for tf in self._check_timeframes:
            if tf not in htf_data:
                continue

            data = htf_data[tf]
            adx = data.get("adx")
            results.append(self.is_strong_trend(adx))

        if not results:
            return False

        if require_all:
            return all(results)
        return any(results)

    def get_adx_from_row(
        self,
        row: pd.Series | dict[str, Any],
    ) -> float | None:
        """データ行からADX値を抽出

        Args:
            row: データ行

        Returns:
            float | None: ADX値
        """
        if isinstance(row, pd.Series):
            adx = row.get("adx")
        else:
            adx = row.get("adx")

        if adx is None:
            return None

        if pd.isna(adx):
            return None

        return float(adx)

    def get_trend_strength_category(
        self,
        adx_value: float | None,
    ) -> str:
        """トレンド強度カテゴリを取得

        Args:
            adx_value: ADX値

        Returns:
            str: カテゴリ名（strong/moderate/weak/unknown）
        """
        if adx_value is None or pd.isna(adx_value):
            return "unknown"

        if adx_value >= 40:
            return "very_strong"
        elif adx_value >= 25:
            return "strong"
        elif adx_value >= 20:
            return "moderate"
        else:
            return "weak"
