"""トレンドフォロー特化型シグナル生成器

勝率向上のためにトレンドフォローに特化。
押し目買い・戻り売りを重視し、逆張りを最小化。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import numpy as np
import pandas as pd

from autotrader.core.enums import SignalType

if TYPE_CHECKING:
    from autotrader.core.entities import Candle, Signal
    from autotrader.core.enums import Timeframe


@dataclass
class TrendFollowConfig:
    """トレンドフォロー設定

    Attributes:
        adx_threshold: ADX閾値（トレンド強度）
        adx_strong: 強いトレンドのADX閾値
        rsi_buy_zone: 買いゾーン（上限）
        rsi_sell_zone: 売りゾーン（下限）
        rsi_extreme_buy: 極端な売られすぎ
        rsi_extreme_sell: 極端な買われすぎ
        sl_atr_mult: SL ATR倍率
        tp_atr_mult: TP ATR倍率
        tp_atr_mult_strong: 強トレンド時TP倍率
        cooldown_minutes: クールダウン時間
        require_macd_confirm: MACD確認必須
        require_stoch_confirm: ストキャスティクス確認
        pullback_sma_tolerance: 押し目判定SMA許容幅（%）
    """

    adx_threshold: float = 20.0
    adx_strong: float = 30.0
    rsi_buy_zone: float = 55.0
    rsi_sell_zone: float = 45.0
    rsi_extreme_buy: float = 30.0
    rsi_extreme_sell: float = 70.0
    sl_atr_mult: float = 1.8
    tp_atr_mult: float = 2.7
    tp_atr_mult_strong: float = 3.5
    cooldown_minutes: int = 120
    require_macd_confirm: bool = True
    require_stoch_confirm: bool = False
    pullback_sma_tolerance: float = 0.5

    @classmethod
    def balanced(cls) -> "TrendFollowConfig":
        """バランス型設定"""
        return cls(
            adx_threshold=18.0,
            adx_strong=28.0,
            rsi_buy_zone=50.0,
            rsi_sell_zone=50.0,
            rsi_extreme_buy=32.0,
            rsi_extreme_sell=68.0,
            sl_atr_mult=1.8,
            tp_atr_mult=2.7,
            tp_atr_mult_strong=3.5,
            cooldown_minutes=90,
            require_macd_confirm=True,
            require_stoch_confirm=False,
        )

    @classmethod
    def aggressive(cls) -> "TrendFollowConfig":
        """積極型設定"""
        return cls(
            adx_threshold=15.0,
            adx_strong=25.0,
            rsi_buy_zone=55.0,
            rsi_sell_zone=45.0,
            rsi_extreme_buy=35.0,
            rsi_extreme_sell=65.0,
            sl_atr_mult=2.0,
            tp_atr_mult=3.0,
            tp_atr_mult_strong=4.0,
            cooldown_minutes=60,
            require_macd_confirm=False,
            require_stoch_confirm=False,
        )


class TrendFollowGenerator:
    """トレンドフォロー特化型シグナル生成器

    主要戦略:
    1. 明確な上昇/下降トレンド時のみエントリー
    2. 押し目買い（SMA付近での反発）
    3. 戻り売り（SMA付近での反落）
    4. トレンド方向のMTF確認
    5. モメンタム指標での確認

    Args:
        config: 設定
    """

    def __init__(self, config: TrendFollowConfig | None = None) -> None:
        self.config = config or TrendFollowConfig.balanced()
        self._last_signal_time: datetime | None = None
        self._higher_tf_data: dict[str, pd.DataFrame] = {}
        self._signal_count = 0

    def set_higher_tf_data(self, timeframe: str, df: pd.DataFrame) -> None:
        """上位足データを設定"""
        self._higher_tf_data[timeframe] = df

    def reset(self) -> None:
        """状態をリセット"""
        self._last_signal_time = None
        self._signal_count = 0

    def _get_higher_tf_trend(
        self,
        tf_name: str,
        current_time: datetime,
    ) -> tuple[str, float]:
        """上位足トレンドを取得

        Returns:
            tuple[str, float]: (方向, ADX値)
        """
        if tf_name not in self._higher_tf_data:
            return "neutral", 0.0

        df = self._higher_tf_data[tf_name]
        mask = df["time"] <= current_time
        if not mask.any():
            return "neutral", 0.0

        row = df[mask].iloc[-1]

        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        close = row.get("close")
        adx = row.get("adx", 0.0)

        if pd.isna(sma_20) or pd.isna(sma_50) or pd.isna(close):
            return "neutral", 0.0

        if pd.isna(adx):
            adx = 0.0

        # トレンド判定
        if close > sma_20 and sma_20 > sma_50:
            return "up", adx
        elif close < sma_20 and sma_20 < sma_50:
            return "down", adx

        return "neutral", adx

    def _is_trend_established(
        self,
        row: pd.Series,
        candle: "Candle",
    ) -> tuple[bool, str, float]:
        """トレンドが確立しているか判定

        Returns:
            tuple[bool, str, float]: (確立フラグ, 方向, ADX)
        """
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        adx = row.get("adx", 0.0)

        if pd.isna(sma_20) or pd.isna(sma_50):
            return False, "neutral", 0.0

        if pd.isna(adx):
            adx = 0.0

        # ADX閾値チェック
        if adx < self.config.adx_threshold:
            return False, "neutral", adx

        # トレンド方向判定
        if candle.close > sma_20 > sma_50:
            return True, "up", adx
        elif candle.close < sma_20 < sma_50:
            return True, "down", adx

        return False, "neutral", adx

    def _is_pullback_entry(
        self,
        row: pd.Series,
        candle: "Candle",
        trend_dir: str,
    ) -> tuple[bool, str]:
        """押し目/戻りエントリーかどうか判定

        Returns:
            tuple[bool, str]: (押し目/戻りフラグ, 理由)
        """
        sma_20 = row.get("sma_20")
        if pd.isna(sma_20):
            return False, ""

        tolerance = sma_20 * (self.config.pullback_sma_tolerance / 100)

        if trend_dir == "up":
            # 上昇トレンド中、SMA20付近まで下落してきた
            if candle.low <= sma_20 + tolerance:
                # かつ、終値がSMA20を上回っている（反発）
                if candle.close > sma_20:
                    return True, "押し目買い（SMA20反発）"
        elif trend_dir == "down":
            # 下降トレンド中、SMA20付近まで上昇してきた
            if candle.high >= sma_20 - tolerance:
                # かつ、終値がSMA20を下回っている（反落）
                if candle.close < sma_20:
                    return True, "戻り売り（SMA20反落）"

        return False, ""

    def _check_momentum_confirm(
        self,
        row: pd.Series,
        trend_dir: str,
    ) -> tuple[bool, str]:
        """モメンタム確認

        Returns:
            tuple[bool, str]: (確認フラグ, 理由)
        """
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        macd_hist = row.get("macd_histogram")
        rsi = row.get("rsi_14")

        reasons = []
        confirmed = False

        # MACD確認
        if self.config.require_macd_confirm:
            if pd.isna(macd) or pd.isna(macd_signal):
                return False, "MACD計算不可"

            if trend_dir == "up":
                if macd > macd_signal:
                    confirmed = True
                    reasons.append("MACD上昇")
                else:
                    return False, "MACDが下降中"
            elif trend_dir == "down":
                if macd < macd_signal:
                    confirmed = True
                    reasons.append("MACD下降")
                else:
                    return False, "MACDが上昇中"
        else:
            confirmed = True

        # RSI確認（トレンド方向と整合性）
        if not pd.isna(rsi):
            if trend_dir == "up":
                if rsi < self.config.rsi_buy_zone:
                    reasons.append(f"RSI適正域({rsi:.1f})")
                elif rsi > 70:
                    # 買われすぎでは新規買いを控える
                    return False, f"RSI過買い({rsi:.1f})"
            elif trend_dir == "down":
                if rsi > self.config.rsi_sell_zone:
                    reasons.append(f"RSI適正域({rsi:.1f})")
                elif rsi < 30:
                    # 売られすぎでは新規売りを控える
                    return False, f"RSI過売り({rsi:.1f})"

        return confirmed, ", ".join(reasons)

    def _check_mtf_alignment(
        self,
        candle: "Candle",
        trend_dir: str,
    ) -> tuple[bool, str, int]:
        """MTFアライメントチェック

        Returns:
            tuple[bool, str, int]: (一致フラグ, 理由, 一致数)
        """
        aligned_count = 0
        total_count = 0
        reasons = []

        for tf_name in ["H4", "D1"]:
            htf_dir, htf_adx = self._get_higher_tf_trend(tf_name, candle.time)
            if htf_dir != "neutral":
                total_count += 1
                if htf_dir == trend_dir:
                    aligned_count += 1
                    reasons.append(f"{tf_name}一致")

        if total_count == 0:
            return True, "上位足データなし", 0

        # 全一致のみ許可
        if aligned_count == total_count:
            return True, f"MTF全一致({aligned_count}/{total_count})", aligned_count

        return False, f"MTF不一致({aligned_count}/{total_count})", aligned_count

    def _is_cooldown_active(self, current_time: datetime) -> bool:
        """クールダウン中か確認"""
        if self._last_signal_time is None:
            return False

        cooldown_delta = timedelta(minutes=self.config.cooldown_minutes)
        return current_time < self._last_signal_time + cooldown_delta

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
            Signal | None: シグナル
        """
        from autotrader.core.entities import Signal

        # クールダウンチェック
        if self._is_cooldown_active(candle.time):
            return None

        # トレンド確立チェック
        trend_ok, trend_dir, adx = self._is_trend_established(row, candle)
        if not trend_ok:
            return None

        # MTFアライメントチェック
        mtf_ok, mtf_reason, mtf_count = self._check_mtf_alignment(
            candle, trend_dir
        )
        if not mtf_ok:
            return None

        # 押し目/戻りチェック
        pullback_ok, pullback_reason = self._is_pullback_entry(
            row, candle, trend_dir
        )

        # モメンタム確認
        momentum_ok, momentum_reason = self._check_momentum_confirm(
            row, trend_dir
        )
        if not momentum_ok:
            return None

        # 押し目/戻りでない場合は、極端なRSI値のみ許可
        rsi = row.get("rsi_14")
        if not pullback_ok:
            if pd.isna(rsi):
                return None
            if trend_dir == "up" and rsi > self.config.rsi_extreme_buy:
                return None
            if trend_dir == "down" and rsi < self.config.rsi_extreme_sell:
                return None

        # シグナル方向決定
        if trend_dir == "up":
            signal_type = SignalType.BUY
        else:
            signal_type = SignalType.SELL

        # SL/TP計算
        atr = row.get("atr_14", 0.5)
        if pd.isna(atr) or atr <= 0:
            atr = 0.5

        # 強いトレンドならTPを拡大
        is_strong_trend = adx >= self.config.adx_strong
        tp_mult = (
            self.config.tp_atr_mult_strong
            if is_strong_trend
            else self.config.tp_atr_mult
        )

        if signal_type == SignalType.BUY:
            stop_loss = candle.close - atr * self.config.sl_atr_mult
            take_profit = candle.close + atr * tp_mult
        else:
            stop_loss = candle.close + atr * self.config.sl_atr_mult
            take_profit = candle.close - atr * tp_mult

        # 確度計算
        confidence = 0.5
        if pullback_ok:
            confidence += 0.15
        if mtf_count >= 2:
            confidence += 0.15
        if is_strong_trend:
            confidence += 0.1

        confidence = min(confidence, 1.0)

        # 理由まとめ
        reasons = [f"トレンド{trend_dir}(ADX:{adx:.1f})"]
        if pullback_ok:
            reasons.append(pullback_reason)
        if mtf_reason:
            reasons.append(mtf_reason)
        if momentum_reason:
            reasons.append(momentum_reason)
        if is_strong_trend:
            reasons.append("強トレンド")

        # クールダウン更新
        self._last_signal_time = candle.time
        self._signal_count += 1

        return Signal(
            signal_id=str(uuid4()),
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            confidence=confidence,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=", ".join(reasons),
            created_at=candle.time,
        )

    @property
    def signal_count(self) -> int:
        """生成されたシグナル数"""
        return self._signal_count


