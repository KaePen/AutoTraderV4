"""指標強度計算器"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from autotrader.calculator.scoring import (
    normalize_atr_by_price,
    score_rsi_continuous,
)

from .config import StrengthConfig


@dataclass(frozen=True)
class IndicatorStrength:
    """個別指標の強度

    各指標は-1.0（売り）から1.0（買い）の範囲で正規化される。

    Attributes:
        rsi: RSI強度
        macd: MACD強度
        trend: SMA整列度によるトレンド強度
        divergence: ダイバージェンス強度
        bollinger: ボリンジャーバンド強度
        stochastic: ストキャスティクス強度
        atr_normalized: 正規化ATR（ボラティリティ）
    """

    rsi: float = 0.0
    macd: float = 0.0
    trend: float = 0.0
    divergence: float = 0.0
    bollinger: float = 0.0
    stochastic: float = 0.0
    atr_normalized: float = 0.0

    @property
    def total_strength(self) -> float:
        """合計強度（-1.0 ~ 1.0に正規化）

        Returns:
            float: 正規化された合計強度
        """
        total = (
            self.rsi + self.macd + self.trend +
            self.divergence + self.bollinger + self.stochastic
        )
        # 6指標の合計を正規化
        return max(-1.0, min(1.0, total / 6.0))

    @property
    def buy_strength(self) -> float:
        """買い強度（0.0 ~ 1.0）

        Returns:
            float: 買い強度
        """
        return max(0.0, self.total_strength)

    @property
    def sell_strength(self) -> float:
        """売り強度（0.0 ~ 1.0）

        Returns:
            float: 売り強度
        """
        return max(0.0, -self.total_strength)


class IndicatorStrengthCalculator:
    """指標強度計算器（全時間足共通）

    OHLCVデータと事前計算された指標値から、正規化された強度を計算する。
    """

    def __init__(self, config: StrengthConfig | None = None):
        """初期化

        Args:
            config: 強度計算設定
        """
        self.config = config or StrengthConfig()

    def calculate(self, row: pd.Series) -> IndicatorStrength:
        """OHLCVデータから正規化された強度を計算

        Args:
            row: 指標値を含むデータ行

        Returns:
            IndicatorStrength: 正規化された指標強度
        """
        return IndicatorStrength(
            rsi=self._calculate_rsi_strength(row),
            macd=self._calculate_macd_strength(row),
            trend=self._calculate_trend_strength(row),
            divergence=self._calculate_divergence_strength(row),
            bollinger=self._calculate_bb_strength(row),
            stochastic=self._calculate_stoch_strength(row),
            atr_normalized=self._calculate_atr_normalized(row),
        )

    def _calculate_rsi_strength(self, row: pd.Series) -> float:
        """RSIから強度を計算（calculator/scoring.pyに委譲）

        Args:
            row: データ行

        Returns:
            float: RSI強度（負=売り、正=買い）
        """
        rsi = row.get("rsi_14")
        if rsi is None or pd.isna(rsi):
            return 0.0

        return score_rsi_continuous(
            rsi,
            oversold=self.config.rsi_oversold,
            overbought=self.config.rsi_overbought,
        )

    def _calculate_macd_strength(self, row: pd.Series) -> float:
        """MACDから強度を計算（-1.0 ~ 1.0）

        Args:
            row: データ行

        Returns:
            float: MACD強度
        """
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        macd_histogram = row.get("macd_histogram")

        if any(pd.isna(v) for v in [macd, macd_signal, macd_histogram]
               if v is not None):
            return 0.0

        if macd is None or macd_signal is None:
            return 0.0

        # ヒストグラムの正規化
        norm_factor = self.config.macd_norm_factor
        if macd_histogram is not None:
            hist_normalized = min(abs(macd_histogram) / norm_factor, 1.0)
        else:
            hist_normalized = 0.5

        # MACDがシグナルより上 → 買い、下 → 売り
        if macd > macd_signal:
            strength = hist_normalized
            # プラス圏ならさらに強い
            if macd > 0:
                strength *= 1.2
        else:
            strength = -hist_normalized
            # マイナス圏ならさらに強い
            if macd < 0:
                strength *= 1.2

        return max(-1.0, min(1.0, strength))

    def _calculate_trend_strength(self, row: pd.Series) -> float:
        """トレンド指標から強度を計算（-1.0 ~ 1.0）

        SMA整列度とADXを考慮。

        Args:
            row: データ行

        Returns:
            float: トレンド強度
        """
        ma_alignment = row.get("ma_alignment")
        adx = row.get("adx")
        close = row.get("close")
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")

        # ma_alignmentがある場合はそれを使用
        if ma_alignment is not None and not pd.isna(ma_alignment):
            base_strength = ma_alignment
        # SMAから計算
        elif (close is not None and sma_20 is not None and
              sma_50 is not None and not any(
                  pd.isna(v) for v in [close, sma_20, sma_50])):
            if close > sma_20 > sma_50:
                base_strength = 1.0
            elif close < sma_20 < sma_50:
                base_strength = -1.0
            elif close > sma_20:
                base_strength = 0.5
            elif close < sma_20:
                base_strength = -0.5
            else:
                base_strength = 0.0
        else:
            return 0.0

        # ADXでトレンド強度を調整
        adx_factor = 1.0
        if adx is not None and not pd.isna(adx):
            threshold = self.config.adx_threshold
            if adx >= threshold:
                adx_factor = 1.0 + (adx - threshold) / 50
            else:
                adx_factor = adx / threshold

        adx_factor = min(adx_factor, 1.5)
        return max(-1.0, min(1.0, base_strength * adx_factor))

    def _calculate_divergence_strength(self, row: pd.Series) -> float:
        """ダイバージェンスから強度を計算（-1.0 ~ 1.0）

        Args:
            row: データ行

        Returns:
            float: ダイバージェンス強度
        """
        bullish_div = row.get("is_bullish_div", False)
        bearish_div = row.get("is_bearish_div", False)

        if bullish_div and not bearish_div:
            return 0.8  # 強い買いシグナル
        elif bearish_div and not bullish_div:
            return -0.8  # 強い売りシグナル
        elif bullish_div and bearish_div:
            return 0.0  # 両方ある場合は中立
        return 0.0

    def _calculate_bb_strength(self, row: pd.Series) -> float:
        """ボリンジャーバンドから強度を計算（-1.0 ~ 1.0）

        Args:
            row: データ行

        Returns:
            float: ボリンジャーバンド強度
        """
        bb_percent_b = row.get("bb_percent_b")

        if bb_percent_b is None or pd.isna(bb_percent_b):
            return 0.0

        lower = self.config.bb_lower_threshold
        upper = self.config.bb_upper_threshold

        # %B < 0: 下限割れ（買い）、%B > 1: 上限割れ（売り）
        if bb_percent_b < 0:
            return min(abs(bb_percent_b), 1.0)
        elif bb_percent_b > 1:
            return -min(bb_percent_b - 1, 1.0)
        elif bb_percent_b < lower:
            return (lower - bb_percent_b) / lower * 0.5
        elif bb_percent_b > upper:
            return -(bb_percent_b - upper) / (1 - upper) * 0.5
        return 0.0

    def _calculate_stoch_strength(self, row: pd.Series) -> float:
        """ストキャスティクスから強度を計算（-1.0 ~ 1.0）

        Args:
            row: データ行

        Returns:
            float: ストキャスティクス強度
        """
        stoch_k = row.get("stoch_k")
        stoch_d = row.get("stoch_d")

        if stoch_k is None or pd.isna(stoch_k):
            return 0.0

        oversold = self.config.stoch_oversold
        overbought = self.config.stoch_overbought

        if stoch_k <= oversold:
            strength = (oversold - stoch_k) / oversold
        elif stoch_k >= overbought:
            strength = -(stoch_k - overbought) / (100 - overbought)
        elif stoch_k < 50:
            strength = 0.2
        else:
            strength = -0.2

        # K/Dクロスで補正
        if stoch_d is not None and not pd.isna(stoch_d):
            if stoch_k > stoch_d:
                strength *= 1.2 if strength > 0 else 0.8
            else:
                strength *= 0.8 if strength > 0 else 1.2

        return max(-1.0, min(1.0, strength))

    def _calculate_atr_normalized(self, row: pd.Series) -> float:
        """ATRを正規化（calculator/scoring.pyに委譲）

        Args:
            row: データ行

        Returns:
            float: 正規化ATR（0.0 ~ 1.0）
        """
        atr = row.get("atr_14")
        close = row.get("close")

        if atr is None or close is None:
            return 0.0

        if pd.isna(atr) or pd.isna(close) or close == 0:
            return 0.0

        return normalize_atr_by_price(atr, close)
