"""V2トレードボットモジュール。

市場構造状態マシンに基づくトレードエンジンの
メインエントリーポイント。

フロー:
1. MarketContextBuilder で市場状態構築
2. RegimeClassifier でレジーム分類
3. V2RiskManager で NoTrade チェック
4. StrategyDispatcher で戦略実行
5. V2RiskManager でシグナル検証
6. Signal エンティティ生成・返却
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

import pandas as pd

from autotrader.calculator.market_structure import (
    LiquidityAnalyzer,
    StructureAnalyzer,
    SwingAnalyzer,
)
from autotrader.calculator.technical.price_action import (
    PriceActionAnalyzer,
)
from autotrader.core.entities import Candle, Signal
from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import V2BotConfig
from autotrader.decision.v2.market_context import (
    MarketContextBuilder,
)
from autotrader.decision.v2.regime_classifier import (
    MarketRegimeV2,
    RegimeClassifier,
)
from autotrader.decision.v2.risk_manager import (
    V2BotState,
    V2RiskManager,
)
from autotrader.decision.v2.strategy_dispatcher import (
    StrategyDispatcher,
)

logger = logging.getLogger(__name__)


class V2TradeBot:
    """V2トレードボット。

    市場構造状態マシンに基づくトレードエンジン。
    3時間足(H1/H4/D1)の構造分析とレジーム分類により
    適切な戦略を自動選択してシグナルを生成する。

    Args:
        config: V2ボット設定。
    """

    def __init__(
        self,
        config: V2BotConfig | None = None,
    ) -> None:
        self._config = config or V2BotConfig()
        self._state = V2BotState()

        # コンポーネント初期化
        self._regime_clf = RegimeClassifier(
            self._config.regime,
        )
        self._risk_mgr = V2RiskManager(
            config=self._config.risk,
            pip_unit=self._config.pip_unit,
            pip_value=self._config.pip_value,
        )
        self._dispatcher = StrategyDispatcher(self._config)

        # データ関連
        self._ctx_builder: MarketContextBuilder | None = None
        self._market_data: dict[str, pd.DataFrame] = {}

        # SMC/PA計算器
        self._swing_analyzer = SwingAnalyzer(
            lookback=5, lookforward=2,
        )
        self._structure_analyzer = StructureAnalyzer(
            swing_analyzer=self._swing_analyzer,
        )
        self._liquidity_analyzer = LiquidityAnalyzer(
            swing_analyzer=self._swing_analyzer,
        )
        self._pa_analyzer = PriceActionAnalyzer()

    @property
    def state(self) -> V2BotState:
        """ボット状態。"""
        return self._state

    @property
    def current_regime(self) -> MarketRegimeV2:
        """現在のレジーム。"""
        return self._regime_clf.current_regime

    def set_market_data(
        self,
        data: dict[str, pd.DataFrame],
    ) -> None:
        """市場データを設定・前処理。

        PrecomputeEngine出力のDataFrameに対し、
        SMC/PriceAction列が不足していれば追加する。

        Args:
            data: 時間足→DataFrame マッピング。
        """
        self._market_data = {}

        for tf, df in data.items():
            if df.empty:
                self._market_data[tf] = df
                continue
            processed = self._ensure_columns(df, tf)
            self._market_data[tf] = processed

        # コンテキストビルダーを構築
        self._ctx_builder = MarketContextBuilder(
            market_data=self._market_data,
            entry_tf=self._config.entry_timeframe,
            structure_tf=self._config.structure_timeframe,
            context_tf=self._config.context_timeframe,
        )

    def generate_signal(
        self,
        current_time: datetime | pd.Timestamp,
        candle: Candle | None = None,
        **kwargs: object,
    ) -> Signal | None:
        """シグナル生成のメインエントリーポイント。

        Args:
            current_time: 現在足のタイムスタンプ。
            candle: 現在のローソク足（互換性用）。
            **kwargs: 追加引数（互換性用、無視）。

        Returns:
            エントリー条件を満たす場合Signal、
            それ以外はNone。
        """
        if self._ctx_builder is None:
            return None

        # --- 1. MarketContext構築 ---
        spread = self._config.risk.max_spread_pips
        ctx = self._ctx_builder.build(
            current_time, spread_pips=spread,
        )
        if ctx is None:
            return None

        # --- 2. レジーム分類 ---
        regime = self._regime_clf.classify(ctx)

        # --- 3. Breakout用QUIET足カウンタ更新 ---
        self._dispatcher.breakout_strategy.update_quiet_bars(
            regime == MarketRegimeV2.QUIET,
        )

        # --- 4. NoTradeチェック ---
        no_trade = self._risk_mgr.check_no_trade(
            ctx, self._state,
        )
        if no_trade is not None:
            return None

        # --- 5. VOLATILE → NoTrade ---
        if regime == MarketRegimeV2.VOLATILE:
            return None

        # --- 6. 戦略ディスパッチ ---
        entry = self._dispatcher.dispatch(regime, ctx)
        if entry is None:
            return None

        # --- 7. 最低確信度チェック ---
        if entry.confidence < self._config.min_confidence:
            return None

        # --- 8. シグナル検証 ---
        if not self._risk_mgr.validate_signal(entry, ctx):
            logger.debug(
                "シグナル検証失敗: %s", entry.reasoning,
            )
            return None

        # --- 9. ロットサイズ計算 ---
        lot = self._risk_mgr.calculate_lot(
            entry, ctx, self._state,
        )

        # --- 10. Signalエンティティ生成 ---
        return Signal(
            signal_id=str(uuid.uuid4())[:8],
            symbol="USDJPY",
            timeframe=self._config.entry_timeframe,
            signal_type=entry.direction,
            confidence=entry.confidence,
            stop_loss=entry.sl_price,
            take_profit=entry.tp_price,
            reasoning=entry.reasoning,
            created_at=pd.Timestamp(current_time),
            regime=regime.value,
            mode=entry.strategy_name,
            lot=lot,
            indicators_snapshot={
                "adx": ctx.h1.adx,
                "rsi": ctx.h1.rsi,
                "atr": ctx.h1.atr,
                "bb_percent_b": ctx.h1.bb_percent_b,
                "normalized_atr": ctx.h1.normalized_atr,
                "h4_trend": ctx.h4.trend_state,
                "d1_trend": ctx.d1.trend_state,
            },
        )

    def update_trade_result(
        self, profit_pips: float,
    ) -> None:
        """トレード結果でボット状態を更新。

        Args:
            profit_pips: 損益(pips)。正=利益、負=損失。
        """
        if profit_pips >= 0:
            self._state.consecutive_losses = 0
            self._state.consecutive_wins += 1
        else:
            self._state.consecutive_wins = 0
            self._state.consecutive_losses += 1

    def update_equity(self, equity: float) -> None:
        """有効証拠金を更新。

        Args:
            equity: 現在の有効証拠金。
        """
        self._state.equity = equity
        if equity > self._state.peak_equity:
            self._state.peak_equity = equity

    def reset(self) -> None:
        """年初リセット。"""
        self._state = V2BotState()
        self._regime_clf.reset()

    # -------------------------------------------------------
    # 内部: 列の補完
    # -------------------------------------------------------

    def _ensure_columns(
        self, df: pd.DataFrame, tf: str,
    ) -> pd.DataFrame:
        """必要な列が不足していれば追加。

        SMC（BOS/CHoCH/Swing/Liquidity）と
        PriceAction列を補完する。
        """
        result = df.copy()

        # SMC列の補完
        _need_smc = (
            "bos_signal" not in result.columns
            or "bars_since_bos" not in result.columns
        )
        if _need_smc:
            try:
                smc_out = (
                    self._structure_analyzer.calculate_all(
                        result,
                    )
                )
                # calculate_allは列を落とすので
                # 必要列のみマージ
                _smc_cols = [
                    c for c in smc_out.columns
                    if c not in result.columns
                ]
                for c in _smc_cols:
                    result[c] = smc_out[c].values
            except Exception as e:
                logger.warning(
                    "%s: SMC計算失敗: %s", tf, e,
                )

        # 流動性グラブの補完
        if "liquidity_grab_bullish" not in result.columns:
            try:
                liq_out = (
                    self._liquidity_analyzer
                    .detect_liquidity_grab(result)
                )
                # detect_liquidity_grabは新DFを返すので
                # 新規列のみマージ
                _liq_cols = [
                    c for c in liq_out.columns
                    if c not in result.columns
                ]
                for c in _liq_cols:
                    result[c] = liq_out[c].values
            except Exception as e:
                logger.warning(
                    "%s: 流動性分析失敗: %s", tf, e,
                )

        # PriceAction列の補完（エントリーTFのみ）
        entry_tf = self._config.entry_timeframe
        if (
            tf == entry_tf
            and "candle_pattern" not in result.columns
        ):
            try:
                atr_col = "atr_14"
                atr = (
                    result[atr_col]
                    if atr_col in result.columns
                    else None
                )
                pa_out = self._pa_analyzer.analyze(
                    result, atr=atr,
                )
                # analyze()は新DFを返すので
                # 新規列のみマージ
                _pa_cols = [
                    c for c in pa_out.columns
                    if c not in result.columns
                ]
                for c in _pa_cols:
                    result[c] = pa_out[c].values
            except Exception as e:
                logger.warning(
                    "%s: PA分析失敗: %s", tf, e,
                )

        return result