@dataclass
class HybridSignalGenerator:
    """ハイブリッドシグナル生成器

    トレンドフォローをベースに、
    高確率シグナル時のみ追加エントリーを許可。

    Attributes:
        trend_generator: トレンドフォロー生成器
        enable_divergence: ダイバージェンス許可
        enable_extreme_rsi: 極端RSI許可
    """

    trend_generator: TrendFollowGenerator = field(
        default_factory=lambda: TrendFollowGenerator(TrendFollowConfig.balanced())
    )
    enable_divergence: bool = True
    enable_extreme_rsi: bool = True

    def set_higher_tf_data(self, timeframe: str, df: pd.DataFrame) -> None:
        """上位足データを設定"""
        self.trend_generator.set_higher_tf_data(timeframe, df)

    def reset(self) -> None:
        """状態をリセット"""
        self.trend_generator.reset()

    def generate(
        self,
        row: pd.Series,
        candle: "Candle",
        symbol: str,
        timeframe: "Timeframe",
    ) -> "Signal | None":
        """シグナルを生成

        優先順位:
        1. トレンドフォローシグナル
        2. ダイバージェンスシグナル（MTF一致時）
        3. 極端RSIシグナル（MTF一致時）
        """
        from autotrader.core.entities import Signal

        # 1. トレンドフォローシグナル
        signal = self.trend_generator.generate(row, candle, symbol, timeframe)
        if signal is not None:
            return signal

        # 追加シグナルのクールダウンチェック
        if self.trend_generator._is_cooldown_active(candle.time):
            return None

        # MTFトレンド確認
        h4_dir, h4_adx = self.trend_generator._get_higher_tf_trend(
            "H4", candle.time
        )
        d1_dir, d1_adx = self.trend_generator._get_higher_tf_trend(
            "D1", candle.time
        )

        # MTF一致確認
        mtf_aligned = h4_dir == d1_dir and h4_dir != "neutral"
        if not mtf_aligned:
            return None

        atr = row.get("atr_14", 0.5)
        if pd.isna(atr) or atr <= 0:
            atr = 0.5

        config = self.trend_generator.config

        # 2. ダイバージェンスシグナル
        if self.enable_divergence:
            is_bullish_div = row.get("is_bullish_div", False)
            is_bearish_div = row.get("is_bearish_div", False)

            if is_bullish_div and h4_dir == "up":
                signal_type = SignalType.BUY
                stop_loss = candle.close - atr * config.sl_atr_mult
                take_profit = candle.close + atr * config.tp_atr_mult
                self.trend_generator._last_signal_time = candle.time
                self.trend_generator._signal_count += 1
                return Signal(
                    signal_id=str(uuid4()),
                    symbol=symbol,
                    timeframe=timeframe,
                    signal_type=signal_type,
                    confidence=0.65,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reasoning="強気ダイバージェンス, MTF上昇一致",
                    created_at=candle.time,
                )

            if is_bearish_div and h4_dir == "down":
                signal_type = SignalType.SELL
                stop_loss = candle.close + atr * config.sl_atr_mult
                take_profit = candle.close - atr * config.tp_atr_mult
                self.trend_generator._last_signal_time = candle.time
                self.trend_generator._signal_count += 1
                return Signal(
                    signal_id=str(uuid4()),
                    symbol=symbol,
                    timeframe=timeframe,
                    signal_type=signal_type,
                    confidence=0.65,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reasoning="弱気ダイバージェンス, MTF下降一致",
                    created_at=candle.time,
                )

        # 3. 極端RSIシグナル（強いトレンド方向のみ）
        if self.enable_extreme_rsi:
            rsi = row.get("rsi_14")
            if not pd.isna(rsi):
                if rsi < 25 and h4_dir == "up" and h4_adx > 25:
                    signal_type = SignalType.BUY
                    stop_loss = candle.close - atr * config.sl_atr_mult
                    take_profit = candle.close + atr * config.tp_atr_mult
                    self.trend_generator._last_signal_time = candle.time
                    self.trend_generator._signal_count += 1
                    return Signal(
                        signal_id=str(uuid4()),
                        symbol=symbol,
                        timeframe=timeframe,
                        signal_type=signal_type,
                        confidence=0.60,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        reasoning=f"RSI極低({rsi:.1f}), MTF上昇一致",
                        created_at=candle.time,
                    )

                if rsi > 75 and h4_dir == "down" and h4_adx > 25:
                    signal_type = SignalType.SELL
                    stop_loss = candle.close + atr * config.sl_atr_mult
                    take_profit = candle.close - atr * config.tp_atr_mult
                    self.trend_generator._last_signal_time = candle.time
                    self.trend_generator._signal_count += 1
                    return Signal(
                        signal_id=str(uuid4()),
                        symbol=symbol,
                        timeframe=timeframe,
                        signal_type=signal_type,
                        confidence=0.60,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        reasoning=f"RSI極高({rsi:.1f}), MTF下降一致",
                        created_at=candle.time,
                    )

        return None
