"""ダイバージェンス検出

価格とオシレーターの乖離からトレンド反転シグナルを検出。
"""

from __future__ import annotations

from enum import Enum

import pandas as pd


class DivergenceType(Enum):
    """ダイバージェンスタイプ"""

    NONE = "none"
    REGULAR_BULLISH = "regular_bullish"  # 価格LL, RSI HL（反転上昇）
    REGULAR_BEARISH = "regular_bearish"  # 価格HH, RSI LH（反転下落）
    HIDDEN_BULLISH = "hidden_bullish"    # 価格HL, RSI LL（トレンド継続）
    HIDDEN_BEARISH = "hidden_bearish"    # 価格LH, RSI HH（トレンド継続）


class DivergenceDetector:
    """ダイバージェンス検出クラス

    スイング高値/安値を検出し、価格とオシレーターの
    ダイバージェンスを判定する。

    Args:
        swing_lookback: スイング検出の確認期間（デフォルト: 5）
        min_swing_distance: 最小スイング間隔（デフォルト: 5）
        max_swing_distance: 最大スイング間隔（デフォルト: 50）
    """

    def __init__(
        self,
        swing_lookback: int = 5,
        min_swing_distance: int = 5,
        max_swing_distance: int = 50,
    ) -> None:
        self.swing_lookback = swing_lookback
        self.min_swing_distance = min_swing_distance
        self.max_swing_distance = max_swing_distance

    def find_swing_highs(self, series: pd.Series) -> pd.Series:
        """スイング高値を検出

        Args:
            series: 価格系列

        Returns:
            pd.Series: スイング高値（Trueの位置がスイング高値）
        """
        n = self.swing_lookback
        swing_highs = pd.Series(False, index=series.index)

        for i in range(n, len(series) - n):
            window = series.iloc[i - n : i + n + 1]
            if series.iloc[i] == window.max():
                swing_highs.iloc[i] = True

        return swing_highs

    def find_swing_lows(self, series: pd.Series) -> pd.Series:
        """スイング安値を検出

        Args:
            series: 価格系列

        Returns:
            pd.Series: スイング安値（Trueの位置がスイング安値）
        """
        n = self.swing_lookback
        swing_lows = pd.Series(False, index=series.index)

        for i in range(n, len(series) - n):
            window = series.iloc[i - n : i + n + 1]
            if series.iloc[i] == window.min():
                swing_lows.iloc[i] = True

        return swing_lows

    def detect_rsi_divergence(
        self, close: pd.Series, rsi: pd.Series
    ) -> pd.Series:
        """RSIダイバージェンスを検出

        Args:
            close: 終値系列
            rsi: RSI系列

        Returns:
            pd.Series: ダイバージェンスタイプ
        """
        result = pd.Series(DivergenceType.NONE, index=close.index)

        price_highs = self.find_swing_highs(close)
        price_lows = self.find_swing_lows(close)

        # スイングポイントのインデックスを取得
        high_indices = close.index[price_highs].tolist()
        low_indices = close.index[price_lows].tolist()

        # 弱気ダイバージェンス検出（高値ベース）
        for i in range(1, len(high_indices)):
            curr_idx = high_indices[i]
            prev_idx = high_indices[i - 1]

            # インデックスの差を計算
            curr_pos = close.index.get_loc(curr_idx)
            prev_pos = close.index.get_loc(prev_idx)
            distance = curr_pos - prev_pos

            if not (self.min_swing_distance <= distance <= self.max_swing_distance):
                continue

            curr_price = close.loc[curr_idx]
            prev_price = close.loc[prev_idx]
            curr_rsi = rsi.loc[curr_idx]
            prev_rsi = rsi.loc[prev_idx]

            if pd.isna(curr_rsi) or pd.isna(prev_rsi):
                continue

            # 通常弱気ダイバージェンス: 価格HH + RSI LH
            if curr_price > prev_price and curr_rsi < prev_rsi:
                result.loc[curr_idx] = DivergenceType.REGULAR_BEARISH

            # 隠れ弱気ダイバージェンス: 価格LH + RSI HH
            elif curr_price < prev_price and curr_rsi > prev_rsi:
                result.loc[curr_idx] = DivergenceType.HIDDEN_BEARISH

        # 強気ダイバージェンス検出（安値ベース）
        for i in range(1, len(low_indices)):
            curr_idx = low_indices[i]
            prev_idx = low_indices[i - 1]

            curr_pos = close.index.get_loc(curr_idx)
            prev_pos = close.index.get_loc(prev_idx)
            distance = curr_pos - prev_pos

            if not (self.min_swing_distance <= distance <= self.max_swing_distance):
                continue

            curr_price = close.loc[curr_idx]
            prev_price = close.loc[prev_idx]
            curr_rsi = rsi.loc[curr_idx]
            prev_rsi = rsi.loc[prev_idx]

            if pd.isna(curr_rsi) or pd.isna(prev_rsi):
                continue

            # 通常強気ダイバージェンス: 価格LL + RSI HL
            if curr_price < prev_price and curr_rsi > prev_rsi:
                result.loc[curr_idx] = DivergenceType.REGULAR_BULLISH

            # 隠れ強気ダイバージェンス: 価格HL + RSI LL
            elif curr_price > prev_price and curr_rsi < prev_rsi:
                result.loc[curr_idx] = DivergenceType.HIDDEN_BULLISH

        return result

    def calculate_divergence_signal(
        self, close: pd.Series, rsi: pd.Series
    ) -> pd.DataFrame:
        """ダイバージェンスシグナルを計算

        Args:
            close: 終値系列
            rsi: RSI系列

        Returns:
            pd.DataFrame: ダイバージェンス関連の特徴量
        """
        divergence = self.detect_rsi_divergence(close, rsi)

        result = pd.DataFrame(index=close.index)
        result["divergence_type"] = divergence
        result["is_bullish_div"] = divergence.isin([
            DivergenceType.REGULAR_BULLISH,
            DivergenceType.HIDDEN_BULLISH,
        ])
        result["is_bearish_div"] = divergence.isin([
            DivergenceType.REGULAR_BEARISH,
            DivergenceType.HIDDEN_BEARISH,
        ])
        result["is_regular_div"] = divergence.isin([
            DivergenceType.REGULAR_BULLISH,
            DivergenceType.REGULAR_BEARISH,
        ])

        return result
