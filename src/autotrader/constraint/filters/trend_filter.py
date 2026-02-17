"""トレンドフィルターモジュール

HTFトレンド整合性フィルターを提供。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from autotrader.core.enums import SignalType


class TrendFilter:
    """HTFトレンド整合性フィルター

    上位時間足（H4, D1）のトレンドとシグナル方向の
    整合性を確認するフィルター。
    """

    def __init__(
        self,
        htf_timeframes: list[str] | None = None,
        min_alignment_score: float = 1.0,
    ):
        """初期化

        Args:
            htf_timeframes: チェックする上位時間足リスト
            min_alignment_score: 最小一致スコア
        """
        self._htf_timeframes = htf_timeframes or ["H4", "D1"]
        self._min_alignment_score = min_alignment_score

    def is_aligned(
        self,
        signal_direction: SignalType,
        htf_data: dict[str, dict[str, Any]],
    ) -> bool:
        """上位時間足とのトレンド整合性を確認

        Args:
            signal_direction: シグナル方向
            htf_data: 上位時間足データ {timeframe: {close, sma_20, sma_50, ...}}

        Returns:
            bool: トレンドが整合しているか
        """
        if signal_direction == SignalType.HOLD:
            return False

        score = self.calculate_alignment_score(signal_direction, htf_data)
        return score >= self._min_alignment_score

    def calculate_alignment_score(
        self,
        signal_direction: SignalType,
        htf_data: dict[str, dict[str, Any]],
    ) -> float:
        """整合性スコアを計算

        各時間足で以下のスコアを付与:
        - 完全一致（close > sma_20 > sma_50 for BUY）: 1.0
        - 部分一致（close > sma_20 for BUY）: 0.5
        - 不一致: 0.0

        Args:
            signal_direction: シグナル方向
            htf_data: 上位時間足データ

        Returns:
            float: 整合性スコア
        """
        aligned_score = 0.0

        for tf in self._htf_timeframes:
            if tf not in htf_data:
                continue

            data = htf_data[tf]
            score = self._check_single_timeframe(signal_direction, data)
            aligned_score += score

        return aligned_score

    def _check_single_timeframe(
        self,
        signal_direction: SignalType,
        data: dict[str, Any],
    ) -> float:
        """単一時間足のトレンド整合性をチェック

        Args:
            signal_direction: シグナル方向
            data: 時間足データ

        Returns:
            float: スコア（0.0, 0.5, 1.0）
        """
        close = data.get("close")
        sma_20 = data.get("sma_20")
        sma_50 = data.get("sma_50")

        # 必要なデータがない場合
        if any(v is None for v in [close, sma_20, sma_50]):
            return 0.0

        # NaN チェック
        if any(pd.isna(v) for v in [close, sma_20, sma_50]):
            return 0.0

        # トレンド判定
        if signal_direction == SignalType.BUY:
            if close > sma_20 > sma_50:
                return 1.0
            elif close > sma_20:
                return 0.5
        elif signal_direction == SignalType.SELL:
            if close < sma_20 < sma_50:
                return 1.0
            elif close < sma_20:
                return 0.5

        return 0.0

    def get_htf_data_from_row(
        self,
        row: pd.Series | dict[str, Any],
    ) -> dict[str, Any]:
        """データ行からHTFデータを抽出

        Args:
            row: データ行

        Returns:
            dict: HTFデータ
        """
        if isinstance(row, pd.Series):
            return {
                "close": row.get("close"),
                "sma_20": row.get("sma_20"),
                "sma_50": row.get("sma_50"),
            }
        return {
            "close": row.get("close"),
            "sma_20": row.get("sma_20"),
            "sma_50": row.get("sma_50"),
        }
