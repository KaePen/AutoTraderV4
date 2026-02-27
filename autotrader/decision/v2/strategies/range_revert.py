"""レンジ逆張り戦略。

RANGINGレジーム時に使用。
BB極値(<%15 or >%85) + サポレジ到達 +
反転足 or 流動性グラブでエントリー。
"""

from __future__ import annotations

import logging

from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import RangeRevertConfig
from autotrader.decision.v2.market_context import MarketContext
from autotrader.decision.v2.strategies.base import (
    V2EntrySignal,
    V2StrategyBase,
)

logger = logging.getLogger(__name__)


class RangeRevertStrategy(V2StrategyBase):
    """レンジ逆張り戦略。

    エントリー条件:
    1. BB%Bが極値圏(<buy_threshold or >sell_threshold)
    2. サポート/レジスタンス到達
    3. 反転足パターン or 流動性グラブ

    Args:
        config: 戦略設定。Noneでデフォルト値。
        pip_unit: 1pipの価格単位。
    """

    def __init__(
        self,
        config: RangeRevertConfig | None = None,
        pip_unit: float = 0.01,
    ) -> None:
        self._config = config or RangeRevertConfig()
        self._pip_unit = pip_unit

    @property
    def name(self) -> str:
        return "RangeRevert"

    def evaluate(
        self, ctx: MarketContext,
    ) -> V2EntrySignal | None:
        """レンジ逆張りのエントリー評価。"""
        cfg = self._config

        # --- 1. BB極値判定 + 方向決定 ---
        direction = self._check_bb_extreme(ctx)
        if direction is None:
            return None

        # --- 2. サポレジ到達 or 流動性グラブ確認 ---
        has_sr = self._check_sr_or_liquidity(
            ctx, direction,
        )
        if not has_sr:
            return None

        # --- 3. 反転足 or 流動性グラブ ---
        has_trigger = self._check_trigger(ctx, direction)
        if not has_trigger:
            return None

        # --- 4. SL計算（レンジ外端） ---
        sl_price = self._calc_sl(ctx, direction)
        sl_pips = (
            abs(ctx.current_price - sl_price)
            / self._pip_unit
        )
        if sl_pips > cfg.sl_max_pips:
            return None
        if sl_pips < 1.0:
            return None

        # --- 5. TP計算（反対側レンジの70%） ---
        tp_price = self._calc_tp(ctx, direction)
        risk = abs(ctx.current_price - sl_price)
        tp_dist = abs(tp_price - ctx.current_price)
        if risk > 0 and tp_dist / risk < cfg.tp_min_rr:
            return None

        # --- 6. 確信度計算 ---
        confidence = self._calc_confidence(ctx, direction)

        return V2EntrySignal(
            direction=direction,
            confidence=confidence,
            sl_price=sl_price,
            tp_price=tp_price,
            reasoning=(
                f"RangeRevert: BB%B={ctx.h1.bb_percent_b:.2f}"
                f" SL={sl_pips:.0f}pips"
                f" dir={'BUY' if direction == SignalType.BUY else 'SELL'}"
            ),
            strategy_name=self.name,
        )

    # -------------------------------------------------------
    # 内部チェックメソッド
    # -------------------------------------------------------

    def _check_bb_extreme(
        self, ctx: MarketContext,
    ) -> SignalType | None:
        """BB%Bの極値から方向を判定。"""
        cfg = self._config
        bb_b = ctx.h1.bb_percent_b
        if bb_b < cfg.bb_buy_threshold:
            return SignalType.BUY
        if bb_b > cfg.bb_sell_threshold:
            return SignalType.SELL
        return None

    def _check_sr_or_liquidity(
        self, ctx: MarketContext, direction: SignalType,
    ) -> bool:
        """サポレジ到達 or 流動性グラブを確認。"""
        pa = ctx.price_action
        if direction == SignalType.BUY:
            return (
                pa.at_support
                or ctx.h4.liquidity_grab_bullish
            )
        return (
            pa.at_resistance
            or ctx.h4.liquidity_grab_bearish
        )

    def _check_trigger(
        self, ctx: MarketContext, direction: SignalType,
    ) -> bool:
        """反転足パターン or 流動性グラブを確認。"""
        pa = ctx.price_action
        threshold = 0.2
        if direction == SignalType.BUY:
            has_pattern = pa.bullish_score >= threshold
            has_grab = ctx.h4.liquidity_grab_bullish
        else:
            has_pattern = pa.bearish_score >= threshold
            has_grab = ctx.h4.liquidity_grab_bearish
        return has_pattern or has_grab

    def _calc_sl(
        self, ctx: MarketContext, direction: SignalType,
    ) -> float:
        """レンジ外端 + ATRバッファでSLを計算。"""
        cfg = self._config
        buf = ctx.h1.atr * cfg.sl_atr_buffer
        if direction == SignalType.BUY:
            # BB下限の外側
            return ctx.h1.bb_lower - buf
        # BB上限の外側
        return ctx.h1.bb_upper + buf

    def _calc_tp(
        self, ctx: MarketContext, direction: SignalType,
    ) -> float:
        """反対側レンジ境界の70%をTPに設定。"""
        cfg = self._config
        if direction == SignalType.BUY:
            # BB中央〜上限の間
            range_target = (
                ctx.h1.bb_upper - ctx.current_price
            )
            return (
                ctx.current_price
                + range_target * cfg.tp_range_pct
            )
        else:
            range_target = (
                ctx.current_price - ctx.h1.bb_lower
            )
            return (
                ctx.current_price
                - range_target * cfg.tp_range_pct
            )

    def _calc_confidence(
        self, ctx: MarketContext, direction: SignalType,
    ) -> float:
        """確信度を計算。

        4要素の重み付きスコア:
        - 流動性グラブ
        - 反転足品質
        - RSIダイバージェンス（簡易判定）
        - BB極値度
        """
        cfg = self._config
        total = 0.0

        # 流動性グラブ
        if direction == SignalType.BUY:
            grab = 1.0 if ctx.h4.liquidity_grab_bullish else 0.0
        else:
            grab = 1.0 if ctx.h4.liquidity_grab_bearish else 0.0
        total += cfg.weight_liquidity_grab * grab

        # 反転足品質
        if direction == SignalType.BUY:
            rev_q = min(1.0, ctx.price_action.bullish_score)
        else:
            rev_q = min(1.0, ctx.price_action.bearish_score)
        total += cfg.weight_reversal_quality * rev_q

        # RSIダイバージェンス簡易判定
        # 買い: RSIが売られすぎ圏
        # 売り: RSIが買われすぎ圏
        if direction == SignalType.BUY:
            rsi_score = max(
                0, min(1, (40 - ctx.h1.rsi) / 20),
            )
        else:
            rsi_score = max(
                0, min(1, (ctx.h1.rsi - 60) / 20),
            )
        total += cfg.weight_rsi_divergence * rsi_score

        # BB極値度: %Bが極端なほど高スコア
        bb_b = ctx.h1.bb_percent_b
        if direction == SignalType.BUY:
            bb_score = max(
                0, min(1, (cfg.bb_buy_threshold - bb_b) / cfg.bb_buy_threshold),
            )
        else:
            bb_score = max(
                0,
                min(
                    1,
                    (bb_b - cfg.bb_sell_threshold)
                    / (1.0 - cfg.bb_sell_threshold),
                ),
            )
        total += cfg.weight_bb_extreme * bb_score

        return max(0.0, min(1.0, total))
