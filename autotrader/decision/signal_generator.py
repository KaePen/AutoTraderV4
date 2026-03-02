"""シグナル生成

テクニカル指標からトレードシグナルを生成。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

import pandas as pd

logger = logging.getLogger(__name__)

from autotrader.core.enums import SignalType

if TYPE_CHECKING:
    from autotrader.adapters.ollama import OllamaClient
    from autotrader.config.settings import StrategyConfig
    from autotrader.core.entities import Candle, Signal
    from autotrader.core.enums import Timeframe


class DirectionPolicy(Enum):
    """方向決定ポリシー"""

    MAX_SIDE = "max_side"  # buy/sell強度の大きい方
    TREND_FOLLOW = "trend_follow"  # 上位足トレンド方向のみ
    CONSENSUS = "consensus"  # 全指標一致時のみ


@dataclass(frozen=True)
class SignalStrength:
    """シグナル強度

    Attributes:
        buy_strength: 買い強度（0-1）
        sell_strength: 売り強度（0-1）
        net_strength: 純強度（正=買い、負=売り）
    """

    buy_strength: float
    sell_strength: float

    @property
    def net_strength(self) -> float:
        """純強度を取得"""
        return self.buy_strength - self.sell_strength

    @property
    def dominant_direction(self) -> SignalType:
        """優勢な方向を取得"""
        if self.buy_strength > self.sell_strength:
            return SignalType.BUY
        elif self.sell_strength > self.buy_strength:
            return SignalType.SELL
        return SignalType.HOLD


@dataclass(frozen=True)
class SignalResult:
    """シグナル生成結果

    Attributes:
        signal_type: シグナル種別
        strength: シグナル強度
        reasoning: 判断理由
    """

    signal_type: SignalType
    strength: SignalStrength
    reasoning: str


class SignalGenerator:
    """シグナル生成クラス

    テクニカル指標から買い/売り強度を計算し、シグナルを生成。

    Args:
        direction_policy: 方向決定ポリシー
        min_strength_threshold: 最小強度閾値
        neutral_zone: 中立帯幅（RSI等で使用）
    """

    # 指標の重み
    INDICATOR_WEIGHTS = {
        "rsi": 0.15,
        "macd": 0.20,
        "stoch": 0.10,
        "trend": 0.25,
        "bb": 0.15,
        "mtf": 0.15,
    }

    def __init__(
        self,
        direction_policy: DirectionPolicy = DirectionPolicy.MAX_SIDE,
        min_strength_threshold: float = 0.3,
        neutral_zone: float = 0.1,
    ) -> None:
        self.direction_policy = direction_policy
        self.min_strength_threshold = min_strength_threshold
        self.neutral_zone = neutral_zone

    def _calculate_rsi_strength(
        self, rsi: float | None
    ) -> tuple[float, float]:
        """RSIから買い/売り強度を計算

        Args:
            rsi: RSI値（0-100）

        Returns:
            tuple[float, float]: (買い強度, 売り強度)
        """
        if rsi is None or pd.isna(rsi):
            return 0.0, 0.0

        # 30以下: 買い強度、70以上: 売り強度
        # 中立帯（45-55）では両方に小さな強度
        if rsi <= 30:
            buy = 1.0 - (rsi / 30)
            sell = 0.0
        elif rsi >= 70:
            buy = 0.0
            sell = (rsi - 70) / 30
        elif 45 <= rsi <= 55:
            # 中立帯
            buy = 0.1
            sell = 0.1
        elif rsi < 45:
            buy = (45 - rsi) / 15 * 0.5
            sell = 0.0
        else:  # 55 < rsi < 70
            buy = 0.0
            sell = (rsi - 55) / 15 * 0.5

        return buy, sell

    def _calculate_macd_strength(
        self,
        macd: float | None,
        macd_signal: float | None,
        macd_histogram: float | None,
    ) -> tuple[float, float]:
        """MACDから買い/売り強度を計算

        Args:
            macd: MACD値
            macd_signal: MACDシグナル
            macd_histogram: MACDヒストグラム

        Returns:
            tuple[float, float]: (買い強度, 売り強度)
        """
        if any(pd.isna(v) for v in [macd, macd_signal, macd_histogram]):
            return 0.0, 0.0

        # ヒストグラムの符号と大きさで判断
        # 正規化（±0.5を基準）
        hist_normalized = min(abs(macd_histogram) / 0.5, 1.0)

        if macd_histogram > 0:
            # MACDがシグナル上抜け
            if macd > macd_signal:
                buy = hist_normalized
                sell = 0.0
            else:
                buy = hist_normalized * 0.5
                sell = 0.0
        else:
            # MACDがシグナル下抜け
            if macd < macd_signal:
                buy = 0.0
                sell = hist_normalized
            else:
                buy = 0.0
                sell = hist_normalized * 0.5

        return buy, sell

    def _calculate_stoch_strength(
        self, stoch_k: float | None, stoch_d: float | None
    ) -> tuple[float, float]:
        """ストキャスティクスから買い/売り強度を計算

        Args:
            stoch_k: %K値
            stoch_d: %D値

        Returns:
            tuple[float, float]: (買い強度, 売り強度)
        """
        if stoch_k is None or stoch_d is None:
            return 0.0, 0.0

        if pd.isna(stoch_k) or pd.isna(stoch_d):
            return 0.0, 0.0

        # 20以下: 売られすぎ（買い）、80以上: 買われすぎ（売り）
        if stoch_k <= 20:
            buy = (20 - stoch_k) / 20
            sell = 0.0
        elif stoch_k >= 80:
            buy = 0.0
            sell = (stoch_k - 80) / 20
        else:
            buy = 0.1 if stoch_k < 50 else 0.0
            sell = 0.1 if stoch_k > 50 else 0.0

        # K/Dクロスで補正
        if stoch_k > stoch_d:
            buy *= 1.2
        else:
            sell *= 1.2

        return min(buy, 1.0), min(sell, 1.0)

    def _calculate_trend_strength(
        self,
        ma_alignment: float | None,
        adx: float | None,
        trend_direction: str | None,
    ) -> tuple[float, float]:
        """トレンド指標から買い/売り強度を計算

        Args:
            ma_alignment: MA整列度（-1から1）
            adx: ADX値
            trend_direction: トレンド方向

        Returns:
            tuple[float, float]: (買い強度, 売り強度)
        """
        if ma_alignment is None or pd.isna(ma_alignment):
            return 0.0, 0.0

        # ADXでトレンド強度を調整
        adx_factor = 1.0
        if adx is not None and not pd.isna(adx):
            if adx >= 25:
                adx_factor = 1.0 + (adx - 25) / 50
            else:
                adx_factor = adx / 25

        adx_factor = min(adx_factor, 1.5)

        if ma_alignment > 0:
            buy = ma_alignment * adx_factor
            sell = 0.0
        else:
            buy = 0.0
            sell = abs(ma_alignment) * adx_factor

        return min(buy, 1.0), min(sell, 1.0)

    def _calculate_bb_strength(
        self, bb_percent_b: float | None, bb_width: float | None
    ) -> tuple[float, float]:
        """ボリンジャーバンドから買い/売り強度を計算

        Args:
            bb_percent_b: %B値
            bb_width: バンド幅

        Returns:
            tuple[float, float]: (買い強度, 売り強度)
        """
        if bb_percent_b is None or pd.isna(bb_percent_b):
            return 0.0, 0.0

        # %B < 0: 下限割れ（買い）、%B > 1: 上限割れ（売り）
        if bb_percent_b < 0:
            buy = min(abs(bb_percent_b), 1.0)
            sell = 0.0
        elif bb_percent_b > 1:
            buy = 0.0
            sell = min(bb_percent_b - 1, 1.0)
        elif bb_percent_b < 0.2:
            buy = (0.2 - bb_percent_b) / 0.2 * 0.5
            sell = 0.0
        elif bb_percent_b > 0.8:
            buy = 0.0
            sell = (bb_percent_b - 0.8) / 0.2 * 0.5
        else:
            buy = 0.0
            sell = 0.0

        return buy, sell

    def _calculate_mtf_strength(
        self,
        mtf_alignment: str | None,
        higher_tf_bias: float | None,
    ) -> tuple[float, float]:
        """MTF指標から買い/売り強度を計算

        Args:
            mtf_alignment: MTF整合状態
            higher_tf_bias: 上位足バイアス

        Returns:
            tuple[float, float]: (買い強度, 売り強度)
        """
        if higher_tf_bias is None or pd.isna(higher_tf_bias):
            return 0.0, 0.0

        # 整合度で補正
        alignment_factor = 1.0
        if mtf_alignment == "aligned_up" or mtf_alignment == "aligned_down":
            alignment_factor = 1.2
        elif mtf_alignment == "conflicting":
            alignment_factor = 0.5

        if higher_tf_bias > 0:
            buy = higher_tf_bias * alignment_factor
            sell = 0.0
        else:
            buy = 0.0
            sell = abs(higher_tf_bias) * alignment_factor

        return min(buy, 1.0), min(sell, 1.0)

    def calculate_strength(self, indicators: pd.Series) -> SignalStrength:
        """全指標から総合強度を計算

        Args:
            indicators: 指標値（Series）

        Returns:
            SignalStrength: 総合シグナル強度
        """
        strengths: dict[str, tuple[float, float]] = {}

        # RSI
        rsi = indicators.get(f"rsi_14")
        if rsi is None:
            rsi = indicators.get("rsi")
        strengths["rsi"] = self._calculate_rsi_strength(rsi)

        # MACD
        macd = indicators.get("macd")
        macd_signal = indicators.get("macd_signal")
        macd_hist = indicators.get("macd_histogram")
        strengths["macd"] = self._calculate_macd_strength(
            macd, macd_signal, macd_hist
        )

        # Stochastics
        stoch_k = indicators.get("stoch_k")
        stoch_d = indicators.get("stoch_d")
        strengths["stoch"] = self._calculate_stoch_strength(stoch_k, stoch_d)

        # Trend
        ma_alignment = indicators.get("ma_alignment")
        adx = indicators.get("adx_14")
        if adx is None:
            adx = indicators.get("adx")
        trend_dir = indicators.get("trend_direction")
        strengths["trend"] = self._calculate_trend_strength(
            ma_alignment, adx, trend_dir
        )

        # Bollinger Bands
        bb_pct = indicators.get("bb_percent_b")
        bb_width = indicators.get("bb_width")
        strengths["bb"] = self._calculate_bb_strength(bb_pct, bb_width)

        # MTF
        mtf_align = indicators.get("mtf_alignment")
        higher_bias = indicators.get("higher_tf_bias")
        strengths["mtf"] = self._calculate_mtf_strength(mtf_align, higher_bias)

        # 重み付け集計
        total_buy = 0.0
        total_sell = 0.0
        total_weight = 0.0

        for name, (buy, sell) in strengths.items():
            weight = self.INDICATOR_WEIGHTS.get(name, 0.1)
            total_buy += buy * weight
            total_sell += sell * weight
            total_weight += weight

        if total_weight > 0:
            total_buy /= total_weight
            total_sell /= total_weight

        return SignalStrength(
            buy_strength=min(total_buy, 1.0),
            sell_strength=min(total_sell, 1.0),
        )

    def generate(self, indicators: pd.Series) -> SignalResult:
        """シグナルを生成

        Args:
            indicators: 指標値

        Returns:
            SignalResult: シグナル生成結果
        """
        strength = self.calculate_strength(indicators)

        # 方向決定
        if self.direction_policy == DirectionPolicy.MAX_SIDE:
            signal_type = strength.dominant_direction
        elif self.direction_policy == DirectionPolicy.TREND_FOLLOW:
            trend_dir = indicators.get("trend_direction")
            if trend_dir in ("strong_up", "up"):
                signal_type = SignalType.BUY if strength.buy_strength > 0.2 else SignalType.HOLD
            elif trend_dir in ("strong_down", "down"):
                signal_type = SignalType.SELL if strength.sell_strength > 0.2 else SignalType.HOLD
            else:
                signal_type = SignalType.HOLD
        else:  # CONSENSUS
            if strength.buy_strength > 0.6 and strength.sell_strength < 0.2:
                signal_type = SignalType.BUY
            elif strength.sell_strength > 0.6 and strength.buy_strength < 0.2:
                signal_type = SignalType.SELL
            else:
                signal_type = SignalType.HOLD

        # 閾値チェック
        max_strength = max(strength.buy_strength, strength.sell_strength)
        if max_strength < self.min_strength_threshold:
            signal_type = SignalType.HOLD

        # 理由生成
        reasons = []
        if signal_type == SignalType.BUY:
            reasons.append(f"買い強度: {strength.buy_strength:.2f}")
        elif signal_type == SignalType.SELL:
            reasons.append(f"売り強度: {strength.sell_strength:.2f}")
        else:
            reasons.append("シグナル強度不足またはコンフリクト")

        return SignalResult(
            signal_type=signal_type,
            strength=strength,
            reasoning="; ".join(reasons),
        )


@dataclass
class OptimizedSignalGenerator:
    """最適化シグナル生成クラス

    Walk-forward検証で最適化されたパラメータを使用。
    MTF（マルチタイムフレーム）確認を含む。

    Attributes:
        config: 戦略設定
        higher_tf_data: 上位足データ（H4など）
    """

    min_signals: int = 3
    signal_margin: int = 1
    adx_threshold: float = 15.0
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 3.0
    use_mtf: bool = True
    mtf_bonus: int = 2
    mtf_required: bool = False
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    cooldown_bars: int = 0
    higher_tf_data: pd.DataFrame | None = field(default=None, repr=False)
    _last_signal_bar: int = field(default=-999, repr=False)
    _current_bar: int = field(default=0, repr=False)

    @classmethod
    def from_config(
        cls, config: "StrategyConfig"
    ) -> "OptimizedSignalGenerator":
        """設定から生成

        Args:
            config: 戦略設定

        Returns:
            OptimizedSignalGenerator: インスタンス
        """
        return cls(
            min_signals=config.min_signals,
            signal_margin=config.signal_margin,
            adx_threshold=config.adx_threshold,
            sl_atr_mult=config.sl_atr_mult,
            tp_atr_mult=config.tp_atr_mult,
            use_mtf=config.use_mtf,
            mtf_bonus=config.mtf_bonus,
            mtf_required=config.mtf_required,
            rsi_oversold=config.rsi_oversold,
            rsi_overbought=config.rsi_overbought,
            cooldown_bars=config.cooldown_bars,
        )

    def set_higher_tf_data(self, df: pd.DataFrame) -> None:
        """上位足データを設定

        Args:
            df: 上位足OHLCVデータ（SMA計算済み）
        """
        self.higher_tf_data = df

    def get_higher_tf_trend(self, current_time: pd.Timestamp) -> str | None:
        """上位足トレンド方向を取得

        Args:
            current_time: 現在時刻

        Returns:
            str | None: "up", "down", または None
        """
        if self.higher_tf_data is None or not self.use_mtf:
            return None

        # 現在時刻以前の最新H4足を取得
        mask = self.higher_tf_data["time"] <= current_time
        if not mask.any():
            return None

        h4_row = self.higher_tf_data[mask].iloc[-1]
        sma_20 = h4_row.get("sma_20")
        sma_50 = h4_row.get("sma_50")
        close = h4_row.get("close")

        if pd.isna(sma_20) or pd.isna(sma_50):
            return None

        # トレンド判定（緩和版）
        if close > sma_20 and sma_20 > sma_50:
            return "up"
        elif close > sma_20 and close > sma_50:
            return "up"
        elif close < sma_20 and sma_20 < sma_50:
            return "down"
        elif close < sma_20 and close < sma_50:
            return "down"

        return None

    def reset(self) -> None:
        """状態をリセット"""
        object.__setattr__(self, "_last_signal_bar", -999)
        object.__setattr__(self, "_current_bar", 0)

    def generate(
        self,
        row: pd.Series,
        candle: "Candle",
        symbol: str,
        timeframe: "Timeframe",
    ) -> "Signal | None":
        """シグナルを生成

        Args:
            row: 指標付きデータ行
            candle: 現在のキャンドル
            symbol: シンボル
            timeframe: 時間足

        Returns:
            Signal | None: 生成されたシグナル
        """
        from autotrader.core.entities import Signal

        # バーカウンター更新
        object.__setattr__(self, "_current_bar", self._current_bar + 1)

        # クールダウンチェック
        if self.cooldown_bars > 0:
            bars_since_signal = self._current_bar - self._last_signal_bar
            if bars_since_signal < self.cooldown_bars:
                return None

        rsi = row.get("rsi_14")
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        atr = row.get("atr_14", 0.5)
        adx = row.get("adx", 25.0)

        # NaN チェック
        if any(pd.isna(v) for v in [rsi, macd, sma_20, sma_50]):
            return None

        # ADX閾値チェック
        if adx is not None and not pd.isna(adx) and adx < self.adx_threshold:
            return None

        # シグナルスコア計算
        buy_signals = 0
        sell_signals = 0
        reasons = []

        # RSI条件
        if rsi < self.rsi_oversold - 5:
            buy_signals += 2
            reasons.append(f"RSI過売り({rsi:.1f})")
        elif rsi < self.rsi_oversold:
            buy_signals += 1
            reasons.append(f"RSI低め({rsi:.1f})")
        elif rsi > self.rsi_overbought + 5:
            sell_signals += 2
            reasons.append(f"RSI過買い({rsi:.1f})")
        elif rsi > self.rsi_overbought:
            sell_signals += 1
            reasons.append(f"RSI高め({rsi:.1f})")

        # MACD条件
        if macd > macd_signal:
            buy_signals += 1
            if macd > 0:
                buy_signals += 1
                reasons.append("MACD上昇+プラス圏")
            else:
                reasons.append("MACDゴールデンクロス")
        elif macd < macd_signal:
            sell_signals += 1
            if macd < 0:
                sell_signals += 1
                reasons.append("MACD下落+マイナス圏")
            else:
                reasons.append("MACDデッドクロス")

        # トレンド条件（SMA）
        if candle.close > sma_20 > sma_50:
            buy_signals += 1
            reasons.append("上昇トレンド")
        elif candle.close < sma_20 < sma_50:
            sell_signals += 1
            reasons.append("下降トレンド")

        # ADXでトレンド強度確認
        if adx and not pd.isna(adx) and adx > 25:
            if buy_signals > sell_signals:
                buy_signals += 1
            elif sell_signals > buy_signals:
                sell_signals += 1

        # ダイバージェンス確認（強いシグナル）
        is_bullish_div = row.get("is_bullish_div", False)
        is_bearish_div = row.get("is_bearish_div", False)

        if is_bullish_div:
            buy_signals += 3
            reasons.append("強気ダイバージェンス")
        if is_bearish_div:
            sell_signals += 3
            reasons.append("弱気ダイバージェンス")

        # MTF確認
        higher_trend = self.get_higher_tf_trend(candle.time)

        # MTF必須モードの場合、トレンド方向が一致しなければスキップ
        if self.mtf_required:
            if higher_trend is None:
                return None
            if buy_signals > sell_signals and higher_trend != "up":
                return None
            if sell_signals > buy_signals and higher_trend != "down":
                return None

        # MTFボーナス（必須でない場合のみ）
        if not self.mtf_required and self.use_mtf:
            if higher_trend == "up" and buy_signals > sell_signals:
                buy_signals += self.mtf_bonus
                reasons.append("H4上昇トレンド確認")
            elif higher_trend == "down" and sell_signals > buy_signals:
                sell_signals += self.mtf_bonus
                reasons.append("H4下降トレンド確認")

        # SL/TP倍率（固定）
        sl_mult = self.sl_atr_mult
        tp_mult = self.tp_atr_mult

        # シグナル判定
        if (
            buy_signals >= self.min_signals
            and buy_signals > sell_signals + self.signal_margin
        ):
            # クールダウン更新
            object.__setattr__(self, "_last_signal_bar", self._current_bar)

            confidence = min(buy_signals / 8, 1.0)
            stop_loss = candle.close - atr * sl_mult
            take_profit = candle.close + atr * tp_mult

            return Signal(
                signal_id=str(uuid4()),
                symbol=symbol,
                timeframe=timeframe,
                signal_type=SignalType.BUY,
                confidence=confidence,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reasoning=", ".join(reasons),
                created_at=candle.time,
            )

        elif (
            sell_signals >= self.min_signals
            and sell_signals > buy_signals + self.signal_margin
        ):
            # クールダウン更新
            object.__setattr__(self, "_last_signal_bar", self._current_bar)

            confidence = min(sell_signals / 8, 1.0)
            stop_loss = candle.close + atr * sl_mult
            take_profit = candle.close - atr * tp_mult

            return Signal(
                signal_id=str(uuid4()),
                symbol=symbol,
                timeframe=timeframe,
                signal_type=SignalType.SELL,
                confidence=confidence,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reasoning=", ".join(reasons),
                created_at=candle.time,
            )

        return None


@dataclass(frozen=True)
class MTFLayerResult:
    """MTF層の分析結果

    Attributes:
        timeframe: 時間足
        direction: トレンド方向（up/down/neutral）
        strength: トレンド強度（0-1）
        ma_alignment: MA整列度（-1から1）
    """

    timeframe: "Timeframe"
    direction: str
    strength: float
    ma_alignment: float


@dataclass(frozen=True)
class MTFAlignmentResult:
    """3層MTFアライメント判定結果

    Attributes:
        is_aligned: 全層一致フラグ
        dominant_direction: 優勢方向（up/down/neutral）
        layer_results: 各層の分析結果
        alignment_score: アライメントスコア（0-1）
        bonus_score: 全層一致時のボーナススコア
    """

    is_aligned: bool
    dominant_direction: str
    layer_results: tuple[MTFLayerResult, ...]
    alignment_score: float
    bonus_score: float


class MTFAlignmentChecker:
    """3層マルチタイムフレームアライメントチェッカー

    構成:
    - M1トレード: M1 + M5 + H1
    - M5トレード: M5 + H1 + H4
    - H1トレード: H1 + H4 + D1

    全層一致時にボーナススコアを付与。

    Args:
        base_timeframe: エントリー時間足
        alignment_bonus: 全層一致時のボーナス（デフォルト0.15）
        require_all_aligned: 全層一致を必須とするか
    """

    # 時間足別のMTF構成
    MTF_LAYERS: dict[str, tuple[str, ...]] = {
        "M1": ("M1", "M5", "H1"),
        "M5": ("M5", "H1", "H4"),
        "M15": ("M15", "H1", "H4"),
        "H1": ("H1", "H4", "D1"),
        "H4": ("H4", "D1", "W1"),
    }

    def __init__(
        self,
        base_timeframe: "Timeframe",
        alignment_bonus: float = 0.15,
        require_all_aligned: bool = False,
    ) -> None:
        from autotrader.core.enums import Timeframe

        self.base_timeframe = base_timeframe
        self.alignment_bonus = alignment_bonus
        self.require_all_aligned = require_all_aligned

        # MTF構成を取得
        tf_key = base_timeframe.value
        if tf_key in self.MTF_LAYERS:
            layer_names = self.MTF_LAYERS[tf_key]
            self.layers = tuple(Timeframe(name) for name in layer_names)
        else:
            # 未定義の場合は単層
            self.layers = (base_timeframe,)

        # 各層のデータ
        self._layer_data: dict["Timeframe", pd.DataFrame] = {}

    def set_layer_data(
        self, timeframe: "Timeframe", data: pd.DataFrame
    ) -> None:
        """時間足別データを設定

        Args:
            timeframe: 時間足
            data: OHLCVデータ（指標計算済み）
        """
        self._layer_data[timeframe] = data

    def _analyze_layer(
        self,
        timeframe: "Timeframe",
        current_time: pd.Timestamp,
    ) -> MTFLayerResult | None:
        """単一層を分析

        Args:
            timeframe: 時間足
            current_time: 現在時刻

        Returns:
            MTFLayerResult | None: 分析結果
        """
        if timeframe not in self._layer_data:
            return None

        df = self._layer_data[timeframe]

        # 時刻カラムの確認
        if "time" in df.columns:
            mask = df["time"] <= current_time
        elif df.index.name == "time" or isinstance(df.index, pd.DatetimeIndex):
            mask = df.index <= current_time
        else:
            return None

        if not mask.any():
            return None

        row = df.loc[mask].iloc[-1]

        # MA整列度を取得
        ma_alignment = row.get("ma_alignment", 0.0)
        if pd.isna(ma_alignment):
            ma_alignment = 0.0

        # ADXでトレンド強度を計算
        adx = row.get("adx_14") or row.get("adx", 25.0)
        if pd.isna(adx):
            adx = 25.0

        # 強度を正規化（ADX 20-40を0-1にマップ）
        strength = max(0.0, min(1.0, (adx - 20) / 20))

        # 方向を判定
        if ma_alignment > 0.2:
            direction = "up"
        elif ma_alignment < -0.2:
            direction = "down"
        else:
            direction = "neutral"

        return MTFLayerResult(
            timeframe=timeframe,
            direction=direction,
            strength=strength,
            ma_alignment=ma_alignment,
        )

    def check_alignment(
        self, current_time: pd.Timestamp
    ) -> MTFAlignmentResult:
        """3層アライメントをチェック

        Args:
            current_time: 現在時刻

        Returns:
            MTFAlignmentResult: アライメント判定結果
        """
        layer_results: list[MTFLayerResult] = []
        directions: list[str] = []

        for tf in self.layers:
            result = self._analyze_layer(tf, current_time)
            if result is not None:
                layer_results.append(result)
                if result.direction != "neutral":
                    directions.append(result.direction)

        if not layer_results:
            return MTFAlignmentResult(
                is_aligned=False,
                dominant_direction="neutral",
                layer_results=(),
                alignment_score=0.0,
                bonus_score=0.0,
            )

        # 優勢方向を判定
        up_count = directions.count("up")
        down_count = directions.count("down")

        if up_count > down_count:
            dominant = "up"
        elif down_count > up_count:
            dominant = "down"
        else:
            dominant = "neutral"

        # アライメントスコアを計算（一致度）
        total = len(layer_results)
        matching = sum(
            1 for r in layer_results if r.direction == dominant
        )
        alignment_score = matching / total if total > 0 else 0.0

        # 全層一致判定
        is_aligned = (
            dominant != "neutral"
            and all(r.direction == dominant for r in layer_results)
        )

        # ボーナススコア
        bonus_score = self.alignment_bonus if is_aligned else 0.0

        return MTFAlignmentResult(
            is_aligned=is_aligned,
            dominant_direction=dominant,
            layer_results=tuple(layer_results),
            alignment_score=alignment_score,
            bonus_score=bonus_score,
        )

    def should_allow_signal(
        self,
        signal_type: SignalType,
        current_time: pd.Timestamp,
    ) -> tuple[bool, str]:
        """シグナルがMTFアライメントに適合するか判定

        Args:
            signal_type: シグナル種別
            current_time: 現在時刻

        Returns:
            tuple[bool, str]: (許可フラグ, 理由)
        """
        result = self.check_alignment(current_time)

        # 全層一致必須モード
        if self.require_all_aligned:
            if not result.is_aligned:
                return False, f"MTF不一致（{result.alignment_score:.0%}）"

        # シグナル方向とトレンド方向の整合性
        if signal_type == SignalType.BUY:
            if result.dominant_direction == "down":
                return False, "MTF逆行（上位足下降中に買い）"
        elif signal_type == SignalType.SELL:
            if result.dominant_direction == "up":
                return False, "MTF逆行（上位足上昇中に売り）"

        # 許可
        if result.is_aligned:
            return True, f"MTF全層一致（{result.dominant_direction}）"
        else:
            return True, f"MTF部分一致（{result.alignment_score:.0%}）"

    def get_bonus_strength(
        self,
        strength: SignalStrength,
        current_time: pd.Timestamp,
    ) -> SignalStrength:
        """MTFボーナスを適用した強度を取得

        Args:
            strength: 元のシグナル強度
            current_time: 現在時刻

        Returns:
            SignalStrength: ボーナス適用後の強度
        """
        result = self.check_alignment(current_time)

        if not result.is_aligned:
            return strength

        # 全層一致時にボーナスを加算
        if result.dominant_direction == "up":
            new_buy = min(1.0, strength.buy_strength + result.bonus_score)
            return SignalStrength(
                buy_strength=new_buy,
                sell_strength=strength.sell_strength,
            )
        elif result.dominant_direction == "down":
            new_sell = min(1.0, strength.sell_strength + result.bonus_score)
            return SignalStrength(
                buy_strength=strength.buy_strength,
                sell_strength=new_sell,
            )

        return strength


@dataclass
class LLMEnhancedSignalGenerator:
    """LLM強化版シグナルジェネレーター

    従来のテクニカルシグナル生成にLLMによるVeto判定と
    信頼度調整を追加。

    処理フロー:
    1. OptimizedSignalGeneratorでシグナル生成
    2. 高信頼度シグナル（confidence > threshold）のみLLM検証
    3. Veto判定でリスク評価
    4. 信頼度調整でTP/SL最適化

    Attributes:
        base_generator: ベースのシグナル生成器
        llm_settings: LLM設定
        ollama_client: Ollamaクライアント
        veto_count: Vetoされたシグナル数
        adjusted_count: 調整されたシグナル数
    """

    min_signals: int = 4
    signal_margin: int = 2
    adx_threshold: float = 20.0
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 3.0
    use_mtf: bool = True
    mtf_bonus: int = 2
    mtf_required: bool = False
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    cooldown_bars: int = 4
    higher_tf_data: pd.DataFrame | None = field(default=None, repr=False)
    _base_generator: "OptimizedSignalGenerator | None" = field(
        default=None, repr=False
    )
    _ollama_client: "OllamaClient | None" = field(default=None, repr=False)
    _llm_enabled: bool = field(default=True, repr=False)
    _veto_threshold: float = field(default=0.6, repr=False)
    _min_confidence_for_llm: float = field(default=0.7, repr=False)
    _veto_count: int = field(default=0, repr=False)
    _adjusted_count: int = field(default=0, repr=False)
    _mtf_data: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_config(
        cls,
        config: "StrategyConfig",
        llm_enabled: bool = True,
        veto_threshold: float = 0.6,
        min_confidence_for_llm: float = 0.7,
    ) -> "LLMEnhancedSignalGenerator":
        """設定から生成

        Args:
            config: 戦略設定
            llm_enabled: LLM検証を有効にするか
            veto_threshold: Veto確信度閾値
            min_confidence_for_llm: LLM検証対象の最低信頼度

        Returns:
            LLMEnhancedSignalGenerator: インスタンス
        """
        instance = cls(
            min_signals=config.min_signals,
            signal_margin=config.signal_margin,
            adx_threshold=config.adx_threshold,
            sl_atr_mult=config.sl_atr_mult,
            tp_atr_mult=config.tp_atr_mult,
            use_mtf=config.use_mtf,
            mtf_bonus=config.mtf_bonus,
            mtf_required=config.mtf_required,
            rsi_oversold=config.rsi_oversold,
            rsi_overbought=config.rsi_overbought,
            cooldown_bars=config.cooldown_bars,
        )
        object.__setattr__(instance, "_llm_enabled", llm_enabled)
        object.__setattr__(instance, "_veto_threshold", veto_threshold)
        object.__setattr__(
            instance, "_min_confidence_for_llm", min_confidence_for_llm
        )
        return instance

    def set_ollama_client(self, client: "OllamaClient") -> None:
        """Ollamaクライアントを設定

        Args:
            client: OllamaClientインスタンス
        """
        object.__setattr__(self, "_ollama_client", client)

    def set_higher_tf_data(self, df: pd.DataFrame) -> None:
        """上位足データを設定

        Args:
            df: 上位足OHLCVデータ
        """
        self.higher_tf_data = df
        if self._base_generator:
            self._base_generator.set_higher_tf_data(df)

    def set_mtf_data(self, timeframe: str, data: dict) -> None:
        """MTFデータを設定（LLMプロンプト用）

        Args:
            timeframe: 時間足
            data: MTF分析データ
        """
        self._mtf_data[timeframe] = data

    def reset(self) -> None:
        """状態をリセット"""
        object.__setattr__(self, "_veto_count", 0)
        object.__setattr__(self, "_adjusted_count", 0)
        if self._base_generator:
            self._base_generator.reset()

    def _get_base_generator(self) -> "OptimizedSignalGenerator":
        """ベースジェネレータを取得（遅延初期化）"""
        if self._base_generator is None:
            generator = OptimizedSignalGenerator(
                min_signals=self.min_signals,
                signal_margin=self.signal_margin,
                adx_threshold=self.adx_threshold,
                sl_atr_mult=self.sl_atr_mult,
                tp_atr_mult=self.tp_atr_mult,
                use_mtf=self.use_mtf,
                mtf_bonus=self.mtf_bonus,
                mtf_required=self.mtf_required,
                rsi_oversold=self.rsi_oversold,
                rsi_overbought=self.rsi_overbought,
                cooldown_bars=self.cooldown_bars,
            )
            if self.higher_tf_data is not None:
                generator.set_higher_tf_data(self.higher_tf_data)
            object.__setattr__(self, "_base_generator", generator)
        return self._base_generator

    def _get_trend_direction(self, row: pd.Series) -> str:
        """トレンド方向を取得

        Args:
            row: 指標付きデータ行

        Returns:
            str: トレンド方向
        """
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        close = row.get("close")

        if pd.isna(sma_20) or pd.isna(sma_50) or pd.isna(close):
            return "neutral"

        if close > sma_20 > sma_50:
            return "up"
        elif close < sma_20 < sma_50:
            return "down"
        return "neutral"

    def _check_veto(
        self,
        signal: "Signal",
        row: pd.Series,
        candle: "Candle",
    ) -> bool:
        """Veto判定を実行

        Args:
            signal: 生成されたシグナル
            row: 指標付きデータ行
            candle: 現在のキャンドル

        Returns:
            bool: Vetoされた場合True
        """
        if not self._llm_enabled or self._ollama_client is None:
            return False

        if signal.confidence < self._min_confidence_for_llm:
            return False

        try:
            result = self._ollama_client.check_veto(
                symbol=signal.symbol,
                timestamp=str(candle.time),
                current_price=float(candle.close),
                direction=signal.signal_type.value,
                confidence=signal.confidence,
                rsi=float(row.get("rsi_14", 50.0)),
                macd=float(row.get("macd", 0.0)),
                adx=float(row.get("adx", 25.0)),
                trend=self._get_trend_direction(row),
                mtf_data=self._mtf_data,
                entry_price=float(candle.close),
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            )

            if result.veto and result.confidence >= self._veto_threshold:
                object.__setattr__(
                    self, "_veto_count", self._veto_count + 1
                )
                logger.warning(
                    f"[LLM] シグナルVeto: {signal.symbol} {signal.signal_type.value} "
                    f"理由: {result.veto_reason or 'N/A'}"
                )
                return True

        except Exception as e:
            # LLMエラー時はフォールバック（Vetoなし）
            logger.warning(f"[LLM] Veto判定エラー、フォールバック: {e}")

        return False

    def _adjust_confidence(
        self,
        signal: "Signal",
        row: pd.Series,
        candle: "Candle",
    ) -> "Signal":
        """信頼度調整を実行

        Args:
            signal: 生成されたシグナル
            row: 指標付きデータ行
            candle: 現在のキャンドル

        Returns:
            Signal: 調整後のシグナル
        """
        if not self._llm_enabled or self._ollama_client is None:
            return signal

        if signal.confidence < self._min_confidence_for_llm:
            return signal

        try:
            atr = float(row.get("atr_14", 0.5))
            atr_20 = row.get("atr_20_mean", atr)
            atr_ratio = atr / atr_20 if atr_20 > 0 else 1.0

            result = self._ollama_client.adjust_confidence(
                symbol=signal.symbol,
                timestamp=str(candle.time),
                current_price=float(candle.close),
                direction=signal.signal_type.value,
                confidence=signal.confidence,
                rsi=float(row.get("rsi_14", 50.0)),
                macd=float(row.get("macd", 0.0)),
                adx=float(row.get("adx", 25.0)),
                mtf_data=self._mtf_data,
                atr=atr,
                atr_ratio=atr_ratio,
                entry_price=float(candle.close),
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            )

            # 信頼度調整
            from autotrader.core.entities import Signal

            adjusted_sl = signal.stop_loss
            adjusted_tp = signal.take_profit

            # TP/SL調整を適用
            if signal.signal_type == SignalType.BUY:
                sl_distance = candle.close - signal.stop_loss
                tp_distance = signal.take_profit - candle.close
                adjusted_sl = candle.close - sl_distance * result.sl_adjustment
                adjusted_tp = candle.close + tp_distance * result.tp_adjustment
            else:
                sl_distance = signal.stop_loss - candle.close
                tp_distance = candle.close - signal.take_profit
                adjusted_sl = candle.close + sl_distance * result.sl_adjustment
                adjusted_tp = candle.close - tp_distance * result.tp_adjustment

            object.__setattr__(
                self, "_adjusted_count", self._adjusted_count + 1
            )

            return Signal(
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                signal_type=signal.signal_type,
                confidence=result.adjusted_confidence,
                stop_loss=adjusted_sl,
                take_profit=adjusted_tp,
                reasoning=f"{signal.reasoning}; LLM調整: {result.adjustment_reason}",
                created_at=signal.created_at,
            )

        except Exception as e:
            # LLMエラー時はフォールバック（調整なし）
            logger.warning(f"[LLM] 信頼度調整エラー、フォールバック: {e}")
            return signal

    def generate(
        self,
        row: pd.Series,
        candle: "Candle",
        symbol: str,
        timeframe: "Timeframe",
    ) -> "Signal | None":
        """シグナルを生成（LLM検証付き）

        Args:
            row: 指標付きデータ行
            candle: 現在のキャンドル
            symbol: シンボル
            timeframe: 時間足

        Returns:
            Signal | None: 生成されたシグナル（Vetoされた場合はNone）
        """
        # ベースシグナル生成
        base_generator = self._get_base_generator()
        signal = base_generator.generate(row, candle, symbol, timeframe)

        if signal is None:
            return None

        # LLMが無効または低信頼度シグナルはそのまま返す
        if not self._llm_enabled or self._ollama_client is None:
            return signal

        if signal.confidence < self._min_confidence_for_llm:
            return signal

        # Veto判定
        if self._check_veto(signal, row, candle):
            return None

        # 信頼度調整
        return self._adjust_confidence(signal, row, candle)

    @property
    def veto_count(self) -> int:
        """Vetoされたシグナル数"""
        return self._veto_count

    @property
    def adjusted_count(self) -> int:
        """調整されたシグナル数"""
        return self._adjusted_count

    def get_stats(self) -> dict:
        """統計情報を取得

        Returns:
            dict: 統計情報
        """
        return {
            "veto_count": self._veto_count,
            "adjusted_count": self._adjusted_count,
            "llm_enabled": self._llm_enabled,
            "veto_threshold": self._veto_threshold,
            "min_confidence_for_llm": self._min_confidence_for_llm,
        }
