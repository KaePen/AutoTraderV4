"""市場構造分析（BOS/CHoCH検出）

スイングポイントのパターンから市場構造を分析し、
Break of Structure (BOS) / Change of Character (CHoCH) を検出する。

BOS: トレンド継続のシグナル
- 上昇トレンド中に前回高値を超える（強気BOS）
- 下降トレンド中に前回安値を下回る（弱気BOS）

CHoCH: トレンド反転の可能性を示すシグナル
- 上昇トレンド中に前回安値を下回る（弱気CHoCH）
- 下降トレンド中に前回高値を超える（強気CHoCH）
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from autotrader.calculator.market_structure.swing_analyzer import (
    SwingAnalyzer,
    SwingPoint,
    SwingType,
)


class TrendState(Enum):
    """トレンド状態"""

    BULLISH = "bullish"  # HH + HL パターン
    BEARISH = "bearish"  # LH + LL パターン
    CONSOLIDATION = "consolidation"  # 明確な構造なし
    REVERSAL_BULLISH = "reversal_bullish"  # 強気反転シグナル
    REVERSAL_BEARISH = "reversal_bearish"  # 弱気反転シグナル


@dataclass(frozen=True)
class StructureSignal:
    """構造シグナル

    Attributes:
        bar_index: シグナル発生バーインデックス
        signal_type: BOS or CHoCH
        direction: 1（強気）or -1（弱気）
        price_level: 突破した価格レベル
        trend_state: シグナル発生時のトレンド状態
    """

    bar_index: int
    signal_type: str  # "BOS" or "CHoCH"
    direction: int  # 1: bullish, -1: bearish
    price_level: float
    trend_state: TrendState


class StructureAnalyzer:
    """市場構造分析器

    スイングポイントのシーケンスを分析し、
    BOS/CHoCHシグナルを検出する。

    Args:
        swing_analyzer: スイング検出器
        min_swings: 構造判定に必要な最小スイング数
    """

    def __init__(
        self,
        swing_analyzer: SwingAnalyzer | None = None,
        min_swings: int = 4,
    ) -> None:
        self.swing_analyzer = swing_analyzer or SwingAnalyzer()
        self.min_swings = min_swings

    def _determine_trend_from_swings(
        self,
        recent_highs: list[SwingPoint],
        recent_lows: list[SwingPoint],
    ) -> TrendState:
        """スイングパターンからトレンド状態を判定

        Args:
            recent_highs: 直近のスイングハイリスト
            recent_lows: 直近のスイングローリスト

        Returns:
            TrendState: 判定されたトレンド状態
        """
        if len(recent_highs) < 2 or len(recent_lows) < 2:
            return TrendState.CONSOLIDATION

        # HH (Higher High) / LH (Lower High) チェック
        hh_count = sum(
            1
            for i in range(1, len(recent_highs))
            if recent_highs[i].price > recent_highs[i - 1].price
        )
        lh_count = len(recent_highs) - 1 - hh_count

        # HL (Higher Low) / LL (Lower Low) チェック
        hl_count = sum(
            1
            for i in range(1, len(recent_lows))
            if recent_lows[i].price > recent_lows[i - 1].price
        )
        ll_count = len(recent_lows) - 1 - hl_count

        # 上昇トレンド: HH + HL が優勢
        if hh_count >= 1 and hl_count >= 1:
            return TrendState.BULLISH

        # 下降トレンド: LH + LL が優勢
        if lh_count >= 1 and ll_count >= 1:
            return TrendState.BEARISH

        return TrendState.CONSOLIDATION

    def detect_bos_choch(
        self,
        df: pd.DataFrame,
    ) -> pd.DataFrame:
        """BOS/CHoCHシグナルを検出

        Args:
            df: OHLC DataFrame

        Returns:
            pd.DataFrame: BOS/CHoCHシグナル列を追加したDataFrame
        """
        result = pd.DataFrame(index=df.index)
        result["bos_signal"] = 0  # 1: bullish BOS, -1: bearish BOS
        result["choch_signal"] = 0  # 1: bullish CHoCH, -1: bearish CHoCH
        result["structure_direction"] = 0  # 1: bullish, -1: bearish
        result["trend_state"] = TrendState.CONSOLIDATION.value

        swing_high_flags = self.swing_analyzer.detect_swing_highs(df)
        swing_low_flags = self.swing_analyzer.detect_swing_lows(df)

        high_prices = df["high"].values
        low_prices = df["low"].values

        # スイングポイントを収集
        swing_high_levels: list[tuple[int, float]] = []
        swing_low_levels: list[tuple[int, float]] = []

        for bar_idx in range(len(df)):
            if swing_high_flags.iloc[bar_idx]:
                swing_high_levels.append(
                    (bar_idx, float(high_prices[bar_idx]))
                )
            if swing_low_flags.iloc[bar_idx]:
                swing_low_levels.append(
                    (bar_idx, float(low_prices[bar_idx]))
                )

            # 十分なスイングポイントがなければスキップ
            if len(swing_high_levels) < 2 or len(swing_low_levels) < 2:
                continue

            # 直近のスイングポイントを取得
            recent_highs = swing_high_levels[-3:]
            recent_lows = swing_low_levels[-3:]

            # 現在のトレンド状態を判定
            prev_trend = self._get_trend_from_recent(
                recent_highs[:-1] if len(recent_highs) > 1 else recent_highs,
                recent_lows[:-1] if len(recent_lows) > 1 else recent_lows,
            )
            curr_trend = self._get_trend_from_recent(recent_highs, recent_lows)

            result.iloc[bar_idx, result.columns.get_loc("trend_state")] = (
                curr_trend.value
            )

            if curr_trend == TrendState.BULLISH:
                result.iloc[
                    bar_idx, result.columns.get_loc("structure_direction")
                ] = 1
            elif curr_trend == TrendState.BEARISH:
                result.iloc[
                    bar_idx, result.columns.get_loc("structure_direction")
                ] = -1

            # BOS/CHoCH検出
            current_close = df["close"].iloc[bar_idx]
            current_high = df["high"].iloc[bar_idx]
            current_low = df["low"].iloc[bar_idx]

            # 直近の確定したスイングレベル
            last_swing_high = swing_high_levels[-1][1]
            last_swing_low = swing_low_levels[-1][1]

            # 2つ前のスイングレベル（比較用）
            prev_swing_high = (
                swing_high_levels[-2][1]
                if len(swing_high_levels) >= 2
                else last_swing_high
            )
            prev_swing_low = (
                swing_low_levels[-2][1]
                if len(swing_low_levels) >= 2
                else last_swing_low
            )

            # BOS検出
            if curr_trend == TrendState.BULLISH:
                # 上昇トレンド中に前回高値を超える → 強気BOS
                if current_high > prev_swing_high:
                    result.iloc[
                        bar_idx, result.columns.get_loc("bos_signal")
                    ] = 1
            elif curr_trend == TrendState.BEARISH:
                # 下降トレンド中に前回安値を下回る → 弱気BOS
                if current_low < prev_swing_low:
                    result.iloc[
                        bar_idx, result.columns.get_loc("bos_signal")
                    ] = -1

            # CHoCH検出
            if prev_trend == TrendState.BULLISH:
                # 上昇から下降への転換（安値を割る）
                if current_low < last_swing_low:
                    result.iloc[
                        bar_idx, result.columns.get_loc("choch_signal")
                    ] = -1
            elif prev_trend == TrendState.BEARISH:
                # 下降から上昇への転換（高値を超える）
                if current_high > last_swing_high:
                    result.iloc[
                        bar_idx, result.columns.get_loc("choch_signal")
                    ] = 1

        return result

    def _get_trend_from_recent(
        self,
        highs: list[tuple[int, float]],
        lows: list[tuple[int, float]],
    ) -> TrendState:
        """直近のスイングレベルからトレンドを判定

        Args:
            highs: (bar_index, price) のリスト
            lows: (bar_index, price) のリスト

        Returns:
            TrendState: トレンド状態
        """
        if len(highs) < 2 or len(lows) < 2:
            return TrendState.CONSOLIDATION

        # 高値の比較
        higher_highs = highs[-1][1] > highs[-2][1]
        lower_highs = highs[-1][1] < highs[-2][1]

        # 安値の比較
        higher_lows = lows[-1][1] > lows[-2][1]
        lower_lows = lows[-1][1] < lows[-2][1]

        # HH + HL = 上昇トレンド
        if higher_highs and higher_lows:
            return TrendState.BULLISH

        # LH + LL = 下降トレンド
        if lower_highs and lower_lows:
            return TrendState.BEARISH

        return TrendState.CONSOLIDATION

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """全市場構造指標を計算

        Args:
            df: OHLC DataFrame

        Returns:
            pd.DataFrame: 市場構造指標を含むDataFrame
        """
        # スイング指標
        swing_df = self.swing_analyzer.calculate_all(df)

        # BOS/CHoCH指標
        structure_df = self.detect_bos_choch(df)

        # 結合
        result = pd.concat([swing_df, structure_df], axis=1)

        # 累積BOS/CHoCHカウント
        result["bos_bullish_count"] = (result["bos_signal"] == 1).cumsum()
        result["bos_bearish_count"] = (result["bos_signal"] == -1).cumsum()
        result["choch_bullish_count"] = (result["choch_signal"] == 1).cumsum()
        result["choch_bearish_count"] = (result["choch_signal"] == -1).cumsum()

        # 直近のBOS/CHoCH発生からのバー数
        result["bars_since_bos"] = self._bars_since_signal(
            result["bos_signal"] != 0
        )
        result["bars_since_choch"] = self._bars_since_signal(
            result["choch_signal"] != 0
        )

        return result

    def _bars_since_signal(self, signal_mask: pd.Series) -> pd.Series:
        """シグナル発生からのバー数を計算

        Args:
            signal_mask: シグナル発生を示すbool Series

        Returns:
            pd.Series: バー数
        """
        groups = signal_mask.cumsum()
        return groups.groupby(groups).cumcount()

    def get_current_structure(self, df: pd.DataFrame) -> dict:
        """現在の市場構造状態を取得

        Args:
            df: OHLC DataFrame

        Returns:
            dict: 現在の構造状態
        """
        structure_df = self.calculate_all(df)
        last_row = structure_df.iloc[-1]

        recent_highs, recent_lows = self.swing_analyzer.get_recent_swings(
            df, n_highs=3, n_lows=3
        )

        return {
            "trend_state": last_row["trend_state"],
            "structure_direction": int(last_row["structure_direction"]),
            "last_bos": int(last_row["bos_signal"]),
            "last_choch": int(last_row["choch_signal"]),
            "bars_since_bos": int(last_row["bars_since_bos"]),
            "bars_since_choch": int(last_row["bars_since_choch"]),
            "last_swing_high": (
                float(last_row["last_swing_high"])
                if not pd.isna(last_row["last_swing_high"])
                else None
            ),
            "last_swing_low": (
                float(last_row["last_swing_low"])
                if not pd.isna(last_row["last_swing_low"])
                else None
            ),
            "recent_swing_highs": [
                {"bar_index": p.bar_index, "price": p.price}
                for p in recent_highs
            ],
            "recent_swing_lows": [
                {"bar_index": p.bar_index, "price": p.price} for p in recent_lows
            ],
        }
