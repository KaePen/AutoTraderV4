"""決済管理

ポジションの決済判断とトレーリングストップ管理。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import pandas as pd

from autotrader.core.enums import ExitReason, SignalType


class TrailingMode(Enum):
    """トレーリングモード"""

    FIXED = "fixed"  # 固定幅
    ATR_BASED = "atr_based"  # ATRベース
    BREAKEVEN_THEN_TRAIL = "breakeven_then_trail"  # 建値移動後追従


@dataclass(frozen=True)
class ExitDecision:
    """決済判断結果

    Attributes:
        should_exit: 決済すべきか
        reason: 決済理由
        exit_price: 決済価格（SL/TP）
        reasoning: 判断理由詳細
    """

    should_exit: bool
    reason: ExitReason | None
    exit_price: float | None
    reasoning: str


@dataclass(frozen=True)
class TrailingResult:
    """トレーリングストップ結果

    Attributes:
        should_update: 更新すべきか
        new_stop_loss: 新しいSL価格
        reasoning: 更新理由
    """

    should_update: bool
    new_stop_loss: float | None
    reasoning: str


class ExitManager:
    """決済管理クラス

    ポジションの決済判断とトレーリングストップを管理。

    Args:
        trailing_mode: トレーリングモード
        atr_multiplier: ATRベースのストップ距離倍率
        breakeven_trigger_atr: 建値移動トリガー（ATR倍率）
        max_holding_hours: 最大保有時間（時間）
        signal_reversal_exit: シグナル反転で決済するか
    """

    def __init__(
        self,
        trailing_mode: TrailingMode = TrailingMode.BREAKEVEN_THEN_TRAIL,
        atr_multiplier: float = 2.0,
        breakeven_trigger_atr: float = 1.0,
        max_holding_hours: int = 24,
        signal_reversal_exit: bool = True,
    ) -> None:
        self.trailing_mode = trailing_mode
        self.atr_multiplier = atr_multiplier
        self.breakeven_trigger_atr = breakeven_trigger_atr
        self.max_holding_hours = max_holding_hours
        self.signal_reversal_exit = signal_reversal_exit

    def _check_stop_loss(
        self,
        position: dict,
        current_price: float,
    ) -> ExitDecision | None:
        """ストップロスチェック

        Args:
            position: ポジション情報
            current_price: 現在価格

        Returns:
            ExitDecision | None: SL到達時の決済判断
        """
        stop_loss = position.get("stop_loss")
        if stop_loss is None:
            return None

        signal_type = position.get("signal_type")

        if signal_type == SignalType.BUY:
            if current_price <= stop_loss:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.STOP_LOSS,
                    exit_price=stop_loss,
                    reasoning=f"SL到達: {current_price} <= {stop_loss}",
                )
        elif signal_type == SignalType.SELL:
            if current_price >= stop_loss:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.STOP_LOSS,
                    exit_price=stop_loss,
                    reasoning=f"SL到達: {current_price} >= {stop_loss}",
                )

        return None

    def _check_take_profit(
        self,
        position: dict,
        current_price: float,
    ) -> ExitDecision | None:
        """テイクプロフィットチェック

        Args:
            position: ポジション情報
            current_price: 現在価格

        Returns:
            ExitDecision | None: TP到達時の決済判断
        """
        take_profit = position.get("take_profit")
        if take_profit is None:
            return None

        signal_type = position.get("signal_type")

        if signal_type == SignalType.BUY:
            if current_price >= take_profit:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.TAKE_PROFIT,
                    exit_price=take_profit,
                    reasoning=f"TP到達: {current_price} >= {take_profit}",
                )
        elif signal_type == SignalType.SELL:
            if current_price <= take_profit:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.TAKE_PROFIT,
                    exit_price=take_profit,
                    reasoning=f"TP到達: {current_price} <= {take_profit}",
                )

        return None

    def _check_time_exit(
        self,
        position: dict,
        current_time: datetime,
    ) -> ExitDecision | None:
        """時間切れチェック

        Args:
            position: ポジション情報
            current_time: 現在時刻

        Returns:
            ExitDecision | None: 時間切れ時の決済判断
        """
        opened_at = position.get("opened_at")
        if opened_at is None:
            return None

        holding_time = current_time - opened_at
        max_holding = timedelta(hours=self.max_holding_hours)

        if holding_time >= max_holding:
            return ExitDecision(
                should_exit=True,
                reason=ExitReason.TIME_EXIT,
                exit_price=None,
                reasoning=f"保有時間超過: {holding_time} >= {max_holding}",
            )

        return None

    def _check_signal_reversal(
        self,
        position: dict,
        indicators: pd.Series,
    ) -> ExitDecision | None:
        """シグナル反転チェック

        Args:
            position: ポジション情報
            indicators: 指標値

        Returns:
            ExitDecision | None: シグナル反転時の決済判断
        """
        if not self.signal_reversal_exit:
            return None

        signal_type = position.get("signal_type")
        trend_direction = indicators.get("trend_direction")
        ma_alignment = indicators.get("ma_alignment")

        if ma_alignment is None or pd.isna(ma_alignment):
            return None

        # 強い反転シグナル
        reversal = False
        if signal_type == SignalType.BUY:
            if trend_direction in ("strong_down",) and ma_alignment < -0.5:
                reversal = True
        elif signal_type == SignalType.SELL:
            if trend_direction in ("strong_up",) and ma_alignment > 0.5:
                reversal = True

        if reversal:
            return ExitDecision(
                should_exit=True,
                reason=ExitReason.SIGNAL_REVERSAL,
                exit_price=None,
                reasoning=f"シグナル反転: トレンド={trend_direction}, MA整列={ma_alignment:.2f}",
            )

        return None

    def should_exit(
        self,
        position: dict,
        current_price: float,
        current_time: datetime,
        indicators: pd.Series,
    ) -> ExitDecision:
        """決済すべきか判断

        Args:
            position: ポジション情報
            current_price: 現在価格
            current_time: 現在時刻
            indicators: 指標値

        Returns:
            ExitDecision: 決済判断結果
        """
        # 優先度順にチェック
        checks = [
            self._check_stop_loss(position, current_price),
            self._check_take_profit(position, current_price),
            self._check_time_exit(position, current_time),
            self._check_signal_reversal(position, indicators),
        ]

        for decision in checks:
            if decision is not None and decision.should_exit:
                return decision

        return ExitDecision(
            should_exit=False,
            reason=None,
            exit_price=None,
            reasoning="決済条件未達",
        )

    def calculate_trailing_stop(
        self,
        position: dict,
        current_price: float,
        atr: float,
    ) -> TrailingResult:
        """トレーリングストップを計算

        Args:
            position: ポジション情報
            current_price: 現在価格
            atr: ATR値

        Returns:
            TrailingResult: トレーリング結果
        """
        entry_price = position.get("entry_price")
        current_sl = position.get("stop_loss")
        signal_type = position.get("signal_type")

        if entry_price is None or current_sl is None:
            return TrailingResult(
                should_update=False,
                new_stop_loss=None,
                reasoning="エントリー価格またはSLが未設定",
            )

        if self.trailing_mode == TrailingMode.FIXED:
            return self._fixed_trailing(
                entry_price, current_price, current_sl, signal_type, atr
            )
        elif self.trailing_mode == TrailingMode.ATR_BASED:
            return self._atr_trailing(
                current_price, current_sl, signal_type, atr
            )
        else:  # BREAKEVEN_THEN_TRAIL
            return self._breakeven_then_trail(
                entry_price, current_price, current_sl, signal_type, atr
            )

    def _fixed_trailing(
        self,
        entry_price: float,
        current_price: float,
        current_sl: float,
        signal_type: SignalType,
        atr: float,
    ) -> TrailingResult:
        """固定幅トレーリング

        Args:
            entry_price: エントリー価格
            current_price: 現在価格
            current_sl: 現在のSL
            signal_type: シグナル種別
            atr: ATR値

        Returns:
            TrailingResult: トレーリング結果
        """
        trail_distance = atr * self.atr_multiplier

        if signal_type == SignalType.BUY:
            new_sl = current_price - trail_distance
            if new_sl > current_sl:
                return TrailingResult(
                    should_update=True,
                    new_stop_loss=new_sl,
                    reasoning=f"固定幅トレーリング: {current_sl:.5f} -> {new_sl:.5f}",
                )
        elif signal_type == SignalType.SELL:
            new_sl = current_price + trail_distance
            if new_sl < current_sl:
                return TrailingResult(
                    should_update=True,
                    new_stop_loss=new_sl,
                    reasoning=f"固定幅トレーリング: {current_sl:.5f} -> {new_sl:.5f}",
                )

        return TrailingResult(
            should_update=False,
            new_stop_loss=None,
            reasoning="SL更新条件未達",
        )

    def _atr_trailing(
        self,
        current_price: float,
        current_sl: float,
        signal_type: SignalType,
        atr: float,
    ) -> TrailingResult:
        """ATRベーストレーリング

        Args:
            current_price: 現在価格
            current_sl: 現在のSL
            signal_type: シグナル種別
            atr: ATR値

        Returns:
            TrailingResult: トレーリング結果
        """
        trail_distance = atr * self.atr_multiplier

        if signal_type == SignalType.BUY:
            new_sl = current_price - trail_distance
            if new_sl > current_sl:
                return TrailingResult(
                    should_update=True,
                    new_stop_loss=new_sl,
                    reasoning=f"ATRトレーリング ({self.atr_multiplier}ATR): {new_sl:.5f}",
                )
        elif signal_type == SignalType.SELL:
            new_sl = current_price + trail_distance
            if new_sl < current_sl:
                return TrailingResult(
                    should_update=True,
                    new_stop_loss=new_sl,
                    reasoning=f"ATRトレーリング ({self.atr_multiplier}ATR): {new_sl:.5f}",
                )

        return TrailingResult(
            should_update=False,
            new_stop_loss=None,
            reasoning="SL更新条件未達",
        )

    def _breakeven_then_trail(
        self,
        entry_price: float,
        current_price: float,
        current_sl: float,
        signal_type: SignalType,
        atr: float,
    ) -> TrailingResult:
        """建値移動後追従トレーリング

        Args:
            entry_price: エントリー価格
            current_price: 現在価格
            current_sl: 現在のSL
            signal_type: シグナル種別
            atr: ATR値

        Returns:
            TrailingResult: トレーリング結果
        """
        breakeven_trigger = atr * self.breakeven_trigger_atr
        trail_distance = atr * self.atr_multiplier

        if signal_type == SignalType.BUY:
            profit = current_price - entry_price

            # まず建値移動
            if profit >= breakeven_trigger and current_sl < entry_price:
                return TrailingResult(
                    should_update=True,
                    new_stop_loss=entry_price,
                    reasoning=f"建値移動: {current_sl:.5f} -> {entry_price:.5f}",
                )

            # 建値移動済みならトレーリング
            if current_sl >= entry_price:
                new_sl = current_price - trail_distance
                if new_sl > current_sl:
                    return TrailingResult(
                        should_update=True,
                        new_stop_loss=new_sl,
                        reasoning=f"トレーリング: {current_sl:.5f} -> {new_sl:.5f}",
                    )

        elif signal_type == SignalType.SELL:
            profit = entry_price - current_price

            if profit >= breakeven_trigger and current_sl > entry_price:
                return TrailingResult(
                    should_update=True,
                    new_stop_loss=entry_price,
                    reasoning=f"建値移動: {current_sl:.5f} -> {entry_price:.5f}",
                )

            if current_sl <= entry_price:
                new_sl = current_price + trail_distance
                if new_sl < current_sl:
                    return TrailingResult(
                        should_update=True,
                        new_stop_loss=new_sl,
                        reasoning=f"トレーリング: {current_sl:.5f} -> {new_sl:.5f}",
                    )

        return TrailingResult(
            should_update=False,
            new_stop_loss=None,
            reasoning="SL更新条件未達",
        )
