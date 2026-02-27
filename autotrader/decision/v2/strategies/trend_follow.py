"""トレンドフォロー戦略。

TRENDINGレジーム時に使用。
H4構造がBULLISH/BEARISHの状態で、H1のプルバック
（EMA-50近傍）+ 反転足確認 + BOS直近でエントリー。
"""

from __future__ import annotations

import logging

from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import TrendFollowConfig
from autotrader.decision.v2.market_context import MarketContext
from autotrader.decision.v2.strategies.base import (
    V2EntrySignal,
    V2StrategyBase,
)

logger = logging.getLogger(__name__)


class TrendFollowStrategy(V2StrategyBase):
    """トレンドフォロー戦略。

    エントリー条件:
    1. H4構造がBULLISH or BEARISH
    2. H1でEMA-50近傍へのプルバック
    3. 反転ローソク足パターン確認
    4. BOS直近N足以内

    Args:
        config: 戦略設定。Noneでデフォルト値。
        pip_unit: 1pipの価格単位(USDJPY=0.01)。
    """

    def __init__(
        self,
        config: TrendFollowConfig | None = None,
        pip_unit: float = 0.01,
    ) -> None:
        self._config = config or TrendFollowConfig()
        self._pip_unit = pip_unit

    @property
    def name(self) -> str:
        return "TrendFollow"

    def evaluate(
        self, ctx: MarketContext,
    ) -> V2EntrySignal | None:
        """トレンドフォローのエントリー評価。"""
        cfg = self._config

        # --- 1. H4構造方向チェック ---
        direction = self._check_h4_structure(ctx)
        if direction is None:
            return None

        # --- 2. BOS鮮度チェック ---
        if not self._check_bos_freshness(ctx, direction):
            return None

        # --- 3. プルバックチェック ---
        ema_dist = self._check_pullback(ctx)
        if ema_dist is None:
            return None

        # --- 4. 反転足確認 ---
        if not self._check_reversal(ctx, direction):
            return None

        # --- 5. SL計算 ---
        sl_price = self._calc_sl(ctx, direction)
        sl_pips = (
            abs(ctx.current_price - sl_price)
            / self._pip_unit
        )
        if sl_pips > cfg.sl_max_pips:
            return None
        if sl_pips < 1.0:
            return None

        # --- 6. TP計算 ---
        risk = abs(ctx.current_price - sl_price)
        tp_price = self._calc_tp(
            ctx, direction, risk,
        )

        # --- 7. RR検証 ---
        tp_dist = abs(tp_price - ctx.current_price)
        if risk > 0 and tp_dist / risk < cfg.tp_min_rr:
            return None

        # --- 8. 確信度計算 ---
        confidence = self._calc_confidence(
            ctx, direction, ema_dist,
        )

        return V2EntrySignal(
            direction=direction,
            confidence=confidence,
            sl_price=sl_price,
            tp_price=tp_price,
            reasoning=(
                f"TrendFollow: H4={ctx.h4.trend_state}"
                f" BOS={ctx.h4.bars_since_bos}bars"
                f" pullback={ema_dist:.1f}ATR"
                f" SL={sl_pips:.0f}pips"
            ),
            strategy_name=self.name,
        )

    # -------------------------------------------------------
    # 内部チェックメソッド
    # -------------------------------------------------------

    def _check_h4_structure(
        self, ctx: MarketContext,
    ) -> SignalType | None:
        """H4構造からトレード方向を判定。"""
        ts = ctx.h4.trend_state
        if ts in ("BULLISH", "REVERSAL_BULLISH"):
            return SignalType.BUY
        if ts in ("BEARISH", "REVERSAL_BEARISH"):
            return SignalType.SELL
        return None

    def _check_bos_freshness(
        self, ctx: MarketContext, direction: SignalType,
    ) -> bool:
        """BOS発生からの経過足数を確認。"""
        cfg = self._config
        # BOS方向が一致し、鮮度が十分か
        bos = ctx.h4.bos_signal
        if direction == SignalType.BUY and bos <= 0:
            # 直近のBOSがなくても構造方向で補完
            if ctx.h4.structure_direction <= 0:
                return False
        elif direction == SignalType.SELL and bos >= 0:
            if ctx.h4.structure_direction >= 0:
                return False
        return ctx.h4.bars_since_bos <= cfg.bos_max_bars

    def _check_pullback(
        self, ctx: MarketContext,
    ) -> float | None:
        """EMA-50近傍へのプルバックを確認。

        Returns:
            プルバック距離(ATR倍率)。条件外はNone。
        """
        cfg = self._config
        if ctx.h1.atr <= 0:
            return None
        dist = (
            abs(ctx.current_price - ctx.h1.ema_50)
            / ctx.h1.atr
        )
        if dist > cfg.pullback_atr_distance:
            return None
        return dist

    def _check_reversal(
        self, ctx: MarketContext, direction: SignalType,
    ) -> bool:
        """反転ローソク足パターンを確認。"""
        pa = ctx.price_action
        threshold = 0.3
        if direction == SignalType.BUY:
            return pa.bullish_score >= threshold
        return pa.bearish_score >= threshold

    def _calc_sl(
        self, ctx: MarketContext, direction: SignalType,
    ) -> float:
        """H4スイングポイントからSL価格を計算。"""
        cfg = self._config
        buf = ctx.h1.atr * cfg.sl_atr_buffer
        if direction == SignalType.BUY:
            return ctx.h4.last_swing_low - buf
        return ctx.h4.last_swing_high + buf

    def _calc_tp(
        self,
        ctx: MarketContext,
        direction: SignalType,
        risk: float,
    ) -> float:
        """TP価格を計算（デフォルトRR or 構造レベル）。"""
        cfg = self._config
        tp_dist = risk * cfg.tp_default_rr
        if direction == SignalType.BUY:
            # H4スイングハイを目標に考慮
            struct_tp = ctx.h4.last_swing_high
            if struct_tp > ctx.current_price:
                struct_dist = struct_tp - ctx.current_price
                tp_dist = max(tp_dist, struct_dist)
            return ctx.current_price + tp_dist
        else:
            struct_tp = ctx.h4.last_swing_low
            if struct_tp < ctx.current_price:
                struct_dist = ctx.current_price - struct_tp
                tp_dist = max(tp_dist, struct_dist)
            return ctx.current_price - tp_dist

    def _calc_confidence(
        self,
        ctx: MarketContext,
        direction: SignalType,
        ema_dist: float,
    ) -> float:
        """確信度を計算。

        4要素の重み付きスコア:
        - 構造整合 (H4-D1方向一致)
        - BOS鮮度
        - 反転足品質
        - モメンタム (RSI + MACD)
        """
        cfg = self._config
        total = 0.0

        # 構造整合: H4とD1の方向一致
        h4_dir = ctx.h4.structure_direction
        d1_dir = ctx.d1.structure_direction
        align = 1.0 if h4_dir == d1_dir and h4_dir != 0 else 0.3
        total += cfg.weight_structure_align * align

        # BOS鮮度: 直近ほど高スコア
        freshness = max(
            0.0,
            1.0 - ctx.h4.bars_since_bos / cfg.bos_max_bars,
        )
        total += cfg.weight_bos_freshness * freshness

        # 反転足品質
        if direction == SignalType.BUY:
            rev_q = min(1.0, ctx.price_action.bullish_score)
        else:
            rev_q = min(1.0, ctx.price_action.bearish_score)
        total += cfg.weight_reversal_quality * rev_q

        # モメンタム
        momentum = self._momentum_score(ctx, direction)
        total += cfg.weight_momentum * momentum

        return max(0.0, min(1.0, total))

    @staticmethod
    def _momentum_score(
        ctx: MarketContext, direction: SignalType,
    ) -> float:
        """RSI + MACDからモメンタムスコアを計算。"""
        if direction == SignalType.BUY:
            # RSI: 30-70 → 0-1
            rsi_s = max(0, min(1, (ctx.h1.rsi - 30) / 40))
            macd_s = 1.0 if ctx.h1.macd_histogram > 0 else 0.3
        else:
            rsi_s = max(0, min(1, (70 - ctx.h1.rsi) / 40))
            macd_s = 1.0 if ctx.h1.macd_histogram < 0 else 0.3
        return (rsi_s + macd_s) / 2.0
