"""M1スキャルピング戦略

1分足の高頻度トレード向け高勝率戦略。
ノイズフィルタリングと上位足確認を重視。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pandas as pd

from autotrader.core.enums import SignalType, Timeframe

if TYPE_CHECKING:
    from autotrader.core.entities import Candle, Signal


@dataclass
class M1ScalperConfig:
    """M1スキャルパー設定

    Attributes:
        min_score: 最小スコア（厳格なフィルター）
        adx_min: 最小ADX（トレンド強度）
        adx_strong: 強トレンドADX
        rsi_oversold: RSI売られすぎ
        rsi_overbought: RSI買われすぎ
        sl_pips: 固定SL（pips）
        tp_pips: 固定TP（pips）
        cooldown_seconds: クールダウン（秒）
        require_m5_confirm: M5確認必須
        require_m15_confirm: M15確認必須
        max_spread_pips: 最大スプレッド（pips）
        volume_filter: ボリュームフィルター有効
        atr_filter: ATRボラティリティフィルター有効
        atr_min_ratio: 最小ATR比率（平均ATRに対する）
        atr_max_ratio: 最大ATR比率
    """

    min_score: int = 6
    adx_min: float = 22.0
    adx_strong: float = 30.0
    rsi_oversold: float = 28.0
    rsi_overbought: float = 72.0
    sl_pips: float = 5.0
    tp_pips: float = 4.0  # 勝率重視：SL > TP
    cooldown_seconds: int = 60
    require_m5_confirm: bool = True
    require_m15_confirm: bool = True
    max_spread_pips: float = 2.0
    volume_filter: bool = True
    atr_filter: bool = True
    atr_min_ratio: float = 0.5
    atr_max_ratio: float = 2.5

    @classmethod
    def aggressive(cls) -> "M1ScalperConfig":
        """積極的設定（トレード頻度高）"""
        return cls(
            min_score=5,
            adx_min=20.0,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            sl_pips=6.0,
            tp_pips=5.0,
            cooldown_seconds=30,
            require_m5_confirm=True,
            require_m15_confirm=False,
        )

    @classmethod
    def conservative(cls) -> "M1ScalperConfig":
        """保守的設定（高勝率）"""
        return cls(
            min_score=7,
            adx_min=25.0,
            rsi_oversold=25.0,
            rsi_overbought=75.0,
            sl_pips=4.0,
            tp_pips=3.0,
            cooldown_seconds=120,
            require_m5_confirm=True,
            require_m15_confirm=True,
        )

    @classmethod
    def balanced(cls) -> "M1ScalperConfig":
        """バランス設定"""
        return cls(
            min_score=5,
            adx_min=20.0,  # 20に引き上げ
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            sl_pips=4.0,
            tp_pips=5.0,  # TP > SL で収益性向上
            cooldown_seconds=45,
            require_m5_confirm=True,
            require_m15_confirm=True,
            atr_filter=True,
            atr_min_ratio=0.5,
            atr_max_ratio=2.5,
        )

    @classmethod
    def adaptive(cls) -> "M1ScalperConfig":
        """適応設定（厳選エントリー・高収益）"""
        return cls(
            min_score=6,
            adx_min=20.0,
            adx_strong=28.0,
            rsi_oversold=28.0,
            rsi_overbought=72.0,
            sl_pips=4.0,
            tp_pips=6.0,  # TP:SL = 1.5:1
            cooldown_seconds=60,
            require_m5_confirm=True,
            require_m15_confirm=True,
            atr_filter=True,
            atr_min_ratio=0.6,
            atr_max_ratio=2.0,
        )


class M1Scalper:
    """M1スキャルパー

    1分足のマイクロトレンドを捉える高勝率戦略。

    特徴:
    - 厳格なエントリー条件（ノイズ排除）
    - 上位足（M5、M15）との整合性確認
    - 固定pips SL/TP（スプレッド考慮）
    - 短いクールダウン
    """

    def __init__(self, config: M1ScalperConfig | None = None) -> None:
        """初期化

        Args:
            config: スキャルパー設定
        """
        self.config = config or M1ScalperConfig.balanced()
        self._m5_df: pd.DataFrame | None = None
        self._m15_df: pd.DataFrame | None = None
        self._last_signal_time: datetime | None = None
        self._bar_count: int = 0
        self._avg_volume: float = 0.0
        self._avg_atr: float = 0.0

    def set_higher_tf_data(self, timeframe: str, df: pd.DataFrame) -> None:
        """上位足データを設定

        Args:
            timeframe: 時間足（M5, M15）
            df: OHLCVデータ（インジケータ計算済み）
        """
        if timeframe == "M5":
            self._m5_df = df
        elif timeframe == "M15":
            self._m15_df = df

    def reset(self) -> None:
        """状態をリセット"""
        self._last_signal_time = None
        self._bar_count = 0
        self._avg_volume = 0.0
        self._avg_atr = 0.0

    def _is_cooldown_active(self, current_time: datetime) -> bool:
        """クールダウン中か判定

        Args:
            current_time: 現在時刻

        Returns:
            bool: クールダウン中ならTrue
        """
        if self._last_signal_time is None:
            return False

        cooldown = timedelta(seconds=self.config.cooldown_seconds)
        return current_time < self._last_signal_time + cooldown

    def _get_htf_trend(
        self,
        timeframe: str,
        current_time: datetime,
    ) -> tuple[str, float]:
        """上位足トレンドを取得

        Args:
            timeframe: 時間足
            current_time: 現在時刻

        Returns:
            tuple: (方向, ADX)
        """
        df = self._m5_df if timeframe == "M5" else self._m15_df
        if df is None or df.empty:
            return "neutral", 0.0

        # 現在時刻以前の最新行を取得
        past_df = df[df["time"] <= current_time]
        if past_df.empty:
            return "neutral", 0.0

        row = past_df.iloc[-1]

        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        adx = row.get("adx", 0.0)
        close = row.get("close")

        if pd.isna(sma_20) or pd.isna(sma_50) or pd.isna(close):
            return "neutral", adx if not pd.isna(adx) else 0.0

        if close > sma_20 > sma_50:
            return "up", adx
        elif close < sma_20 < sma_50:
            return "down", adx

        return "neutral", adx if not pd.isna(adx) else 0.0

    def _check_htf_alignment(
        self,
        candle: "Candle",
        direction: str,
    ) -> tuple[bool, str]:
        """上位足との整合性チェック

        Args:
            candle: 現在のキャンドル
            direction: シグナル方向

        Returns:
            tuple: (OK, 理由)
        """
        reasons = []
        aligned_count = 0

        # M5チェック
        if self.config.require_m5_confirm:
            m5_dir, m5_adx = self._get_htf_trend("M5", candle.time)
            if m5_dir == direction:
                aligned_count += 1
                reasons.append(f"M5:{direction}")
            elif m5_dir != "neutral":
                return False, "M5逆方向"

        # M15チェック
        if self.config.require_m15_confirm:
            m15_dir, m15_adx = self._get_htf_trend("M15", candle.time)
            if m15_dir == direction:
                aligned_count += 1
                reasons.append(f"M15:{direction}")
            elif m15_dir != "neutral":
                return False, "M15逆方向"

        return True, ", ".join(reasons) if reasons else ""

    def _calculate_score(
        self,
        row: pd.Series,
        candle: "Candle",
    ) -> tuple[int, int, list[str]]:
        """スコア計算

        Args:
            row: 指標付きデータ行
            candle: キャンドル

        Returns:
            tuple: (買いスコア, 売りスコア, 理由リスト)
        """
        buy_score = 0
        sell_score = 0
        reasons = []

        # RSI
        rsi = row.get("rsi_14")
        if not pd.isna(rsi):
            if rsi < self.config.rsi_oversold:
                buy_score += 2
                reasons.append(f"RSI過売り({rsi:.1f})")
            elif rsi < 40:
                buy_score += 1
            elif rsi > self.config.rsi_overbought:
                sell_score += 2
                reasons.append(f"RSI過買い({rsi:.1f})")
            elif rsi > 60:
                sell_score += 1

        # MACD
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        macd_hist = row.get("macd_histogram")
        macd_hist_slope = row.get("macd_hist_slope")

        if not pd.isna(macd) and not pd.isna(macd_signal):
            if macd > macd_signal:
                buy_score += 1
                if not pd.isna(macd_hist_slope) and macd_hist_slope > 0:
                    buy_score += 1
                    reasons.append("MACD上昇加速")
            else:
                sell_score += 1
                if not pd.isna(macd_hist_slope) and macd_hist_slope < 0:
                    sell_score += 1
                    reasons.append("MACD下降加速")

        # トレンド（SMA）
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        if not pd.isna(sma_20) and not pd.isna(sma_50):
            if candle.close > sma_20 > sma_50:
                buy_score += 1
                reasons.append("上昇トレンド")
            elif candle.close < sma_20 < sma_50:
                sell_score += 1
                reasons.append("下降トレンド")

        # ストキャスティクス
        stoch_k = row.get("stoch_k")
        if not pd.isna(stoch_k):
            if stoch_k < 20:
                buy_score += 1
                reasons.append(f"Stoch過売り({stoch_k:.0f})")
            elif stoch_k > 80:
                sell_score += 1
                reasons.append(f"Stoch過買い({stoch_k:.0f})")

        # ADX（トレンド強度ボーナス）
        adx = row.get("adx", 0.0)
        if not pd.isna(adx) and adx >= self.config.adx_strong:
            if buy_score > sell_score:
                buy_score += 1
                reasons.append(f"強トレンド(ADX:{adx:.0f})")
            elif sell_score > buy_score:
                sell_score += 1
                reasons.append(f"強トレンド(ADX:{adx:.0f})")

        # ダイバージェンス
        is_bullish_div = row.get("is_bullish_div", False)
        is_bearish_div = row.get("is_bearish_div", False)
        if is_bullish_div:
            buy_score += 2
            reasons.append("強気ダイバージェンス")
        if is_bearish_div:
            sell_score += 2
            reasons.append("弱気ダイバージェンス")

        return buy_score, sell_score, reasons

    def _check_volume_filter(self, row: pd.Series) -> bool:
        """ボリュームフィルター

        Args:
            row: データ行

        Returns:
            bool: OK ならTrue
        """
        if not self.config.volume_filter:
            return True

        volume = row.get("volume", 0)
        if pd.isna(volume) or volume <= 0:
            return True  # ボリュームデータなしは許可

        # 平均ボリュームを更新
        if self._avg_volume == 0:
            self._avg_volume = volume
        else:
            self._avg_volume = self._avg_volume * 0.99 + volume * 0.01

        # 平均の50%以上ならOK
        return volume >= self._avg_volume * 0.5

    def _check_atr_filter(self, row: pd.Series) -> bool:
        """ATRボラティリティフィルター

        低ボラティリティ（レンジ相場）や過度な高ボラティリティを排除。

        Args:
            row: データ行

        Returns:
            bool: 適正ボラティリティならTrue
        """
        if not self.config.atr_filter:
            return True

        atr = row.get("atr_14", 0.0)
        if pd.isna(atr) or atr <= 0:
            return True  # ATRデータなしは許可

        # 平均ATRを更新（指数移動平均）
        if self._avg_atr == 0:
            self._avg_atr = atr
        else:
            self._avg_atr = self._avg_atr * 0.995 + atr * 0.005

        # ATRが適正範囲内かチェック
        atr_ratio = atr / self._avg_atr if self._avg_atr > 0 else 1.0
        return self.config.atr_min_ratio <= atr_ratio <= self.config.atr_max_ratio

    def generate(
        self,
        row: pd.Series,
        candle: "Candle",
        symbol: str,
        timeframe: "Timeframe",
    ) -> "Signal | None":
        """シグナル生成

        Args:
            row: 指標付きデータ行
            candle: キャンドル
            symbol: シンボル
            timeframe: 時間足

        Returns:
            Signal | None: シグナル
        """
        from autotrader.core.entities import Signal

        self._bar_count += 1

        # クールダウン
        if self._is_cooldown_active(candle.time):
            return None

        # ADXチェック
        adx = row.get("adx", 0.0)
        if pd.isna(adx) or adx < self.config.adx_min:
            return None

        # ボリュームフィルター
        if not self._check_volume_filter(row):
            return None

        # ATRフィルター（低/過度ボラティリティ排除）
        if not self._check_atr_filter(row):
            return None

        # スコア計算
        buy_score, sell_score, reasons = self._calculate_score(row, candle)

        # 方向決定
        if buy_score > sell_score and buy_score >= self.config.min_score:
            direction = "up"
            signal_type = SignalType.BUY
            score = buy_score
        elif sell_score > buy_score and sell_score >= self.config.min_score:
            direction = "down"
            signal_type = SignalType.SELL
            score = sell_score
        else:
            return None

        # 上位足確認
        htf_ok, htf_reason = self._check_htf_alignment(candle, direction)
        if not htf_ok:
            return None
        if htf_reason:
            reasons.append(htf_reason)

        # SL/TP（固定pips）
        pip_value = 0.01  # USDJPY
        if signal_type == SignalType.BUY:
            stop_loss = candle.close - self.config.sl_pips * pip_value
            take_profit = candle.close + self.config.tp_pips * pip_value
        else:
            stop_loss = candle.close + self.config.sl_pips * pip_value
            take_profit = candle.close - self.config.tp_pips * pip_value

        # クールダウン更新
        self._last_signal_time = candle.time

        confidence = min(score / 10, 1.0)

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
