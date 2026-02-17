"""トレードパラメータの単一ソース."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradingParams:
    """全トレードパラメータの一元管理.

    Attributes:
        spread_pips: スプレッド（pips）
        default_sl_pips: デフォルトSL（pips）
        default_tp_pips: デフォルトTP（pips）
        pip_value: 1pipあたりの価値（円）
        min_lot: 最小ロット
        max_lot: 最大ロット
        slippage_pips: スリッページ（pips）
        commission_per_lot: ロットあたり手数料
    """

    spread_pips: float = 1.5
    default_sl_pips: float = 20.0
    default_tp_pips: float = 40.0
    pip_value: float = 100.0
    min_lot: float = 0.01
    max_lot: float = 10.0
    slippage_pips: float = 0.5
    commission_per_lot: float = 0.0


# デフォルトインスタンス
DEFAULT_TRADING_PARAMS = TradingParams()
