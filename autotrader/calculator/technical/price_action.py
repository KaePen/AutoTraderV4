"""価格アクション分析

キャンドルパターン検出とトレンド構造分析。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class CandlePattern(Enum):
    """キャンドルパターン種別"""

    NONE = "none"
    PIN_BAR_BULLISH = "pin_bar_bullish"
    PIN_BAR_BEARISH = "pin_bar_bearish"
    ENGULFING_BULLISH = "engulfing_bullish"
    ENGULFING_BEARISH = "engulfing_bearish"
    DOJI = "doji"
    MORNING_STAR = "morning_star"
    EVENING_STAR = "evening_star"
    THREE_WHITE_SOLDIERS = "three_white_soldiers"
    THREE_BLACK_CROWS = "three_black_crows"
    HAMMER = "hammer"
    SHOOTING_STAR = "shooting_star"
    INSIDE_BAR = "inside_bar"


@dataclass(frozen=True)
class PriceActionResult:
    """価格アクション分析結果

    Attributes:
        pattern: 検出されたパターン
        bullish_score: 買いスコア（0-1）
        bearish_score: 売りスコア（0-1）
        at_support: サポート付近フラグ
        at_resistance: レジスタンス付近フラグ
        trend_with_pullback: トレンド中の押し目/戻りフラグ
    """

    pattern: CandlePattern
    bullish_score: float
    bearish_score: float
    at_support: bool
    at_resistance: bool
    trend_with_pullback: bool


class PriceActionAnalyzer:
    """価格アクション分析クラス

    キャンドルパターンとサポート/レジスタンスレベルを分析。

    Args:
        body_ratio_threshold: 実体比率閾値（ピンバー判定用）
        wick_ratio_threshold: ヒゲ比率閾値（ピンバー判定用）
        sr_lookback: S/R計算のルックバック期間
        sr_tolerance: S/R付近判定の許容幅（ATR倍率）
    """

    def __init__(
        self,
        body_ratio_threshold: float = 0.3,
        wick_ratio_threshold: float = 2.0,
        sr_lookback: int = 20,
        sr_tolerance: float = 0.5,
    ) -> None:
        self.body_ratio_threshold = body_ratio_threshold
        self.wick_ratio_threshold = wick_ratio_threshold
        self.sr_lookback = sr_lookback
        self.sr_tolerance = sr_tolerance

    def calculate_candle_metrics(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
    ) -> dict[str, float]:
        """キャンドルメトリクスを計算

        Args:
            open_: 始値
            high: 高値
            low: 安値
            close: 終値

        Returns:
            dict: メトリクス辞書
        """
        total_range = high - low
        if total_range == 0:
            return {
                "body_size": 0.0,
                "upper_wick": 0.0,
                "lower_wick": 0.0,
                "body_ratio": 0.0,
                "upper_wick_ratio": 0.0,
                "lower_wick_ratio": 0.0,
                "is_bullish": True,
            }

        body_size = abs(close - open_)
        is_bullish = close >= open_

        if is_bullish:
            upper_wick = high - close
            lower_wick = open_ - low
        else:
            upper_wick = high - open_
            lower_wick = close - low

        body_ratio = body_size / total_range
        upper_wick_ratio = upper_wick / body_size if body_size > 0 else 0
        lower_wick_ratio = lower_wick / body_size if body_size > 0 else 0

        return {
            "body_size": body_size,
            "upper_wick": upper_wick,
            "lower_wick": lower_wick,
            "body_ratio": body_ratio,
            "upper_wick_ratio": upper_wick_ratio,
            "lower_wick_ratio": lower_wick_ratio,
            "is_bullish": is_bullish,
        }

    def detect_pin_bar(
        self,
        open_: float,
        high: float,
        low: float,
        close: float,
    ) -> CandlePattern:
        """ピンバー（ハンマー/シューティングスター）を検出

        Args:
            open_: 始値
            high: 高値
            low: 安値
            close: 終値

        Returns:
            CandlePattern: 検出されたパターン
        """
        metrics = self.calculate_candle_metrics(open_, high, low, close)

        # 小さい実体
        if metrics["body_ratio"] > self.body_ratio_threshold:
            return CandlePattern.NONE

        # 長い下ヒゲ（買いシグナル）
        if metrics["lower_wick_ratio"] >= self.wick_ratio_threshold:
            if metrics["upper_wick_ratio"] < 1.0:
                return CandlePattern.PIN_BAR_BULLISH

        # 長い上ヒゲ（売りシグナル）
        if metrics["upper_wick_ratio"] >= self.wick_ratio_threshold:
            if metrics["lower_wick_ratio"] < 1.0:
                return CandlePattern.PIN_BAR_BEARISH

        return CandlePattern.NONE

    def detect_engulfing(
        self,
        prev_open: float,
        prev_close: float,
        curr_open: float,
        curr_high: float,
        curr_low: float,
        curr_close: float,
    ) -> CandlePattern:
        """包み足（エンゲルフィング）を検出

        Args:
            prev_open: 前足始値
            prev_close: 前足終値
            curr_open: 現在足始値
            curr_high: 現在足高値
            curr_low: 現在足安値
            curr_close: 現在足終値

        Returns:
            CandlePattern: 検出されたパターン
        """
        prev_bullish = prev_close >= prev_open
        curr_bullish = curr_close >= curr_open

        prev_body_high = max(prev_open, prev_close)
        prev_body_low = min(prev_open, prev_close)
        curr_body_high = max(curr_open, curr_close)
        curr_body_low = min(curr_open, curr_close)

        # 買い包み足
        if not prev_bullish and curr_bullish:
            if curr_body_low <= prev_body_low and curr_body_high >= prev_body_high:
                return CandlePattern.ENGULFING_BULLISH

        # 売り包み足
        if prev_bullish and not curr_bullish:
            if curr_body_low <= prev_body_low and curr_body_high >= prev_body_high:
                return CandlePattern.ENGULFING_BEARISH

        return CandlePattern.NONE

    def detect_inside_bar(
        self,
        prev_high: float,
        prev_low: float,
        curr_high: float,
        curr_low: float,
    ) -> bool:
        """インサイドバーを検出

        Args:
            prev_high: 前足高値
            prev_low: 前足安値
            curr_high: 現在足高値
            curr_low: 現在足安値

        Returns:
            bool: インサイドバーの場合True
        """
        return curr_high <= prev_high and curr_low >= prev_low

    def calculate_support_resistance(
        self,
        highs: pd.Series,
        lows: pd.Series,
    ) -> tuple[float, float]:
        """サポート/レジスタンスレベルを計算

        Args:
            highs: 高値系列
            lows: 安値系列

        Returns:
            tuple[float, float]: (サポート, レジスタンス)
        """
        recent_highs = highs.tail(self.sr_lookback)
        recent_lows = lows.tail(self.sr_lookback)

        resistance = recent_highs.max()
        support = recent_lows.min()

        return support, resistance

    def is_at_support(
        self,
        price: float,
        support: float,
        atr: float,
    ) -> bool:
        """サポートレベル付近かどうか判定

        Args:
            price: 現在価格
            support: サポートレベル
            atr: ATR値

        Returns:
            bool: サポート付近の場合True
        """
        tolerance = atr * self.sr_tolerance
        return abs(price - support) <= tolerance

    def is_at_resistance(
        self,
        price: float,
        resistance: float,
        atr: float,
    ) -> bool:
        """レジスタンスレベル付近かどうか判定

        Args:
            price: 現在価格
            resistance: レジスタンスレベル
            atr: ATR値

        Returns:
            bool: レジスタンス付近の場合True
        """
        tolerance = atr * self.sr_tolerance
        return abs(price - resistance) <= tolerance

    def detect_pullback(
        self,
        closes: pd.Series,
        sma_short: pd.Series,
        sma_long: pd.Series,
    ) -> tuple[bool, str]:
        """押し目/戻りを検出

        Args:
            closes: 終値系列
            sma_short: 短期SMA
            sma_long: 長期SMA

        Returns:
            tuple[bool, str]: (押し目/戻りフラグ, 方向)
        """
        if len(closes) < 3:
            return False, "neutral"

        current_close = closes.iloc[-1]
        prev_close = closes.iloc[-2]
        current_sma_short = sma_short.iloc[-1]
        current_sma_long = sma_long.iloc[-1]

        # 上昇トレンド中
        if current_sma_short > current_sma_long:
            # 押し目: 短期SMAに近づいてきた
            if current_close <= current_sma_short * 1.005:
                if prev_close > current_sma_short:
                    return True, "bullish_pullback"

        # 下降トレンド中
        if current_sma_short < current_sma_long:
            # 戻り: 短期SMAに近づいてきた
            if current_close >= current_sma_short * 0.995:
                if prev_close < current_sma_short:
                    return True, "bearish_pullback"

        return False, "neutral"

    def analyze(
        self,
        df: pd.DataFrame,
        atr: float | None = None,
    ) -> pd.DataFrame:
        """価格アクション分析を実行

        Args:
            df: OHLCVデータフレーム
            atr: ATR値（オプション）

        Returns:
            pd.DataFrame: 分析結果を含むデータフレーム
        """
        result = pd.DataFrame(index=df.index)

        # キャンドルパターン検出
        patterns = []
        bullish_scores = []
        bearish_scores = []

        for i in range(len(df)):
            row = df.iloc[i]
            pattern = CandlePattern.NONE
            bullish_score = 0.0
            bearish_score = 0.0

            # ピンバー検出
            pin = self.detect_pin_bar(
                row["open"], row["high"], row["low"], row["close"]
            )
            if pin != CandlePattern.NONE:
                pattern = pin
                if pin == CandlePattern.PIN_BAR_BULLISH:
                    bullish_score = 0.3
                else:
                    bearish_score = 0.3

            # 包み足検出（2足目以降）
            if i > 0:
                prev = df.iloc[i - 1]
                engulf = self.detect_engulfing(
                    prev["open"],
                    prev["close"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                )
                if engulf != CandlePattern.NONE:
                    pattern = engulf
                    if engulf == CandlePattern.ENGULFING_BULLISH:
                        bullish_score = 0.4
                    else:
                        bearish_score = 0.4

                # インサイドバー検出
                if self.detect_inside_bar(
                    prev["high"], prev["low"], row["high"], row["low"]
                ):
                    if pattern == CandlePattern.NONE:
                        pattern = CandlePattern.INSIDE_BAR
                    # インサイドバーは中立的
                    bullish_score = max(bullish_score, 0.1)
                    bearish_score = max(bearish_score, 0.1)

            patterns.append(pattern.value)
            bullish_scores.append(bullish_score)
            bearish_scores.append(bearish_score)

        result["candle_pattern"] = patterns
        result["pa_bullish_score"] = bullish_scores
        result["pa_bearish_score"] = bearish_scores

        # サポート/レジスタンス
        result["support"] = df["low"].rolling(self.sr_lookback).min()
        result["resistance"] = df["high"].rolling(self.sr_lookback).max()

        # S/R付近判定
        if atr is not None:
            result["at_support"] = (
                abs(df["close"] - result["support"]) <= atr * self.sr_tolerance
            )
            result["at_resistance"] = (
                abs(df["close"] - result["resistance"]) <= atr * self.sr_tolerance
            )
        else:
            result["at_support"] = False
            result["at_resistance"] = False

        return result

    def get_pattern_score(
        self,
        pattern: CandlePattern,
        at_support: bool,
        at_resistance: bool,
        trend_direction: str,
    ) -> tuple[float, float]:
        """パターンスコアを取得（S/R位置考慮）

        Args:
            pattern: キャンドルパターン
            at_support: サポート付近フラグ
            at_resistance: レジスタンス付近フラグ
            trend_direction: トレンド方向（"up", "down", "neutral"）

        Returns:
            tuple[float, float]: (買いスコア, 売りスコア)
        """
        bullish = 0.0
        bearish = 0.0

        # パターン基本スコア
        pattern_scores = {
            CandlePattern.PIN_BAR_BULLISH: (0.25, 0.0),
            CandlePattern.PIN_BAR_BEARISH: (0.0, 0.25),
            CandlePattern.ENGULFING_BULLISH: (0.35, 0.0),
            CandlePattern.ENGULFING_BEARISH: (0.0, 0.35),
            CandlePattern.HAMMER: (0.30, 0.0),
            CandlePattern.SHOOTING_STAR: (0.0, 0.30),
            CandlePattern.MORNING_STAR: (0.40, 0.0),
            CandlePattern.EVENING_STAR: (0.0, 0.40),
            CandlePattern.THREE_WHITE_SOLDIERS: (0.35, 0.0),
            CandlePattern.THREE_BLACK_CROWS: (0.0, 0.35),
            CandlePattern.DOJI: (0.0, 0.0),
            CandlePattern.INSIDE_BAR: (0.0, 0.0),
            CandlePattern.NONE: (0.0, 0.0),
        }

        bullish, bearish = pattern_scores.get(pattern, (0.0, 0.0))

        # S/R位置ボーナス
        if at_support and bullish > 0:
            bullish *= 1.5
        if at_resistance and bearish > 0:
            bearish *= 1.5

        # トレンド方向ボーナス
        if trend_direction == "up" and bullish > 0:
            bullish *= 1.2
        if trend_direction == "down" and bearish > 0:
            bearish *= 1.2

        # 逆張り警戒（トレンドと逆のシグナルは減衰）
        if trend_direction == "up" and bearish > 0:
            bearish *= 0.6
        if trend_direction == "down" and bullish > 0:
            bullish *= 0.6

        return min(bullish, 1.0), min(bearish, 1.0)
