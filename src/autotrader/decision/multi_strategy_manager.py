"""マルチ戦略マネージャー

複数の時間足・戦略を組み合わせて収益機会を最大化する。
- H1: トレンドフォロー（勝率重視）
- M15: モメンタムスキャルピング
- M5: 超短期リバーサル
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
class MultiStrategyConfig:
    """マルチ戦略設定"""

    # H1設定
    h1_enabled: bool = True
    h1_min_score: int = 4
    h1_min_adx: float = 18.0
    h1_sl_atr: float = 2.5
    h1_tp_atr: float = 1.8
    h1_cooldown: int = 120  # 分

    # M15設定
    m15_enabled: bool = True
    m15_min_score: int = 5
    m15_min_adx: float = 20.0
    m15_sl_atr: float = 2.0
    m15_tp_atr: float = 1.5
    m15_cooldown: int = 45  # 分

    # リスク管理
    max_daily_trades: int = 10
    max_daily_loss_pct: float = 3.0
    position_sizing: str = "fixed"  # "fixed", "kelly", "volatility"

    @classmethod
    def aggressive(cls) -> MultiStrategyConfig:
        """積極設定"""
        return cls(
            h1_min_score=3,
            m15_min_score=4,
            h1_cooldown=60,
            m15_cooldown=30,
            max_daily_trades=15,
        )

    @classmethod
    def conservative(cls) -> MultiStrategyConfig:
        """保守設定"""
        return cls(
            h1_min_score=5,
            m15_min_score=6,
            h1_cooldown=180,
            m15_cooldown=90,
            max_daily_trades=5,
            max_daily_loss_pct=2.0,
        )


@dataclass
class StrategyState:
    """戦略状態"""

    last_h1_signal: datetime | None = None
    last_m15_signal: datetime | None = None
    daily_trades: int = 0
    daily_pnl_pct: float = 0.0
    current_date: str = ""


class MultiStrategyManager:
    """マルチ戦略マネージャー

    H1とM15の両方でシグナルを生成し、
    リスク管理ルールに基づいて統合する。

    Args:
        config: 設定
    """

    def __init__(
        self,
        config: MultiStrategyConfig | None = None,
    ) -> None:
        self.config = config or MultiStrategyConfig()
        self.state = StrategyState()
        self._htf_data: dict[str, pd.DataFrame] = {}

    def set_higher_tf_data(self, tf_name: str, df: pd.DataFrame) -> None:
        """上位足データを設定"""
        self._htf_data[tf_name] = df

    def reset(self) -> None:
        """状態リセット"""
        self.state = StrategyState()

    def update_daily_state(
        self,
        current_time: datetime,
        pnl_pct: float = 0.0,
    ) -> None:
        """日次状態更新"""
        date_str = current_time.strftime("%Y-%m-%d")
        if date_str != self.state.current_date:
            self.state.current_date = date_str
            self.state.daily_trades = 0
            self.state.daily_pnl_pct = 0.0
        else:
            self.state.daily_pnl_pct += pnl_pct

    def _get_htf_trend(
        self,
        tf_name: str,
        current_time: datetime,
    ) -> tuple[str, float]:
        """上位足トレンド取得"""
        if tf_name not in self._htf_data:
            return "neutral", 0.0

        df = self._htf_data[tf_name]
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

        if close > sma_20 > sma_50:
            return "up", adx
        elif close < sma_20 < sma_50:
            return "down", adx

        return "neutral", adx

    def _calculate_score(
        self,
        row: pd.Series,
        close: float,
    ) -> tuple[int, int, list[str]]:
        """スコア計算（共通ロジック）"""
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

        # RSI
        if not pd.isna(rsi):
            if rsi < 25:
                buy_score += 3
                reasons.append(f"RSI極低({rsi:.0f})")
            elif rsi < 35:
                buy_score += 2
            elif rsi > 75:
                sell_score += 3
                reasons.append(f"RSI極高({rsi:.0f})")
            elif rsi > 65:
                sell_score += 2

        # MACD
        if not pd.isna(macd) and not pd.isna(macd_signal):
            if macd > macd_signal:
                buy_score += 2
                if macd > 0:
                    buy_score += 1
            elif macd < macd_signal:
                sell_score += 2
                if macd < 0:
                    sell_score += 1

        # トレンド
        if not pd.isna(sma_20) and not pd.isna(sma_50):
            if close > sma_20 > sma_50:
                buy_score += 2
                reasons.append("上昇トレンド")
            elif close < sma_20 < sma_50:
                sell_score += 2
                reasons.append("下降トレンド")

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
            reasons.append(f"強ADX({adx:.0f})")

        # ダイバージェンス
        if row.get("is_bullish_div", False):
            buy_score += 3
            reasons.append("強気ダイバージェンス")
        if row.get("is_bearish_div", False):
            sell_score += 3
            reasons.append("弱気ダイバージェンス")

        return buy_score, sell_score, reasons

    def _check_daily_limits(self) -> bool:
        """日次制限チェック"""
        if self.state.daily_trades >= self.config.max_daily_trades:
            return False
        if self.state.daily_pnl_pct <= -self.config.max_daily_loss_pct:
            return False
        return True

    def generate_h1_signal(
        self,
        row: pd.Series,
        candle: Candle,
        symbol: str,
    ) -> Signal | None:
        """H1シグナル生成"""
        from autotrader.core.entities import Signal

        if not self.config.h1_enabled:
            return None

        # 日次制限
        self.update_daily_state(candle.time)
        if not self._check_daily_limits():
            return None

        # クールダウン
        if self.state.last_h1_signal:
            cooldown = timedelta(minutes=self.config.h1_cooldown)
            if candle.time < self.state.last_h1_signal + cooldown:
                return None

        # ADX
        adx = row.get("adx", 0.0)
        if pd.isna(adx) or adx < self.config.h1_min_adx:
            return None

        # スコア計算
        buy_score, sell_score, reasons = self._calculate_score(row, candle.close)

        # 方向決定
        min_score = self.config.h1_min_score
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
        h4_dir, _ = self._get_htf_trend("H4", candle.time)
        d1_dir, _ = self._get_htf_trend("D1", candle.time)
        if h4_dir != "neutral" and h4_dir != direction:
            return None
        if d1_dir != "neutral" and d1_dir != direction:
            return None

        # SL/TP
        atr = row.get("atr_14", 0.5)
        if pd.isna(atr) or atr <= 0:
            atr = 0.5

        if signal_type == SignalType.BUY:
            stop_loss = candle.close - atr * self.config.h1_sl_atr
            take_profit = candle.close + atr * self.config.h1_tp_atr
        else:
            stop_loss = candle.close + atr * self.config.h1_sl_atr
            take_profit = candle.close - atr * self.config.h1_tp_atr

        self.state.last_h1_signal = candle.time
        self.state.daily_trades += 1

        return Signal(
            signal_id=str(uuid4()),
            symbol=symbol,
            timeframe=Timeframe.H1,
            signal_type=signal_type,
            confidence=min(score / 10, 1.0),
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=f"H1スコア{score}: " + ", ".join(reasons),
            created_at=candle.time,
        )

    def generate_m15_signal(
        self,
        row: pd.Series,
        candle: Candle,
        symbol: str,
    ) -> Signal | None:
        """M15シグナル生成"""
        from autotrader.core.entities import Signal

        if not self.config.m15_enabled:
            return None

        # 日次制限
        self.update_daily_state(candle.time)
        if not self._check_daily_limits():
            return None

        # クールダウン
        if self.state.last_m15_signal:
            cooldown = timedelta(minutes=self.config.m15_cooldown)
            if candle.time < self.state.last_m15_signal + cooldown:
                return None

        # ADX
        adx = row.get("adx", 0.0)
        if pd.isna(adx) or adx < self.config.m15_min_adx:
            return None

        # スコア計算
        buy_score, sell_score, reasons = self._calculate_score(row, candle.close)

        # 方向決定
        min_score = self.config.m15_min_score
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

        # 上位足確認（H1とH4）
        h1_dir, _ = self._get_htf_trend("H1", candle.time)
        h4_dir, _ = self._get_htf_trend("H4", candle.time)
        if h1_dir != "neutral" and h1_dir != direction:
            return None
        if h4_dir != "neutral" and h4_dir != direction:
            return None

        # SL/TP
        atr = row.get("atr_14", 0.2)
        if pd.isna(atr) or atr <= 0:
            atr = 0.2

        if signal_type == SignalType.BUY:
            stop_loss = candle.close - atr * self.config.m15_sl_atr
            take_profit = candle.close + atr * self.config.m15_tp_atr
        else:
            stop_loss = candle.close + atr * self.config.m15_sl_atr
            take_profit = candle.close - atr * self.config.m15_tp_atr

        self.state.last_m15_signal = candle.time
        self.state.daily_trades += 1

        return Signal(
            signal_id=str(uuid4()),
            symbol=symbol,
            timeframe=Timeframe.M15,
            signal_type=signal_type,
            confidence=min(score / 10, 1.0),
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=f"M15スコア{score}: " + ", ".join(reasons),
            created_at=candle.time,
        )
