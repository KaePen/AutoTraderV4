"""高勝率シグナル生成器

勝率60%を目指す厳選シグナル生成。
エントリー精度を最大化し、確実なシグナルのみ発行。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pandas as pd

from autotrader.core.enums import SignalType

if TYPE_CHECKING:
    from autotrader.core.entities import Candle, Signal
    from autotrader.core.enums import Timeframe


@dataclass
class HighWinRateConfig:
    """高勝率設定

    Attributes:
        min_score: 最小スコア（シグナル発行閾値）
        adx_min: 最小ADX
        rsi_oversold: RSI売られすぎ
        rsi_overbought: RSI買われすぎ
        sl_atr_mult: SL ATR倍率
        tp_atr_mult: TP ATR倍率（勝率重視で短め）
        cooldown_minutes: クールダウン時間
        require_htf_confirm: 上位足確認必須
        require_momentum_align: モメンタム整列必須
    """

    min_score: int = 5
    adx_min: float = 18.0
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    sl_atr_mult: float = 2.2
    tp_atr_mult: float = 2.2  # 1:1 リスクリワード（勝率重視）
    cooldown_minutes: int = 120
    require_htf_confirm: bool = True
    require_momentum_align: bool = True

    @classmethod
    def win_rate_60(cls) -> "HighWinRateConfig":
        """勝率60%目標設定"""
        return cls(
            min_score=6,
            adx_min=20.0,
            rsi_oversold=32.0,
            rsi_overbought=68.0,
            sl_atr_mult=2.5,
            tp_atr_mult=2.0,  # TPを短くして勝率向上
            cooldown_minutes=180,
            require_htf_confirm=True,
            require_momentum_align=True,
        )

    @classmethod
    def balanced(cls) -> "HighWinRateConfig":
        """バランス設定"""
        return cls(
            min_score=5,
            adx_min=18.0,
            rsi_oversold=33.0,
            rsi_overbought=67.0,
            sl_atr_mult=2.3,
            tp_atr_mult=2.3,
            cooldown_minutes=120,
            require_htf_confirm=True,
            require_momentum_align=True,
        )


class HighWinRateGenerator:
    """高勝率シグナル生成器

    勝率を最大化するため:
    1. スコア制でシグナル強度を厳密に計算
    2. 高スコアシグナルのみ発行
    3. TP/SL比を1:1に近づけて勝率向上
    4. MTF確認で逆張りを排除
    5. モメンタム確認で勢いを確認

    Args:
        config: 設定
    """

    def __init__(self, config: HighWinRateConfig | None = None) -> None:
        self.config = config or HighWinRateConfig.balanced()
        self._last_signal_time: datetime | None = None
        self._higher_tf_data: dict[str, pd.DataFrame] = {}

    def set_higher_tf_data(self, timeframe: str, df: pd.DataFrame) -> None:
        """上位足データを設定"""
        self._higher_tf_data[timeframe] = df

    def reset(self) -> None:
        """状態をリセット"""
        self._last_signal_time = None

    def _get_htf_trend(
        self,
        tf_name: str,
        current_time: datetime,
    ) -> tuple[str, float]:
        """上位足トレンドを取得

        Returns:
            tuple[str, float]: (方向, ADX)
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

        if close > sma_20 and sma_20 > sma_50:
            return "up", adx
        elif close < sma_20 and sma_20 < sma_50:
            return "down", adx

        return "neutral", adx

    def _calculate_score(
        self,
        row: pd.Series,
        candle: "Candle",
    ) -> tuple[int, int, list[str]]:
        """買い/売りスコアを計算

        Returns:
            tuple[int, int, list[str]]: (買いスコア, 売りスコア, 理由リスト)
        """
        buy_score = 0
        sell_score = 0
        reasons = []

        rsi = row.get("rsi_14")
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        stoch_k = row.get("stoch_k")
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        adx = row.get("adx", 0.0)

        # ---- RSI (最大+3点) ----
        if not pd.isna(rsi):
            if rsi < self.config.rsi_oversold - 10:
                buy_score += 3
                reasons.append(f"RSI極低({rsi:.1f})")
            elif rsi < self.config.rsi_oversold - 5:
                buy_score += 2
                reasons.append(f"RSI低({rsi:.1f})")
            elif rsi < self.config.rsi_oversold:
                buy_score += 1

            if rsi > self.config.rsi_overbought + 10:
                sell_score += 3
                reasons.append(f"RSI極高({rsi:.1f})")
            elif rsi > self.config.rsi_overbought + 5:
                sell_score += 2
                reasons.append(f"RSI高({rsi:.1f})")
            elif rsi > self.config.rsi_overbought:
                sell_score += 1

        # ---- MACD (最大+3点) ----
        if not pd.isna(macd) and not pd.isna(macd_signal):
            if macd > macd_signal:
                buy_score += 1
                if macd > 0:
                    buy_score += 1
                    reasons.append("MACD+プラス圏")
                # ゴールデンクロス直後
                macd_hist = row.get("macd_histogram", 0)
                macd_prev = row.get("macd_hist_slope", 0)
                if not pd.isna(macd_prev) and macd_prev > 0 and macd_hist > 0:
                    buy_score += 1
            elif macd < macd_signal:
                sell_score += 1
                if macd < 0:
                    sell_score += 1
                    reasons.append("MACD+マイナス圏")
                macd_hist = row.get("macd_histogram", 0)
                macd_prev = row.get("macd_hist_slope", 0)
                if not pd.isna(macd_prev) and macd_prev < 0 and macd_hist < 0:
                    sell_score += 1

        # ---- トレンド (最大+2点) ----
        if not pd.isna(sma_20) and not pd.isna(sma_50):
            if candle.close > sma_20 > sma_50:
                buy_score += 2
                reasons.append("上昇トレンド")
            elif candle.close < sma_20 < sma_50:
                sell_score += 2
                reasons.append("下降トレンド")
            elif candle.close > sma_20:
                buy_score += 1
            elif candle.close < sma_20:
                sell_score += 1

        # ---- ADX (最大+2点) ----
        if not pd.isna(adx):
            if adx > 30:
                if buy_score > sell_score:
                    buy_score += 2
                elif sell_score > buy_score:
                    sell_score += 2
                reasons.append(f"強トレンド(ADX:{adx:.1f})")
            elif adx > 25:
                if buy_score > sell_score:
                    buy_score += 1
                elif sell_score > buy_score:
                    sell_score += 1

        # ---- ストキャスティクス (最大+2点) ----
        if not pd.isna(stoch_k):
            if stoch_k < 20:
                buy_score += 2
                reasons.append(f"Stoch売られすぎ({stoch_k:.1f})")
            elif stoch_k < 30:
                buy_score += 1
            elif stoch_k > 80:
                sell_score += 2
                reasons.append(f"Stoch買われすぎ({stoch_k:.1f})")
            elif stoch_k > 70:
                sell_score += 1

        # ---- ダイバージェンス (最大+3点) ----
        if row.get("is_bullish_div", False):
            buy_score += 3
            reasons.append("強気ダイバージェンス")
        if row.get("is_bearish_div", False):
            sell_score += 3
            reasons.append("弱気ダイバージェンス")

        return buy_score, sell_score, reasons

    def _check_htf_alignment(
        self,
        candle: "Candle",
        direction: str,
    ) -> tuple[bool, int, str]:
        """上位足アライメント確認

        Returns:
            tuple[bool, int, str]: (一致フラグ, 一致数, 理由)
        """
        aligned_count = 0
        total = 0

        for tf_name in ["H4", "D1"]:
            htf_dir, htf_adx = self._get_htf_trend(tf_name, candle.time)
            if htf_dir != "neutral":
                total += 1
                if htf_dir == direction:
                    aligned_count += 1

        if total == 0:
            return True, 0, "上位足データなし"

        if aligned_count == total:
            return True, aligned_count, f"MTF全一致({aligned_count}/{total})"
        elif aligned_count > 0:
            return False, aligned_count, f"MTF部分一致({aligned_count}/{total})"
        else:
            return False, 0, "MTF逆行"

    def _check_momentum_alignment(
        self,
        row: pd.Series,
        direction: str,
    ) -> tuple[bool, str]:
        """モメンタム整列確認

        Returns:
            tuple[bool, str]: (一致フラグ, 理由)
        """
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")

        if pd.isna(macd) or pd.isna(macd_signal):
            return True, "MACD計算不可"

        if direction == "up":
            if macd > macd_signal:
                return True, "モメンタム上昇"
            else:
                return False, "モメンタム逆行(MACD下降中)"
        else:
            if macd < macd_signal:
                return True, "モメンタム下降"
            else:
                return False, "モメンタム逆行(MACD上昇中)"

    def _is_cooldown_active(self, current_time: datetime) -> bool:
        """クールダウン確認"""
        if self._last_signal_time is None:
            return False
        cooldown = timedelta(minutes=self.config.cooldown_minutes)
        return current_time < self._last_signal_time + cooldown

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

        # ADXチェック
        adx = row.get("adx", 0.0)
        if pd.isna(adx):
            adx = 0.0
        if adx < self.config.adx_min:
            return None

        # スコア計算
        buy_score, sell_score, reasons = self._calculate_score(row, candle)

        # 優勢な方向を決定
        if buy_score > sell_score:
            direction = "up"
            score = buy_score
            signal_type = SignalType.BUY
        elif sell_score > buy_score:
            direction = "down"
            score = sell_score
            signal_type = SignalType.SELL
        else:
            return None

        # 最小スコア閾値
        if score < self.config.min_score:
            return None

        # 上位足確認
        if self.config.require_htf_confirm:
            htf_ok, htf_count, htf_reason = self._check_htf_alignment(
                candle, direction
            )
            if not htf_ok:
                return None
            if htf_count > 0:
                reasons.append(htf_reason)

        # モメンタム確認
        if self.config.require_momentum_align:
            mom_ok, mom_reason = self._check_momentum_alignment(row, direction)
            if not mom_ok:
                return None
            reasons.append(mom_reason)

        # SL/TP計算
        atr = row.get("atr_14", 0.5)
        if pd.isna(atr) or atr <= 0:
            atr = 0.5

        if signal_type == SignalType.BUY:
            stop_loss = candle.close - atr * self.config.sl_atr_mult
            take_profit = candle.close + atr * self.config.tp_atr_mult
        else:
            stop_loss = candle.close + atr * self.config.sl_atr_mult
            take_profit = candle.close - atr * self.config.tp_atr_mult

        # 確度（スコアベース）
        confidence = min(score / 12, 1.0)

        # クールダウン更新
        self._last_signal_time = candle.time

        return Signal(
            signal_id=str(uuid4()),
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            confidence=confidence,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=f"スコア{score}: " + ", ".join(reasons),
            created_at=candle.time,
        )
