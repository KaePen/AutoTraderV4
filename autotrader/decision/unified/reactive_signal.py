"""リアクティブモード シグナル生成

予測型（MTFコンセンサス）ではなく、実際の値動きに反応してエントリーする。
ドンチャンチャネル・ブレイクアウト + モメンタム確認で方向を決定。

設計原則:
- 方向を予測しない。動いた方向に乗る。
- ブレイクアウト検出 → ADX/ATRで確認 → エントリー
- 短期TF（M5/M15）のみ使用。上位TFの遅延に依存しない。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from autotrader.core.enums import SignalType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReactiveConfig:
    """リアクティブモード設定

    Attributes:
        donchian_period: ドンチャンチャネル期間（足数）
        atr_period: ATR期間
        adx_min: ブレイクアウト許可のADX最小値（トレンド確認）
        momentum_atr_mult: ブレイクアウト確認のATR倍率
        sl_atr_mult: SL幅のATR倍率（ブレイクアウト）
        tp_atr_mult: TP幅のATR倍率（ブレイクアウト）
        ema_fast: 短期EMA期間（方向フィルター）
        ema_slow: 長期EMA期間（方向フィルター）
        min_bar_range_atr: 直近バーの最小レンジ（ATR比）
        cooldown_bars: エントリー後の再エントリー禁止足数
        swing_enabled: レンジスイングモード有効化
        swing_adx_max: スイング許可のADX最大値（レンジ判定）
        swing_rsi_oversold: RSI過売水準
        swing_rsi_overbought: RSI過買水準
        swing_sl_atr_mult: SL幅のATR倍率（スイング）
        swing_tp_atr_mult: TP幅のATR倍率（スイング）
    """

    donchian_period: int = 20
    atr_period: int = 14
    adx_min: float = 22.0
    momentum_atr_mult: float = 0.5
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.5
    ema_fast: int = 12
    ema_slow: int = 26
    min_bar_range_atr: float = 0.3
    cooldown_bars: int = 3
    # レンジスイング設定
    swing_enabled: bool = True
    swing_adx_max: float = 20.0
    swing_rsi_oversold: float = 30.0
    swing_rsi_overbought: float = 70.0
    swing_sl_atr_mult: float = 1.0
    swing_tp_atr_mult: float = 1.5


@dataclass
class ReactiveState:
    """リアクティブモード内部状態（足ごとに更新）"""

    bars_since_last_signal: int = 999
    last_signal_direction: SignalType = SignalType.HOLD
    # ドンチャンチャネル用の高値/安値リングバッファ
    high_buffer: list[float] = field(default_factory=list)
    low_buffer: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class ReactiveSignalResult:
    """リアクティブシグナル評価結果

    Attributes:
        direction: BUY/SELL/HOLD
        strength: シグナル強度 (0.0-1.0)
        sl_pips: 推奨SL (pips)
        tp_pips: 推奨TP (pips)
        rationale: 判断理由
        breakout_type: ブレイクアウトの種類
        adx: ADX値
        atr_pips: ATR (pips)
    """

    direction: SignalType
    strength: float
    sl_pips: float
    tp_pips: float
    rationale: str
    breakout_type: str = ""
    adx: float = 0.0
    atr_pips: float = 0.0


_HOLD = ReactiveSignalResult(
    direction=SignalType.HOLD,
    strength=0.0,
    sl_pips=0.0,
    tp_pips=0.0,
    rationale="",
)


class ReactiveSignalGenerator:
    """リアクティブモード シグナル生成器

    ドンチャンチャネル・ブレイクアウトをベースに、
    ADX/ATR/EMAで確認してからエントリーする。

    使い方:
        gen = ReactiveSignalGenerator(config, pip_unit=0.01)
        result = gen.evaluate(row)  # row: pd.Series with OHLC + indicators
    """

    def __init__(
        self,
        config: ReactiveConfig | None = None,
        pip_unit: float = 0.01,
    ) -> None:
        self._config = config or ReactiveConfig()
        self._pip_unit = pip_unit
        self._state = ReactiveState()

    def reset(self) -> None:
        """状態リセット（年またぎ等）"""
        self._state = ReactiveState()

    def evaluate(
        self,
        row: pd.Series,
        current_time: pd.Timestamp | None = None,
    ) -> ReactiveSignalResult:
        """1足分のデータを評価してシグナルを返す

        Args:
            row: OHLC + インジケータを含むデータ行
            current_time: 現在時刻（ログ用）

        Returns:
            ReactiveSignalResult
        """
        cfg = self._config
        state = self._state

        # OHLC取得
        close = float(row.get("close", 0))
        high = float(row.get("high", 0))
        low = float(row.get("low", 0))
        if close <= 0:
            return _HOLD

        # リングバッファ更新
        state.high_buffer.append(high)
        state.low_buffer.append(low)
        if len(state.high_buffer) > cfg.donchian_period:
            state.high_buffer.pop(0)
            state.low_buffer.pop(0)

        # クールダウン
        state.bars_since_last_signal += 1
        if state.bars_since_last_signal < cfg.cooldown_bars:
            return _HOLD

        # データ不足
        if len(state.high_buffer) < cfg.donchian_period:
            return _HOLD

        # --- インジケータ取得 ---
        atr = float(row.get("atr_14", 0) or 0)
        adx = float(row.get("adx", 0) or 0)
        ema_fast = float(row.get("ema_12", 0) or 0)
        ema_slow = float(row.get("ema_26", 0) or 0)
        macd_hist = float(row.get("macd_histogram", 0) or 0)

        if atr <= 0:
            return _HOLD

        atr_pips = atr / self._pip_unit

        # --- ドンチャンチャネル ---
        # 直近N足の最高値/最安値（現在足を除く）
        dc_high = max(state.high_buffer[:-1])
        dc_low = min(state.low_buffer[:-1])

        # --- ブレイクアウト検出 ---
        breakout_buy = close > dc_high
        breakout_sell = close < dc_low

        # RSI + 価格偏差（SMA20からのATR比）
        rsi = float(row.get("rsi_14", 50) or 50)
        _sma20 = float(row.get("sma_20", 0) or 0)
        # SMA20からの乖離をATRで正規化
        if _sma20 > 0 and atr > 0:
            price_deviation = (close - _sma20) / atr
        else:
            price_deviation = 0.0

        if breakout_buy or breakout_sell:
            # ===== ブレイクアウトモード =====
            return self._evaluate_breakout(
                close, high, low, atr, atr_pips, adx,
                ema_fast, ema_slow, macd_hist,
                dc_high, dc_low,
                breakout_buy, state, cfg,
            )

        # ===== レンジスイングモード =====
        if cfg.swing_enabled:
            return self._evaluate_swing(
                close, high, low, atr, atr_pips, adx,
                price_deviation, rsi, macd_hist,
                state, cfg,
            )

        return _HOLD

    def _evaluate_breakout(
        self,
        close: float, high: float, low: float,
        atr: float, atr_pips: float, adx: float,
        ema_fast: float, ema_slow: float, macd_hist: float,
        dc_high: float, dc_low: float,
        breakout_buy: bool,
        state: ReactiveState, cfg: ReactiveConfig,
    ) -> ReactiveSignalResult:
        """ブレイクアウトシグナル評価"""
        if breakout_buy:
            direction = SignalType.BUY
            breakout_type = "DC_HIGH_BREAK"
        else:
            direction = SignalType.SELL
            breakout_type = "DC_LOW_BREAK"

        # ADXトレンド確認
        if adx < cfg.adx_min:
            return _HOLD

        # EMA方向確認
        if ema_fast > 0 and ema_slow > 0:
            if direction == SignalType.BUY and ema_fast < ema_slow:
                return _HOLD
            if direction == SignalType.SELL and ema_fast > ema_slow:
                return _HOLD

        # モメンタム確認
        bar_range = high - low
        if bar_range < atr * cfg.min_bar_range_atr:
            return _HOLD
        if direction == SignalType.BUY and macd_hist < 0:
            return _HOLD
        if direction == SignalType.SELL and macd_hist > 0:
            return _HOLD

        # シグナル強度
        if direction == SignalType.BUY:
            breakout_dist = close - dc_high
        else:
            breakout_dist = dc_low - close
        momentum_ratio = breakout_dist / atr if atr > 0 else 0
        strength = min(
            1.0, 0.5 + momentum_ratio * 0.5 + (adx - cfg.adx_min) / 40
        )

        sl_pips = round(atr_pips * cfg.sl_atr_mult, 1)
        tp_pips = round(atr_pips * cfg.tp_atr_mult, 1)
        sl_pips = max(sl_pips, 10.0)
        tp_pips = max(tp_pips, sl_pips * 1.2)

        # --- 状態更新 ---
        state.bars_since_last_signal = 0
        state.last_signal_direction = direction

        rationale = (
            f"REACTIVE {breakout_type}: "
            f"close={close:.3f} vs dc={'high' if breakout_buy else 'low'}="
            f"{dc_high if breakout_buy else dc_low:.3f}, "
            f"ADX={adx:.1f}, ATR={atr_pips:.1f}p, "
            f"EMA{'↑' if ema_fast > ema_slow else '↓'}, "
            f"MACD_H={macd_hist:.5f}"
        )

        return ReactiveSignalResult(
            direction=direction,
            strength=strength,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            rationale=rationale,
            breakout_type=breakout_type,
            adx=adx,
            atr_pips=atr_pips,
        )

    def _evaluate_swing(
        self,
        close: float, high: float, low: float,
        atr: float, atr_pips: float, adx: float,
        price_deviation: float, rsi: float, macd_hist: float,
        state: ReactiveState, cfg: ReactiveConfig,
    ) -> ReactiveSignalResult:
        """レンジスイングシグナル評価

        SMA20からの乖離(ATR正規化) + RSI極値からの反転に乗る。
        レンジ相場（ADX低い）で発動。

        price_deviation: (close - SMA20) / ATR
          -2.0以下 = SMA20から2ATR下方乖離 → BUY候補
          +2.0以上 = SMA20から2ATR上方乖離 → SELL候補
        """
        # レンジ確認: ADXが低い = トレンドなし
        if adx > cfg.swing_adx_max:
            return _HOLD

        direction = SignalType.HOLD
        swing_type = ""

        # SMA20から下方乖離 + RSI過売 → BUY
        if (
            price_deviation <= -1.5
            and rsi <= cfg.swing_rsi_oversold
        ):
            direction = SignalType.BUY
            swing_type = "SWING_OVERSOLD"

        # SMA20から上方乖離 + RSI過買 → SELL
        elif (
            price_deviation >= 1.5
            and rsi >= cfg.swing_rsi_overbought
        ):
            direction = SignalType.SELL
            swing_type = "SWING_OVERBOUGHT"

        if direction == SignalType.HOLD:
            return _HOLD

        # 極端な逆行モメンタムのみブロック
        if direction == SignalType.BUY and macd_hist < -atr * 2:
            return _HOLD
        if direction == SignalType.SELL and macd_hist > atr * 2:
            return _HOLD

        # シグナル強度（乖離度 + RSI極端さ）
        dev_strength = min(1.0, abs(price_deviation) / 3.0)
        if direction == SignalType.BUY:
            rsi_strength = max(0, (cfg.swing_rsi_oversold - rsi) / 20)
        else:
            rsi_strength = max(0, (rsi - cfg.swing_rsi_overbought) / 20)
        strength = min(1.0, 0.4 + dev_strength * 0.3 + rsi_strength * 0.3)

        # スイング用SL/TP（タイトに設定）
        sl_pips = round(atr_pips * cfg.swing_sl_atr_mult, 1)
        tp_pips = round(atr_pips * cfg.swing_tp_atr_mult, 1)
        sl_pips = max(sl_pips, 8.0)
        tp_pips = max(tp_pips, sl_pips * 1.0)

        # 状態更新
        state.bars_since_last_signal = 0
        state.last_signal_direction = direction

        rationale = (
            f"REACTIVE {swing_type}: "
            f"dev={price_deviation:+.2f}ATR, RSI={rsi:.1f}, "
            f"ADX={adx:.1f}(range), ATR={atr_pips:.1f}p, "
            f"MACD_H={macd_hist:.5f}"
        )

        return ReactiveSignalResult(
            direction=direction,
            strength=strength,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            rationale=rationale,
            breakout_type=swing_type,
            adx=adx,
            atr_pips=atr_pips,
        )
