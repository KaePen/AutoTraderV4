"""レジーム分類器モジュール。

ヒステリシス付き状態マシンで市場レジームを分類。
危険状態(VOLATILE)への遷移は即座、安全復帰は慎重に行う。
"""

from __future__ import annotations

import logging
from enum import Enum

from autotrader.decision.v2.config import RegimeClassifierConfig
from autotrader.decision.v2.market_context import MarketContext

logger = logging.getLogger(__name__)


class MarketRegimeV2(str, Enum):
    """V2市場レジーム。

    Attributes:
        TRENDING: 明確なトレンド（ADX高+MA整列）。
        RANGING: レンジ相場（ADX低+ATR安定）。
        QUIET: 低ボラティリティ（ATR低）。
        VOLATILE: 高ボラティリティ（ATR極高）。
    """

    TRENDING = "TRENDING"
    RANGING = "RANGING"
    QUIET = "QUIET"
    VOLATILE = "VOLATILE"


class RegimeClassifier:
    """ヒステリシス付きレジーム分類器。

    状態遷移に複数足の確認を要求することで
    ノイズによる誤分類を防止する。

    原則: 危険状態(VOLATILE)への遷移は即座、
    安全状態への復帰は慎重に。

    Args:
        config: 分類器設定。Noneの場合デフォルト値。
    """

    def __init__(
        self, config: RegimeClassifierConfig | None = None,
    ) -> None:
        self._config = config or RegimeClassifierConfig()
        self._current: MarketRegimeV2 = MarketRegimeV2.QUIET
        self._counters: dict[MarketRegimeV2, int] = {}

    @property
    def current_regime(self) -> MarketRegimeV2:
        """現在のレジーム。"""
        return self._current

    def classify(self, ctx: MarketContext) -> MarketRegimeV2:
        """市場コンテキストからレジームを分類。

        ヒステリシスにより、候補レジームが必要足数連続で
        検出されるまで遷移しない。

        Args:
            ctx: 現在の市場コンテキスト。

        Returns:
            現在のレジーム（遷移確定後の状態）。
        """
        candidate = self._detect_candidate(ctx)

        # 現在レジームと同一 → カウンタリセット
        if candidate == self._current:
            self._counters.clear()
            return self._current

        # 遷移候補のカウントを更新
        count = self._counters.get(candidate, 0) + 1
        self._counters[candidate] = count

        # 他の候補をリセット
        for regime in MarketRegimeV2:
            if regime not in (candidate, self._current):
                self._counters.pop(regime, None)

        # 必要足数に達したら遷移
        required = self._required_bars(
            self._current, candidate,
        )
        if count >= required:
            prev = self._current
            self._current = candidate
            self._counters.clear()
            logger.debug(
                "レジーム遷移: %s → %s (%d足確認)",
                prev.value,
                candidate.value,
                required,
            )

        return self._current

    def reset(self) -> None:
        """状態をリセット（年初等で使用）。"""
        self._current = MarketRegimeV2.QUIET
        self._counters.clear()

    def _detect_candidate(
        self, ctx: MarketContext,
    ) -> MarketRegimeV2:
        """現在の指標値から候補レジームを判定。

        判定優先順位:
        1. VOLATILE (norm_ATR > 閾値) - 最優先
        2. TRENDING (ADX高 + MA整列)
        3. RANGING (ADX低 + ATR安定)
        4. QUIET (上記いずれにも該当しない)
        """
        cfg = self._config
        adx = ctx.h1.adx
        norm_atr = ctx.h1.normalized_atr
        ma_align = ctx.ma_alignment

        # 1. VOLATILE: 高ボラティリティ
        if norm_atr > cfg.volatile_atr_threshold:
            return MarketRegimeV2.VOLATILE

        # 2. TRENDING: ADX高 + MA整列
        if (
            adx > cfg.trending_adx_threshold
            and abs(ma_align) > cfg.trending_ma_align_threshold
        ):
            return MarketRegimeV2.TRENDING

        # 3. RANGING: ADX低 + ATR安定帯
        if (
            adx < cfg.ranging_adx_threshold
            and cfg.ranging_atr_lower
            <= norm_atr
            <= cfg.ranging_atr_upper
        ):
            return MarketRegimeV2.RANGING

        # 4. QUIET: デフォルト
        return MarketRegimeV2.QUIET

    def _required_bars(
        self,
        from_regime: MarketRegimeV2,
        to_regime: MarketRegimeV2,
    ) -> int:
        """遷移に必要な確認足数を取得。"""
        cfg = self._config
        _map = {
            # QUIET → 他
            (MarketRegimeV2.QUIET, MarketRegimeV2.TRENDING):
                cfg.quiet_to_trending_bars,
            (MarketRegimeV2.QUIET, MarketRegimeV2.RANGING):
                cfg.quiet_to_ranging_bars,
            (MarketRegimeV2.QUIET, MarketRegimeV2.VOLATILE):
                cfg.quiet_to_volatile_bars,
            # TRENDING → 他
            (MarketRegimeV2.TRENDING, MarketRegimeV2.RANGING):
                cfg.trending_to_ranging_bars,
            (MarketRegimeV2.TRENDING, MarketRegimeV2.VOLATILE):
                cfg.trending_to_volatile_bars,
            (MarketRegimeV2.TRENDING, MarketRegimeV2.QUIET):
                cfg.trending_to_quiet_bars,
            # RANGING → 他
            (MarketRegimeV2.RANGING, MarketRegimeV2.TRENDING):
                cfg.ranging_to_trending_bars,
            (MarketRegimeV2.RANGING, MarketRegimeV2.VOLATILE):
                cfg.ranging_to_volatile_bars,
            (MarketRegimeV2.RANGING, MarketRegimeV2.QUIET):
                cfg.ranging_to_quiet_bars,
            # VOLATILE → 他（安全復帰は慎重）
            (MarketRegimeV2.VOLATILE, MarketRegimeV2.TRENDING):
                cfg.volatile_to_trending_bars,
            (MarketRegimeV2.VOLATILE, MarketRegimeV2.RANGING):
                cfg.volatile_to_ranging_bars,
            (MarketRegimeV2.VOLATILE, MarketRegimeV2.QUIET):
                cfg.volatile_to_quiet_bars,
        }
        return _map.get((from_regime, to_regime), 3)
