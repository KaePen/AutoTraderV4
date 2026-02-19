"""MT5アダプタパッケージ

MT5接続・データ取得・注文実行の統合インターフェース。
"""

from __future__ import annotations

from autotrader.adapters.mt5.config import MT5Config
from autotrader.adapters.mt5.connection import (
    DirectTransport,
    MT5ConnectionManager,
    MT5Transport,
)
from autotrader.adapters.mt5.converters import (
    mt5_account_to_entity,
    mt5_position_to_entity,
    mt5_rates_to_dataframe,
    mt5_symbol_to_entity,
    signal_to_mt5_request,
)
from autotrader.adapters.mt5.exceptions import (
    MT5ConnectionError,
    MT5DataError,
    MT5Error,
    MT5ExecutionError,
)

__all__ = [
    "MT5Config",
    "MT5Transport",
    "DirectTransport",
    "MT5ConnectionManager",
    "mt5_account_to_entity",
    "mt5_symbol_to_entity",
    "mt5_position_to_entity",
    "mt5_rates_to_dataframe",
    "signal_to_mt5_request",
    "MT5Error",
    "MT5ConnectionError",
    "MT5ExecutionError",
    "MT5DataError",
]
