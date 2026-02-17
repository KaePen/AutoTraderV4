"""コンサバティブ・スキャルピング戦略

月+5%を目標に堅実なトレードを行う。
- 上位足トレンド方向のみエントリー
- 短いTP（勝率重視）
- ボラティリティ適応型SL
- 連敗時の休止機能
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pandas as pd

from autotrader.core.enums import SignalType, Timeframe

if TYPE_CHECKING:
    from autotrader.core.entities import Candle, Signal


@dataclass
class ScalperConfig:
    """スキャルパー設定"""

    # エントリー条件
    min_adx: float = 20.0  # 最小ADX
    rsi_oversold: float = 35.0  # RSI売られすぎ
    rsi_overbought: float = 65.0  # RSI買われすぎ
    min_score: int = 4  # 最小スコア

    # SL/TP設定（ATR倍率）
    sl_atr_mult: float = 2.0  # SL = 2.0 × ATR
    tp_atr_mult: float = 2.5  # TP = 2.5 × ATR（利益重視に変更）

    # ボラティリティフィルター
    min_atr_pips: float = 3.0  # 最小ATR（pips）
    max_atr_pips: float = 50.0  # 最大ATR（pips）

    # クールダウン
    cooldown_bars: int = 3  # 最小待機足数

    # 連敗管理
    max_consecutive_losses: int = 3  # 連敗上限
    loss_cooldown_bars: int = 12  # 連敗時休止足数

    # 上位足確認
    require_htf_trend: bool = True

    @classmethod
    def for_m5(cls) -> ScalperConfig:
        """M5用設定"""
        return cls(
            min_adx=20.0,
            rsi_oversold=28.0,
            rsi_overbought=72.0,
            min_score=5,  # 厳選
            sl_atr_mult=2.0,
            tp_atr_mult=2.0,
            min_atr_pips=2.0,
            max_atr_pips=20.0,
            cooldown_bars=12,
            max_consecutive_losses=3,
            loss_cooldown_bars=36,
        )

    @classmethod
    def for_m15(cls) -> ScalperConfig:
        """M15用設定"""
        return cls(
            min_adx=18.0,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            min_score=4,
            sl_atr_mult=2.0,
            tp_atr_mult=2.2,
            min_atr_pips=3.0,
            max_atr_pips=30.0,
            cooldown_bars=8,
            max_consecutive_losses=3,
            loss_cooldown_bars=24,
        )

    @classmethod
    def for_h1(cls) -> ScalperConfig:
        """H1用設定"""
        return cls(
            min_adx=18.0,
            rsi_oversold=32.0,
            rsi_overbought=68.0,
            min_score=4,
            sl_atr_mult=2.2,
            tp_atr_mult=2.8,  # 利益重視
            min_atr_pips=5.0,
            max_atr_pips=50.0,
            cooldown_bars=2,
            max_consecutive_losses=3,
            loss_cooldown_bars=8,
        )


@dataclass
class ScalperState:
    """スキャルパー状態"""

    last_signal_bar: int = -999
    consecutive_losses: int = 0
    loss_cooldown_until: int = 0
    total_trades: int = 0
    winning_trades: int = 0


class ConservativeScalper:
    """コンサバティブ・スキャルパー

    月+5%を目標に堅実なスキャルピングを行う。
    上位足トレンド方向のみエントリー、短いTPで勝率重視。

    Args:
        config: 設定
        timeframe: 時間足
    """

    def __init__(
        self,
        config: ScalperConfig | None = None,
        timeframe: Timeframe = Timeframe.M5,
    ) -> None:
        if config is None:
            if timeframe == Timeframe.M5:
                config = ScalperConfig.for_m5()
            elif timeframe == Timeframe.M15:
                config = ScalperConfig.for_m15()
            else:
                config = ScalperConfig.for_h1()

        self.config = config
        self.timeframe = timeframe
        self.state = ScalperState()
        self._htf_data: dict[str, pd.DataFrame] = {}
        self._bar_count: int = 0

    def set_higher_tf_data(self, tf_name: str, df: pd.DataFrame) -> None:
        """上位足データを設定

        Args:
            tf_name: 時間足名（例: "H1", "H4"）
            df: OHLCVデータ（sma_20, sma_50, adx列必須）
        """
        self._htf_data[tf_name] = df

    def reset(self) -> None:
        """状態をリセット"""
        self.state = ScalperState()
        self._bar_count = 0

    def record_trade_result(self, is_win: bool) -> None:
        """トレード結果を記録

        Args:
            is_win: 勝ちトレードか
        """
        self.state.total_trades += 1
        if is_win:
            self.state.winning_trades += 1
            self.state.consecutive_losses = 0
        else:
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= self.config.max_consecutive_losses:
                self.state.loss_cooldown_until = (
                    self._bar_count + self.config.loss_cooldown_bars
                )

    def _get_htf_trend(
        self,
        tf_name: str,
        current_time: datetime,
    ) -> str:
        """上位足トレンド取得

        Returns:
            str: "up", "down", "neutral"
        """
        if tf_name not in self._htf_data:
            return "neutral"

        df = self._htf_data[tf_name]
        mask = df["time"] <= current_time
        if not mask.any():
            return "neutral"

        row = df[mask].iloc[-1]
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

    def _check_htf_alignment(
        self,
        candle: Candle,
        direction: str,
    ) -> bool:
        """上位足アライメント確認

        Args:
            candle: 現在のキャンドル
            direction: 希望方向 ("up" or "down")

        Returns:
            bool: 一致しているか
        """
        if not self.config.require_htf_trend:
            return True

        # 時間足に応じた上位足を確認
        if self.timeframe in (Timeframe.M1, Timeframe.M5):
            htf_list = ["M15", "H1"]
        elif self.timeframe == Timeframe.M15:
            htf_list = ["H1", "H4"]
        else:
            htf_list = ["H4", "D1"]

        for tf_name in htf_list:
            htf_trend = self._get_htf_trend(tf_name, candle.time)
            if htf_trend != "neutral" and htf_trend != direction:
                return False

        return True

    def _is_cooldown_active(self) -> bool:
        """クールダウン確認"""
        # 通常クールダウン
        if self._bar_count < self.state.last_signal_bar + self.config.cooldown_bars:
            return True
        # 連敗後クールダウン
        if self._bar_count < self.state.loss_cooldown_until:
            return True
        return False

    def _calculate_signal_score(
        self,
        row: pd.Series,
        candle: Candle,
    ) -> tuple[int, int, list[str]]:
        """シグナルスコア計算

        Returns:
            tuple[int, int, list[str]]: (買いスコア, 売りスコア, 理由)
        """
        buy_score = 0
        sell_score = 0
        reasons: list[str] = []

        rsi = row.get("rsi_14")
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        stoch_k = row.get("stoch_k")
        sma_20 = row.get("sma_20")
        adx = row.get("adx", 0.0)

        # RSI
        if not pd.isna(rsi):
            if rsi < self.config.rsi_oversold:
                buy_score += 2
                reasons.append(f"RSI低({rsi:.0f})")
            elif rsi > self.config.rsi_overbought:
                sell_score += 2
                reasons.append(f"RSI高({rsi:.0f})")

        # MACD
        if not pd.isna(macd) and not pd.isna(macd_signal):
            if macd > macd_signal:
                buy_score += 1
                if macd > 0:
                    buy_score += 1
            elif macd < macd_signal:
                sell_score += 1
                if macd < 0:
                    sell_score += 1

        # トレンド
        if not pd.isna(sma_20):
            if candle.close > sma_20:
                buy_score += 1
            elif candle.close < sma_20:
                sell_score += 1

        # ストキャスティクス
        if not pd.isna(stoch_k):
            if stoch_k < 20:
                buy_score += 2
                reasons.append(f"Stoch低({stoch_k:.0f})")
            elif stoch_k > 80:
                sell_score += 2
                reasons.append(f"Stoch高({stoch_k:.0f})")

        # ADX
        if not pd.isna(adx) and adx > 25:
            if buy_score > sell_score:
                buy_score += 1
            elif sell_score > buy_score:
                sell_score += 1
            reasons.append(f"ADX強({adx:.0f})")

        return buy_score, sell_score, reasons

    def generate(
        self,
        row: pd.Series,
        candle: Candle,
        symbol: str,
    ) -> Signal | None:
        """シグナル生成

        Args:
            row: 指標付きデータ行
            candle: 現在のキャンドル
            symbol: シンボル

        Returns:
            Signal | None: シグナル
        """
        from autotrader.core.entities import Signal

        self._bar_count += 1

        # クールダウンチェック
        if self._is_cooldown_active():
            return None

        # ADXチェック
        adx = row.get("adx", 0.0)
        if pd.isna(adx) or adx < self.config.min_adx:
            return None

        # ATRチェック
        atr = row.get("atr_14", 0.0)
        if pd.isna(atr) or atr <= 0:
            return None
        atr_pips = atr * 100  # USDJPY想定
        if atr_pips < self.config.min_atr_pips:
            return None
        if atr_pips > self.config.max_atr_pips:
            return None

        # スコア計算
        buy_score, sell_score, reasons = self._calculate_signal_score(row, candle)

        # 優勢な方向を決定
        min_score = self.config.min_score
        if buy_score > sell_score and buy_score >= min_score:
            direction = "up"
            signal_type = SignalType.BUY
            score = buy_score
        elif sell_score > buy_score and sell_score >= min_score:
            direction = "down"
            signal_type = SignalType.SELL
            score = sell_score
        else:
            return None

        # 上位足確認
        if not self._check_htf_alignment(candle, direction):
            return None

        # SL/TP計算
        if signal_type == SignalType.BUY:
            stop_loss = candle.close - atr * self.config.sl_atr_mult
            take_profit = candle.close + atr * self.config.tp_atr_mult
        else:
            stop_loss = candle.close + atr * self.config.sl_atr_mult
            take_profit = candle.close - atr * self.config.tp_atr_mult

        # 状態更新
        self.state.last_signal_bar = self._bar_count

        confidence = min(score / 8, 1.0)

        return Signal(
            signal_id=str(uuid4()),
            symbol=symbol,
            timeframe=self.timeframe,
            signal_type=signal_type,
            confidence=confidence,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=f"スコア{score}: " + ", ".join(reasons),
            created_at=candle.time,
        )


class MultiTimeframeScalper:
    """マルチタイムフレーム・スキャルパー

    複数時間足で同時にシグナルを生成し、
    一貫した方向のシグナルのみを採用する。

    Args:
        timeframes: 使用する時間足リスト
    """

    def __init__(
        self,
        timeframes: list[Timeframe] | None = None,
    ) -> None:
        if timeframes is None:
            timeframes = [Timeframe.M5, Timeframe.M15, Timeframe.H1]

        self.timeframes = timeframes
        self.scalpers: dict[Timeframe, ConservativeScalper] = {}

        for tf in timeframes:
            self.scalpers[tf] = ConservativeScalper(timeframe=tf)

    def set_higher_tf_data(
        self,
        tf_name: str,
        df: pd.DataFrame,
    ) -> None:
        """上位足データを全スキャルパーに設定"""
        for scalper in self.scalpers.values():
            scalper.set_higher_tf_data(tf_name, df)

    def reset(self) -> None:
        """全スキャルパーをリセット"""
        for scalper in self.scalpers.values():
            scalper.reset()

    def get_scalper(self, timeframe: Timeframe) -> ConservativeScalper:
        """指定時間足のスキャルパーを取得"""
        return self.scalpers[timeframe]
