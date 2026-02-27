"""V2リスク管理モジュール。

構造ベースのSL/TP検証、NoTrade条件チェック、
ポジションサイジングを提供する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import V2RiskConfig
from autotrader.decision.v2.market_context import MarketContext
from autotrader.decision.v2.strategies.base import V2EntrySignal

logger = logging.getLogger(__name__)


@dataclass
class V2BotState:
    """V2ボットの状態。

    トレード実行に伴い更新される可変状態。
    年初にリセットされる。

    Attributes:
        equity: 現在の有効証拠金。
        initial_equity: 初期証拠金。
        consecutive_losses: 連敗数。
        consecutive_wins: 連勝数。
        peak_equity: 最高到達証拠金。
    """

    equity: float = 1_000_000.0
    initial_equity: float = 1_000_000.0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    peak_equity: float = 1_000_000.0


@dataclass(frozen=True)
class NoTradeReason:
    """トレード不可理由。

    Attributes:
        code: 理由コード。
        message: 説明メッセージ。
    """

    code: str
    message: str


class V2RiskManager:
    """V2リスク管理。

    以下の責務を担う:
    - NoTrade条件チェック（スプレッド、時間帯、連敗）
    - SL/TP価格の妥当性検証
    - ポジションサイジング（確信度スケーリング）

    Args:
        config: リスク管理設定。
        pip_unit: 1pipの価格単位。
        pip_value: 1pip/lotあたりの損益。
    """

    def __init__(
        self,
        config: V2RiskConfig | None = None,
        pip_unit: float = 0.01,
        pip_value: float = 100.0,
    ) -> None:
        self._config = config or V2RiskConfig()
        self._pip_unit = pip_unit
        self._pip_value = pip_value

    def check_no_trade(
        self,
        ctx: MarketContext,
        state: V2BotState,
    ) -> NoTradeReason | None:
        """NoTrade条件をチェック。

        Args:
            ctx: 現在の市場コンテキスト。
            state: ボット状態。

        Returns:
            トレード不可の場合NoTradeReason、
            問題なければNone。
        """
        cfg = self._config

        # スプレッドチェック
        if ctx.spread_pips > cfg.max_spread_pips:
            return NoTradeReason(
                code="HIGH_SPREAD",
                message=(
                    f"スプレッド {ctx.spread_pips:.1f}pips"
                    f" > 上限 {cfg.max_spread_pips}pips"
                ),
            )

        # 時間帯チェック
        hour_utc = ctx.current_time.hour
        if hour_utc in cfg.blocked_hours_utc:
            return NoTradeReason(
                code="BLOCKED_HOURS",
                message=f"低流動性時間帯 (UTC {hour_utc}時)",
            )

        # 連敗チェック
        if state.consecutive_losses >= cfg.max_consecutive_losses:
            return NoTradeReason(
                code="CONSECUTIVE_LOSSES",
                message=(
                    f"連敗 {state.consecutive_losses}回"
                    f" >= 上限 {cfg.max_consecutive_losses}回"
                ),
            )

        return None

    def validate_signal(
        self,
        signal: V2EntrySignal,
        ctx: MarketContext,
    ) -> bool:
        """エントリーシグナルの妥当性を検証。

        SL/TP価格が現在価格に対して正しい方向か確認。

        Args:
            signal: 戦略が生成したシグナル。
            ctx: 現在の市場コンテキスト。

        Returns:
            True=有効、False=無効。
        """
        price = ctx.current_price
        if signal.direction == SignalType.BUY:
            # BUY: SL < price < TP
            if signal.sl_price >= price:
                return False
            if signal.tp_price <= price:
                return False
        else:
            # SELL: TP < price < SL
            if signal.sl_price <= price:
                return False
            if signal.tp_price >= price:
                return False
        return True

    def calculate_lot(
        self,
        signal: V2EntrySignal,
        ctx: MarketContext,
        state: V2BotState,
    ) -> float:
        """ポジションサイズを計算。

        リスク率ベースのロットサイジング。
        確信度スケーリングオプション付き。

        Args:
            signal: エントリーシグナル。
            ctx: 市場コンテキスト。
            state: ボット状態。

        Returns:
            ロットサイズ（0.01単位に丸め）。
        """
        cfg = self._config

        # リスク率の決定
        risk_pct = cfg.base_risk_pct
        if cfg.confidence_scale:
            # 確信度でリスクをスケーリング
            # 0.5→base, 1.0→max
            scale = min(
                1.0,
                (signal.confidence - 0.5) / 0.5,
            )
            risk_pct = (
                cfg.base_risk_pct
                + scale * (cfg.max_risk_pct - cfg.base_risk_pct)
            )

        # リスク金額
        risk_amount = state.equity * risk_pct

        # SL距離(pips)
        sl_pips = (
            abs(ctx.current_price - signal.sl_price)
            / self._pip_unit
        )
        if sl_pips < 1.0:
            return 0.01

        # ロット = リスク金額 / (SL pips × pip_value)
        loss_per_lot = sl_pips * self._pip_value
        if loss_per_lot <= 0:
            return 0.01

        lot = risk_amount / loss_per_lot

        # 0.01単位に丸め、最低0.01
        lot = max(0.01, round(lot / 0.01) * 0.01)
        # 上限クランプ
        lot = min(lot, 5.0)

        return lot

    def calculate_trailing_stop(
        self,
        entry_price: float,
        current_price: float,
        current_sl: float,
        direction: SignalType,
        atr: float,
    ) -> float | None:
        """トレーリングストップを計算。

        RR到達に応じてSLを引き上げ/引き下げ。

        Args:
            entry_price: エントリー価格。
            current_price: 現在価格。
            current_sl: 現在のSL。
            direction: トレード方向。
            atr: 現在のATR。

        Returns:
            新しいSL価格。変更不要ならNone。
        """
        cfg = self._config

        risk = abs(entry_price - current_sl)
        if risk <= 0:
            # BE済み→ATRをリスク代用
            risk = atr
            if risk <= 0:
                return None

        if direction == SignalType.BUY:
            profit = current_price - entry_price
        else:
            profit = entry_price - current_price

        rr = profit / risk

        # ブレイクイーブン
        if rr >= cfg.breakeven_at_rr:
            be_price = entry_price
            if direction == SignalType.BUY:
                if current_sl < be_price:
                    return be_price
            else:
                if current_sl > be_price:
                    return be_price

        # トレーリング開始
        if rr >= cfg.trailing_start_rr:
            trail_dist = atr * cfg.trailing_atr_multiplier
            if direction == SignalType.BUY:
                new_sl = current_price - trail_dist
                if new_sl > current_sl:
                    return new_sl
            else:
                new_sl = current_price + trail_dist
                if new_sl < current_sl:
                    return new_sl

        return None
