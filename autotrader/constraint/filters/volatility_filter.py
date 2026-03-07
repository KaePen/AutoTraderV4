"""ボラティリティフィルター

異常なボラティリティ環境でのトレードをスキップ。
極端に高いATRは予測困難、低いATRは利益が出にくい。
"""

from __future__ import annotations

import pandas as pd

from autotrader.constraint.filters.filter_result import FilterResult


class VolatilityFilter:
    """ボラティリティフィルター

    ATRの過去分布と比較して、異常なボラティリティ環境を検出。

    Args:
        high_threshold_percentile: 高ATR閾値パーセンタイル
        low_threshold_percentile: 低ATR閾値パーセンタイル
        lookback_periods: 比較期間
    """

    def __init__(
        self,
        high_threshold_percentile: float = 95.0,
        low_threshold_percentile: float = 10.0,
        lookback_periods: int = 200,
        pip_unit: float = 0.01,
    ) -> None:
        self.high_threshold = high_threshold_percentile
        self.low_threshold = low_threshold_percentile
        self.lookback = lookback_periods
        self._pip_unit = pip_unit

    def should_skip(
        self,
        row: pd.Series,
        df_history: pd.DataFrame | None = None,
    ) -> FilterResult:
        """トレードをスキップすべきか判定

        Args:
            row: 現在のデータ行
            df_history: 過去データ（パーセンタイル計算用）

        Returns:
            FilterResult: フィルター結果
        """
        current_atr = row.get("atr_14")
        if current_atr is None or pd.isna(current_atr):
            return FilterResult(skip=False)

        # 履歴がなければ単純なチェック
        if df_history is None or df_history.empty:
            return self._simple_check(current_atr)

        # 履歴からパーセンタイルを計算
        atr_col = "atr_14"
        if atr_col not in df_history.columns:
            return FilterResult(skip=False)

        historical_atr = df_history[atr_col].dropna()
        if len(historical_atr) < self.lookback:
            return FilterResult(skip=False)

        # 直近のルックバック期間のみ使用
        recent_atr = historical_atr.iloc[-self.lookback :]

        # パーセンタイル計算
        percentile = (recent_atr < current_atr).mean() * 100

        # 高ボラティリティ
        if percentile > self.high_threshold:
            return FilterResult(
                skip=True,
                reason=f"異常高ATR({percentile:.0f}%ile)",
            )

        # 低ボラティリティ
        if percentile < self.low_threshold:
            return FilterResult(
                skip=True,
                reason=f"極低ATR({percentile:.0f}%ile)",
            )

        return FilterResult(skip=False)

    def _simple_check(self, atr: float) -> FilterResult:
        """履歴なしの単純チェック

        Args:
            atr: 現在のATR

        Returns:
            FilterResult: フィルター結果
        """
        # ATRをpipsに変換して判定（通貨ペア非依存）
        atr_pips = atr / self._pip_unit
        if atr_pips > 50.0:
            return FilterResult(
                skip=True,
                reason=f"極端高ATR({atr_pips:.1f}pips)",
            )
        if atr_pips < 3.0:
            return FilterResult(
                skip=True,
                reason=f"極端低ATR({atr_pips:.1f}pips)",
            )
        return FilterResult(skip=False)

    def calculate_atr_regime(
        self,
        row: pd.Series,
        df_history: pd.DataFrame,
    ) -> str:
        """ATRレジームを判定

        Args:
            row: 現在のデータ行
            df_history: 過去データ

        Returns:
            str: レジーム（low/normal/high/extreme）
        """
        current_atr = row.get("atr_14")
        if current_atr is None or pd.isna(current_atr):
            return "unknown"

        if df_history is None or df_history.empty:
            return "unknown"

        historical_atr = df_history["atr_14"].dropna()
        if len(historical_atr) < 50:
            return "unknown"

        percentile = (historical_atr < current_atr).mean() * 100

        if percentile >= 90:
            return "extreme"
        elif percentile >= 70:
            return "high"
        elif percentile <= 20:
            return "low"
        return "normal"
