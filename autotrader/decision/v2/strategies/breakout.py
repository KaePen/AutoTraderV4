"""ブレイクアウト戦略。

QUIETレジーム時に使用。
BBスクイーズ解消 + レンジブレイク + BOS確認 +
ADX上昇でエントリー。
"""

from __future__ import annotations

import logging

from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import BreakoutConfig
from autotrader.decision.v2.market_context import MarketContext
from autotrader.decision.v2.strategies.base import (
    V2EntrySignal,
    V2StrategyBase,
)

logger = logging.getLogger(__name__)


class BreakoutStrategy(V2StrategyBase):
    """ブレイクアウト戦略。

    エントリー条件:
    1. QUIET状態がN足以上継続
    2. BBスクイーズ解消（bb_squeeze低下）
    3. BOS確認
    4. ADX > 閾値

    Args:
        config: 戦略設定。Noneでデフォルト値。
        pip_unit: 1pipの価格単位。
    """

    def __init__(
        self,
        config: BreakoutConfig | None = None,
        pip_unit: float = 0.01,
        *,
        quiet_bars_counter: int = 0,
    ) -> None:
        self._config = config or BreakoutConfig()
        self._pip_unit = pip_unit
        self._quiet_bars: int = quiet_bars_counter

    @property
    def name(self) -> str:
        return "Breakout"

    def update_quiet_bars(self, is_quiet: bool) -> None:
        """QUIET足数カウンタを更新。

        RegimeClassifierの結果に基づき、
        TradeBot から毎足呼び出す。
        """
        if is_quiet:
            self._quiet_bars += 1
        else:
            self._quiet_bars = 0

    def evaluate(
        self, ctx: MarketContext,
    ) -> V2EntrySignal | None:
        """ブレイクアウトのエントリー評価。"""
        cfg = self._config

        # --- 1. QUIET継続足数チェック ---
        if self._quiet_bars < cfg.min_quiet_bars:
            return None

        # --- 2. BBスクイーズ解消チェック ---
        if not self._check_squeeze_release(ctx):
            return None

        # --- 3. BOS + 方向決定 ---
        direction = self._check_bos(ctx)
        if direction is None:
            return None

        # --- 4. ADXチェック ---
        if ctx.h1.adx < cfg.adx_breakout_threshold:
            return None

        # --- 5. SL計算（コンソリデーション下端/上端） ---
        sl_price = self._calc_sl(ctx, direction)
        sl_pips = (
            abs(ctx.current_price - sl_price)
            / self._pip_unit
        )
        if sl_pips < 1.0:
            return None

        # --- 6. TP計算（測定目標値） ---
        tp_price = self._calc_tp(ctx, direction, sl_pips)
        risk = abs(ctx.current_price - sl_price)
        tp_dist = abs(tp_price - ctx.current_price)
        if risk > 0 and tp_dist / risk < cfg.tp_min_rr:
            return None

        # --- 7. 確信度計算 ---
        confidence = self._calc_confidence(ctx, direction)

        return V2EntrySignal(
            direction=direction,
            confidence=confidence,
            sl_price=sl_price,
            tp_price=tp_price,
            reasoning=(
                f"Breakout: quiet={self._quiet_bars}bars"
                f" ADX={ctx.h1.adx:.1f}"
                f" bb_sq={ctx.h1.bb_squeeze:.2f}"
                f" SL={sl_pips:.0f}pips"
            ),
            strategy_name=self.name,
        )

    # -------------------------------------------------------
    # 内部チェックメソッド
    # -------------------------------------------------------

    def _check_squeeze_release(
        self, ctx: MarketContext,
    ) -> bool:
        """BBスクイーズ解消を確認。

        bb_squeeze値が閾値を下回ると解消判定。
        """
        return ctx.h1.bb_squeeze < self._config.squeeze_threshold

    def _check_bos(
        self, ctx: MarketContext,
    ) -> SignalType | None:
        """H1のBOSからブレイク方向を判定。"""
        bos = ctx.h4.bos_signal
        if bos > 0:
            return SignalType.BUY
        if bos < 0:
            return SignalType.SELL
        # H1構造方向にフォールバック
        sd = ctx.h4.structure_direction
        if sd > 0:
            return SignalType.BUY
        if sd < 0:
            return SignalType.SELL
        return None

    def _calc_sl(
        self, ctx: MarketContext, direction: SignalType,
    ) -> float:
        """コンソリデーションレンジ端 + ATRバッファ。"""
        cfg = self._config
        buf = ctx.h1.atr * cfg.sl_atr_buffer
        if direction == SignalType.BUY:
            # レンジ下端 = BB下限
            return ctx.h1.bb_lower - buf
        # レンジ上端 = BB上限
        return ctx.h1.bb_upper + buf

    def _calc_tp(
        self,
        ctx: MarketContext,
        direction: SignalType,
        sl_pips: float,
    ) -> float:
        """測定目標値（レンジ幅×N）でTPを計算。

        レンジ幅 = BB幅。H4構造レベルも考慮。
        """
        cfg = self._config
        range_width = ctx.h1.bb_upper - ctx.h1.bb_lower
        target_dist = range_width * cfg.tp_range_multiplier

        if direction == SignalType.BUY:
            tp = ctx.current_price + target_dist
            # H4構造レベルも候補
            if ctx.h4.last_swing_high > ctx.current_price:
                struct_dist = (
                    ctx.h4.last_swing_high
                    - ctx.current_price
                )
                target_dist = max(target_dist, struct_dist)
                tp = ctx.current_price + target_dist
            return tp
        else:
            tp = ctx.current_price - target_dist
            if ctx.h4.last_swing_low < ctx.current_price:
                struct_dist = (
                    ctx.current_price
                    - ctx.h4.last_swing_low
                )
                target_dist = max(target_dist, struct_dist)
                tp = ctx.current_price - target_dist
            return tp

    def _calc_confidence(
        self, ctx: MarketContext, direction: SignalType,
    ) -> float:
        """確信度を計算。

        4要素の重み付きスコア:
        - BOS確認
        - ADX強度
        - BB拡大率
        - D1構造整合
        """
        cfg = self._config
        total = 0.0

        # BOS確認: BOS信号がある場合高スコア
        bos_score = 1.0 if ctx.h4.bos_signal != 0 else 0.3
        total += cfg.weight_bos_confirm * bos_score

        # ADX強度: 閾値からの超過度合い
        adx_excess = max(
            0,
            ctx.h1.adx - cfg.adx_breakout_threshold,
        )
        adx_score = min(1.0, adx_excess / 20.0)
        total += cfg.weight_adx_strength * adx_score

        # BB拡大率: bb_widthの大きさ
        bb_score = min(1.0, ctx.h1.bb_width * 10.0)
        total += cfg.weight_bb_expansion * bb_score

        # D1構造整合: ブレイク方向とD1方向の一致
        if direction == SignalType.BUY:
            d1_align = (
                1.0 if ctx.d1.structure_direction > 0
                else 0.3
            )
        else:
            d1_align = (
                1.0 if ctx.d1.structure_direction < 0
                else 0.3
            )
        total += cfg.weight_d1_alignment * d1_align

        return max(0.0, min(1.0, total))
