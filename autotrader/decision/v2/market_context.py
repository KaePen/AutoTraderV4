"""市場コンテキストモジュール。

各時間足の指標・構造データを統合し、
トレード判断に必要な市場状態を提供する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# データクラス: 時間足別の市場状態
# ============================================================


@dataclass(frozen=True)
class H1Indicators:
    """H1テクニカル指標。

    エントリー判定に使用するモメンタム・
    ボラティリティ・トレンド指標を保持。
    """

    rsi: float
    macd: float
    macd_signal: float
    macd_histogram: float
    atr: float
    adx: float
    plus_di: float
    minus_di: float
    bb_upper: float
    bb_lower: float
    bb_percent_b: float
    bb_width: float
    bb_squeeze: float
    ema_20: float
    ema_50: float
    normalized_atr: float
    stoch_k: float
    stoch_d: float


@dataclass(frozen=True)
class StructureState:
    """市場構造状態（H4/D1共通）。

    BOS/CHoCH、スイングポイント、流動性グラブなど
    SMC（Smart Money Concepts）関連の構造情報。
    """

    trend_state: str  # BULLISH/BEARISH/CONSOLIDATION等
    bos_signal: int  # 1=強気BOS, -1=弱気BOS, 0=なし
    choch_signal: int  # 1=強気CHoCH, -1=弱気CHoCH, 0=なし
    bars_since_bos: int
    bars_since_choch: int
    last_swing_high: float
    last_swing_low: float
    structure_direction: int  # 1=上昇, -1=下降, 0=中立
    liquidity_grab_bullish: bool
    liquidity_grab_bearish: bool
    adx: float


@dataclass(frozen=True)
class PriceActionState:
    """プライスアクション状態。

    ローソク足パターンとサポレジ到達情報。
    """

    candle_pattern: str  # CandlePattern名
    bullish_score: float
    bearish_score: float
    at_support: bool
    at_resistance: bool


@dataclass(frozen=True)
class MarketContext:
    """統合市場コンテキスト。

    3時間足（H1/H4/D1）の指標・構造データと
    プライスアクション情報を統合した不変オブジェクト。
    RegimeClassifier, Strategy, RiskManager が参照する。

    Attributes:
        current_price: 現在のクローズ価格。
        current_time: 現在足のタイムスタンプ。
        h1: H1テクニカル指標。
        h4: H4市場構造状態。
        d1: D1市場構造状態。
        price_action: プライスアクション情報。
        ma_alignment: MA整列度(-1〜1)。
        volatility_trend: ボラティリティトレンド。
        spread_pips: 現在のスプレッド(pips)。
    """

    current_price: float
    current_time: datetime
    h1: H1Indicators
    h4: StructureState
    d1: StructureState
    price_action: PriceActionState
    ma_alignment: float
    volatility_trend: float
    spread_pips: float = 1.5


# ============================================================
# MarketContextBuilder: DataFrame行からコンテキスト構築
# ============================================================

# カラム名マッピング（PrecomputeEngine出力 → 内部名）
_H1_COLUMNS = {
    "rsi_14": "rsi",
    "macd": "macd",
    "macd_signal": "macd_signal",
    "macd_histogram": "macd_histogram",
    "atr_14": "atr",
    "adx_14": "adx",
    "plus_di_14": "plus_di",
    "minus_di_14": "minus_di",
    "bb_upper": "bb_upper",
    "bb_lower": "bb_lower",
    "bb_percent_b": "bb_percent_b",
    "bb_width": "bb_width",
    "bb_squeeze": "bb_squeeze",
    "ema_20": "ema_20",
    "ema_50": "ema_50",
    "normalized_atr": "normalized_atr",
    "stoch_k": "stoch_k",
    "stoch_d": "stoch_d",
}

_STRUCTURE_COLUMNS = {
    "trend_state_smc": "trend_state",
    "bos_signal": "bos_signal",
    "choch_signal": "choch_signal",
    "bars_since_bos": "bars_since_bos",
    "bars_since_choch": "bars_since_choch",
    "last_swing_high": "last_swing_high",
    "last_swing_low": "last_swing_low",
    "structure_direction": "structure_direction",
    "liquidity_grab_bullish": "liquidity_grab_bullish",
    "liquidity_grab_bearish": "liquidity_grab_bearish",
    "adx_14": "adx",
}

_PA_COLUMNS = {
    "candle_pattern": "candle_pattern",
    "pa_bullish_score": "bullish_score",
    "pa_bearish_score": "bearish_score",
    "at_support": "at_support",
    "at_resistance": "at_resistance",
}


def _safe_get(
    row: pd.Series,
    col: str,
    default: float | int | bool | str = 0.0,
) -> float | int | bool | str:
    """DataFrame行から安全に値を取得。"""
    val = row.get(col, default)
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return val


def _extract_h1(row: pd.Series) -> H1Indicators:
    """H1行からH1Indicatorsを構築。"""
    vals = {}
    for src, dst in _H1_COLUMNS.items():
        vals[dst] = float(_safe_get(row, src, 0.0))
    return H1Indicators(**vals)


def _extract_structure(row: pd.Series) -> StructureState:
    """構造行からStructureStateを構築。"""
    return StructureState(
        trend_state=str(
            _safe_get(row, "trend_state_smc", "CONSOLIDATION")
        ),
        bos_signal=int(_safe_get(row, "bos_signal", 0)),
        choch_signal=int(_safe_get(row, "choch_signal", 0)),
        bars_since_bos=int(
            _safe_get(row, "bars_since_bos", 9999)
        ),
        bars_since_choch=int(
            _safe_get(row, "bars_since_choch", 9999)
        ),
        last_swing_high=float(
            _safe_get(row, "last_swing_high", 0.0)
        ),
        last_swing_low=float(
            _safe_get(row, "last_swing_low", 0.0)
        ),
        structure_direction=int(
            _safe_get(row, "structure_direction", 0)
        ),
        liquidity_grab_bullish=bool(
            _safe_get(row, "liquidity_grab_bullish", False)
        ),
        liquidity_grab_bearish=bool(
            _safe_get(row, "liquidity_grab_bearish", False)
        ),
        adx=float(_safe_get(row, "adx_14", 0.0)),
    )


def _extract_price_action(row: pd.Series) -> PriceActionState:
    """H1行からPriceActionStateを構築。"""
    return PriceActionState(
        candle_pattern=str(
            _safe_get(row, "candle_pattern", "NONE")
        ),
        bullish_score=float(
            _safe_get(row, "pa_bullish_score", 0.0)
        ),
        bearish_score=float(
            _safe_get(row, "pa_bearish_score", 0.0)
        ),
        at_support=bool(
            _safe_get(row, "at_support", False)
        ),
        at_resistance=bool(
            _safe_get(row, "at_resistance", False)
        ),
    )


class MarketContextBuilder:
    """市場コンテキストビルダー。

    プリコンピュート済みのDataFrame群から
    指定時刻のMarketContextを構築する。

    Args:
        market_data: 時間足→DataFrame のマッピング。
        entry_tf: エントリー時間足キー(例: "H1")。
        structure_tf: 構造分析時間足キー(例: "H4")。
        context_tf: 上位コンテキスト時間足キー(例: "D1")。
    """

    def __init__(
        self,
        market_data: dict[str, pd.DataFrame],
        entry_tf: str = "H1",
        structure_tf: str = "H4",
        context_tf: str = "D1",
    ) -> None:
        self._data = market_data
        self._entry_tf = entry_tf
        self._structure_tf = structure_tf
        self._context_tf = context_tf
        # 時間足インデックスをキャッシュ
        self._indices: dict[str, pd.DatetimeIndex] = {}
        for tf, df in market_data.items():
            if not df.empty:
                idx = pd.DatetimeIndex(df.index)
                self._indices[tf] = idx

    def build(
        self,
        current_time: datetime | pd.Timestamp,
        spread_pips: float = 1.5,
    ) -> MarketContext | None:
        """指定時刻の市場コンテキストを構築。

        Args:
            current_time: 現在足のタイムスタンプ。
            spread_pips: 現在のスプレッド(pips)。

        Returns:
            MarketContext。データ不足時はNone。
        """
        ts = pd.Timestamp(current_time)

        # 各時間足の現在行を取得
        h1_row = self._get_row(self._entry_tf, ts)
        h4_row = self._get_row(self._structure_tf, ts)
        d1_row = self._get_row(self._context_tf, ts)

        if h1_row is None:
            return None

        # H4/D1が無い場合はデフォルト構造
        h1 = _extract_h1(h1_row)
        h4 = (
            _extract_structure(h4_row)
            if h4_row is not None
            else _default_structure()
        )
        d1 = (
            _extract_structure(d1_row)
            if d1_row is not None
            else _default_structure()
        )
        pa = _extract_price_action(h1_row)

        return MarketContext(
            current_price=float(h1_row.get("close", 0.0)),
            current_time=ts.to_pydatetime(),
            h1=h1,
            h4=h4,
            d1=d1,
            price_action=pa,
            ma_alignment=float(
                _safe_get(h1_row, "ma_alignment", 0.0)
            ),
            volatility_trend=float(
                _safe_get(h1_row, "volatility_trend", 0.0)
            ),
            spread_pips=spread_pips,
        )

    def _get_row(
        self,
        tf: str,
        ts: pd.Timestamp,
    ) -> pd.Series | None:
        """指定時間足で ts 以前の最新行を取得。"""
        if tf not in self._data:
            return None

        df = self._data[tf]
        if df.empty:
            return None

        idx = self._indices.get(tf)
        if idx is None:
            return None

        # ts以前の最新行を検索
        mask = idx <= ts
        if not mask.any():
            return None

        pos = mask.nonzero()[0][-1]
        return df.iloc[pos]


def _default_structure() -> StructureState:
    """デフォルトの構造状態。"""
    return StructureState(
        trend_state="CONSOLIDATION",
        bos_signal=0,
        choch_signal=0,
        bars_since_bos=9999,
        bars_since_choch=9999,
        last_swing_high=0.0,
        last_swing_low=0.0,
        structure_direction=0,
        liquidity_grab_bullish=False,
        liquidity_grab_bearish=False,
        adx=0.0,
    )
