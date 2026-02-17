"""月間収益目標型戦略

月+5%を目標に、収益状況に応じてリスクを動的に調整する。
- 目標未達時: 積極的にトレード
- 目標達成時: 保守的に運用
- 大きな損失時: 回復まで休止
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
class MonthlyTargetConfig:
    """月間目標設定"""

    # 月間目標
    monthly_target_pct: float = 5.0  # 月間目標収益率
    monthly_max_loss_pct: float = -15.0  # 月間最大損失率（停止）
    monthly_stop_profit_pct: float = 15.0  # 月間利益停止率（大きく設定）

    # エントリー条件
    min_score: int = 4
    min_adx: float = 18.0
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0

    # SL/TP設定
    base_sl_atr: float = 2.5
    base_tp_atr: float = 1.8  # 勝率重視の短いTP

    # ポジションサイジング
    base_volume: float = 1.0
    aggressive_volume: float = 1.5  # 目標未達時
    conservative_volume: float = 0.8  # 目標達成後

    # クールダウン
    cooldown_minutes: int = 120

    # 上位足確認
    require_htf_alignment: bool = True
    require_momentum_align: bool = True

    @classmethod
    def standard(cls) -> MonthlyTargetConfig:
        """標準設定"""
        return cls()

    @classmethod
    def aggressive(cls) -> MonthlyTargetConfig:
        """積極設定"""
        return cls(
            monthly_target_pct=7.0,
            min_score=3,
            base_volume=1.5,
            aggressive_volume=2.5,
            cooldown_minutes=60,
        )

    @classmethod
    def conservative(cls) -> MonthlyTargetConfig:
        """保守設定"""
        return cls(
            monthly_target_pct=3.0,
            monthly_max_loss_pct=-8.0,
            min_score=5,
            base_volume=0.8,
            aggressive_volume=1.0,
            cooldown_minutes=180,
        )


@dataclass
class MonthlyState:
    """月間状態"""

    current_month: str = ""
    month_start_balance: float = 0.0
    current_balance: float = 0.0
    month_pnl: float = 0.0
    month_pnl_pct: float = 0.0
    month_trades: int = 0
    month_wins: int = 0
    consecutive_losses: int = 0
    last_signal_time: datetime | None = None
    is_monthly_target_reached: bool = False
    is_monthly_stopped: bool = False


class MonthlyTargetStrategy:
    """月間収益目標型戦略

    月+5%を目標に、収益状況に応じたリスク管理を行う。
    - 勝率重視のエントリー条件（HighWinRateGenerator互換）
    - 月間収益に応じた動的ポジションサイジング
    - 大損時の自動休止

    Args:
        config: 設定
        initial_balance: 初期資金
    """

    def __init__(
        self,
        config: MonthlyTargetConfig | None = None,
        initial_balance: float = 1_000_000.0,
    ) -> None:
        self.config = config or MonthlyTargetConfig.standard()
        self.initial_balance = initial_balance
        self.state = MonthlyState(
            current_balance=initial_balance,
            month_start_balance=initial_balance,
        )
        self._htf_data: dict[str, pd.DataFrame] = {}

    def set_higher_tf_data(self, tf_name: str, df: pd.DataFrame) -> None:
        """上位足データを設定"""
        self._htf_data[tf_name] = df

    def reset(self) -> None:
        """状態をリセット"""
        self.state = MonthlyState(current_balance=self.initial_balance)

    def update_balance(self, new_balance: float, trade_time: datetime) -> None:
        """残高更新

        Args:
            new_balance: 新残高
            trade_time: トレード時刻
        """
        month_key = trade_time.strftime("%Y-%m")

        # 月が変わったら状態リセット
        if month_key != self.state.current_month:
            self.state.current_month = month_key
            self.state.month_start_balance = self.state.current_balance
            self.state.month_pnl = 0.0
            self.state.month_pnl_pct = 0.0
            self.state.month_trades = 0
            self.state.month_wins = 0
            self.state.is_monthly_target_reached = False
            self.state.is_monthly_stopped = False

        # 収益更新
        pnl_change = new_balance - self.state.current_balance
        self.state.current_balance = new_balance
        self.state.month_pnl = new_balance - self.state.month_start_balance

        if self.state.month_start_balance > 0:
            self.state.month_pnl_pct = (
                self.state.month_pnl / self.state.month_start_balance * 100
            )

        # トレード結果記録
        if pnl_change != 0:
            self.state.month_trades += 1
            if pnl_change > 0:
                self.state.month_wins += 1
                self.state.consecutive_losses = 0
            else:
                self.state.consecutive_losses += 1

        # 月間目標/停止判定
        if self.state.month_pnl_pct >= self.config.monthly_target_pct:
            self.state.is_monthly_target_reached = True
        if self.state.month_pnl_pct >= self.config.monthly_stop_profit_pct:
            self.state.is_monthly_stopped = True
        if self.state.month_pnl_pct <= self.config.monthly_max_loss_pct:
            self.state.is_monthly_stopped = True

    def get_current_volume(self) -> float:
        """現在の推奨ボリューム取得"""
        if self.state.is_monthly_stopped:
            return 0.0

        if self.state.is_monthly_target_reached:
            return self.config.conservative_volume

        # 目標進捗に応じて調整
        if self.state.month_pnl_pct < 0:
            # 損失中: 基本ボリューム
            return self.config.base_volume
        elif self.state.month_pnl_pct < self.config.monthly_target_pct * 0.5:
            # 目標半分未達: 積極的
            return self.config.aggressive_volume
        else:
            # 目標に近い: 基本
            return self.config.base_volume

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

        if close > sma_20 and sma_20 > sma_50:
            return "up", adx
        elif close < sma_20 and sma_20 < sma_50:
            return "down", adx

        return "neutral", adx

    def _calculate_score(
        self,
        row: pd.Series,
        candle: Candle,
    ) -> tuple[int, int, list[str]]:
        """買い/売りスコア計算"""
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

        # RSI (最大+3点)
        if not pd.isna(rsi):
            if rsi < self.config.rsi_oversold - 10:
                buy_score += 3
                reasons.append(f"RSI極低({rsi:.0f})")
            elif rsi < self.config.rsi_oversold:
                buy_score += 2

            if rsi > self.config.rsi_overbought + 10:
                sell_score += 3
                reasons.append(f"RSI極高({rsi:.0f})")
            elif rsi > self.config.rsi_overbought:
                sell_score += 2

        # MACD (最大+3点)
        if not pd.isna(macd) and not pd.isna(macd_signal):
            if macd > macd_signal:
                buy_score += 1
                if macd > 0:
                    buy_score += 1
                macd_hist = row.get("macd_histogram", 0)
                macd_slope = row.get("macd_hist_slope", 0)
                if not pd.isna(macd_slope) and macd_slope > 0:
                    buy_score += 1
            elif macd < macd_signal:
                sell_score += 1
                if macd < 0:
                    sell_score += 1
                macd_hist = row.get("macd_histogram", 0)
                macd_slope = row.get("macd_hist_slope", 0)
                if not pd.isna(macd_slope) and macd_slope < 0:
                    sell_score += 1

        # トレンド (最大+2点)
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

        # ADX (最大+2点)
        if not pd.isna(adx):
            if adx > 30:
                if buy_score > sell_score:
                    buy_score += 2
                elif sell_score > buy_score:
                    sell_score += 2
                reasons.append(f"強トレンド(ADX:{adx:.0f})")
            elif adx > 25:
                if buy_score > sell_score:
                    buy_score += 1
                elif sell_score > buy_score:
                    sell_score += 1

        # ストキャスティクス (最大+2点)
        if not pd.isna(stoch_k):
            if stoch_k < 20:
                buy_score += 2
                reasons.append(f"Stoch低({stoch_k:.0f})")
            elif stoch_k < 30:
                buy_score += 1
            elif stoch_k > 80:
                sell_score += 2
                reasons.append(f"Stoch高({stoch_k:.0f})")
            elif stoch_k > 70:
                sell_score += 1

        # ダイバージェンス (最大+3点)
        if row.get("is_bullish_div", False):
            buy_score += 3
            reasons.append("強気ダイバージェンス")
        if row.get("is_bearish_div", False):
            sell_score += 3
            reasons.append("弱気ダイバージェンス")

        return buy_score, sell_score, reasons

    def _check_htf_alignment(
        self,
        candle: Candle,
        direction: str,
    ) -> tuple[bool, str]:
        """上位足アライメント確認"""
        if not self.config.require_htf_alignment:
            return True, ""

        aligned_count = 0
        total = 0

        for tf_name in ["H4", "D1"]:
            htf_dir, htf_adx = self._get_htf_trend(tf_name, candle.time)
            if htf_dir != "neutral":
                total += 1
                if htf_dir == direction:
                    aligned_count += 1

        if total == 0:
            return True, "上位足データなし"

        if aligned_count == total:
            return True, f"MTF全一致({aligned_count}/{total})"
        elif aligned_count > 0:
            return False, f"MTF部分一致({aligned_count}/{total})"
        else:
            return False, "MTF逆行"

    def _check_momentum_alignment(
        self,
        row: pd.Series,
        direction: str,
    ) -> tuple[bool, str]:
        """モメンタム整列確認"""
        if not self.config.require_momentum_align:
            return True, ""

        macd = row.get("macd")
        macd_signal = row.get("macd_signal")

        if pd.isna(macd) or pd.isna(macd_signal):
            return True, "MACD計算不可"

        if direction == "up":
            if macd > macd_signal:
                return True, "モメンタム上昇"
            else:
                return False, "モメンタム逆行"
        else:
            if macd < macd_signal:
                return True, "モメンタム下降"
            else:
                return False, "モメンタム逆行"

    def _is_cooldown_active(self, current_time: datetime) -> bool:
        """クールダウン確認"""
        if self.state.last_signal_time is None:
            return False
        cooldown = timedelta(minutes=self.config.cooldown_minutes)
        return current_time < self.state.last_signal_time + cooldown

    def generate(
        self,
        row: pd.Series,
        candle: Candle,
        symbol: str,
        timeframe: Timeframe,
    ) -> Signal | None:
        """シグナル生成

        Args:
            row: 指標付きデータ行
            candle: 現在のキャンドル
            symbol: シンボル
            timeframe: 時間足

        Returns:
            Signal | None: シグナル
        """
        from autotrader.core.entities import Signal

        # 月の更新チェック
        month_key = candle.time.strftime("%Y-%m")
        if month_key != self.state.current_month:
            self.state.current_month = month_key
            self.state.month_start_balance = self.state.current_balance
            self.state.month_pnl = 0.0
            self.state.month_pnl_pct = 0.0
            self.state.month_trades = 0
            self.state.month_wins = 0
            self.state.is_monthly_target_reached = False
            self.state.is_monthly_stopped = False
            self.state.consecutive_losses = 0  # 月初めにリセット

        # 月間停止中はトレードしない
        if self.state.is_monthly_stopped:
            return None

        # クールダウン
        if self._is_cooldown_active(candle.time):
            return None

        # ADXチェック
        adx = row.get("adx", 0.0)
        if pd.isna(adx) or adx < self.config.min_adx:
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

        # 最小スコア
        if score < self.config.min_score:
            return None

        # 上位足確認
        htf_ok, htf_reason = self._check_htf_alignment(candle, direction)
        if not htf_ok:
            return None
        if htf_reason:
            reasons.append(htf_reason)

        # モメンタム確認
        mom_ok, mom_reason = self._check_momentum_alignment(row, direction)
        if not mom_ok:
            return None
        if mom_reason:
            reasons.append(mom_reason)

        # SL/TP計算
        atr = row.get("atr_14", 0.5)
        if pd.isna(atr) or atr <= 0:
            atr = 0.5

        if signal_type == SignalType.BUY:
            stop_loss = candle.close - atr * self.config.base_sl_atr
            take_profit = candle.close + atr * self.config.base_tp_atr
        else:
            stop_loss = candle.close + atr * self.config.base_sl_atr
            take_profit = candle.close - atr * self.config.base_tp_atr

        # 状態更新
        self.state.last_signal_time = candle.time

        confidence = min(score / 12, 1.0)

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
