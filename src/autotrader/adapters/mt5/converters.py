"""MT5データ変換モジュール

MT5の辞書形式データとドメインエンティティ間の変換。
全関数はイミュータブル（新規オブジェクト返却）。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from autotrader.adapters.mt5.constants import (
    DEFAULT_DEVIATION,
    DEFAULT_MAGIC_NUMBER,
    ORDER_FILLING_FOK,
    ORDER_TYPE_BUY,
    ORDER_TYPE_SELL,
    POSITION_TYPE_BUY,
    TRADE_ACTION_DEAL,
)
from autotrader.core.entities import (
    AccountInfo,
    Position,
    Signal,
    SymbolInfo,
)
from autotrader.core.enums import SignalType


def mt5_account_to_entity(data: dict) -> AccountInfo:
    """MT5口座情報をAccountInfoエンティティに変換

    Args:
        data: MT5口座情報辞書

    Returns:
        AccountInfo: 口座情報エンティティ
    """
    return AccountInfo(
        balance=float(data.get("balance", 0)),
        equity=float(data.get("equity", 0)),
        margin=float(data.get("margin", 0)),
        free_margin=float(data.get("margin_free", 0)),
        margin_level=float(data.get("margin_level", 0)),
        profit=float(data.get("profit", 0)),
        login=int(data.get("login", 0)),
        server=str(data.get("server", "")),
        name=str(data.get("name", "")),
        currency=str(data.get("currency", "JPY")),
        leverage=int(data.get("leverage", 0)),
    )


def mt5_symbol_to_entity(data: dict) -> SymbolInfo:
    """MT5シンボル情報をSymbolInfoエンティティに変換

    Args:
        data: MT5シンボル情報辞書

    Returns:
        SymbolInfo: シンボル情報エンティティ
    """
    return SymbolInfo(
        symbol=str(data.get("name", "")),
        point=float(data.get("point", 0.001)),
        digits=int(data.get("digits", 3)),
        spread=int(data.get("spread", 0)),
        min_lot=float(data.get("volume_min", 0.01)),
        max_lot=float(data.get("volume_max", 100.0)),
        lot_step=float(data.get("volume_step", 0.01)),
        contract_size=float(data.get("trade_contract_size", 100000)),
    )


def mt5_position_to_entity(data: dict) -> Position:
    """MT5ポジション情報をPositionエンティティに変換

    Args:
        data: MT5ポジション情報辞書

    Returns:
        Position: ポジションエンティティ
    """
    pos_type = int(data.get("type", 0))
    signal_type = (
        SignalType.BUY
        if pos_type == POSITION_TYPE_BUY
        else SignalType.SELL
    )

    # MT5のタイムスタンプはUNIXエポック秒
    time_val = data.get("time", 0)
    opened_at = datetime.fromtimestamp(
        int(time_val), tz=timezone.utc
    )

    return Position(
        position_id=str(data.get("ticket", 0)),
        ticket=int(data.get("ticket", 0)),
        symbol=str(data.get("symbol", "")),
        signal_type=signal_type,
        volume=float(data.get("volume", 0)),
        entry_price=float(data.get("price_open", 0)),
        stop_loss=float(data.get("sl", 0)) or None,
        take_profit=float(data.get("tp", 0)) or None,
        opened_at=opened_at,
        unrealized_pnl=float(data.get("profit", 0)),
    )


def mt5_rates_to_dataframe(rates: list[dict]) -> pd.DataFrame:
    """MT5レート配列をDataFrameに変換

    Args:
        rates: MT5レートデータのリスト

    Returns:
        pd.DataFrame: OHLCVデータフレーム
    """
    if not rates:
        return pd.DataFrame(
            columns=["time", "open", "high", "low", "close", "volume"]
        )

    df = pd.DataFrame(rates)

    # MT5のtimeはUNIXエポック秒
    if "time" in df.columns:
        df["time"] = pd.to_datetime(
            pd.to_numeric(df["time"]), unit="s", utc=True
        )

    # カラム名の正規化
    rename_map = {
        "tick_volume": "volume",
        "real_volume": "real_volume",
    }
    df = df.rename(columns=rename_map)

    # 必要カラムのみ抽出
    required = ["time", "open", "high", "low", "close", "volume"]
    existing = [c for c in required if c in df.columns]
    df = df[existing]

    return df


def signal_to_mt5_request(
    signal: Signal,
    volume: float,
    tick: dict,
    magic: int = DEFAULT_MAGIC_NUMBER,
    deviation: int = DEFAULT_DEVIATION,
    filling_type: int = ORDER_FILLING_FOK,
) -> dict:
    """シグナルをMT5注文リクエストに変換

    Args:
        signal: トレードシグナル
        volume: ロット数
        tick: 現在のティック情報（ask, bid）
        magic: マジックナンバー
        deviation: 許容スリッページ
        filling_type: 充填タイプ

    Returns:
        dict: MT5注文リクエスト辞書
    """
    is_buy = signal.signal_type == SignalType.BUY
    order_type = ORDER_TYPE_BUY if is_buy else ORDER_TYPE_SELL
    price = float(tick.get("ask", 0)) if is_buy else float(
        tick.get("bid", 0)
    )

    request = {
        "action": TRADE_ACTION_DEAL,
        "symbol": signal.symbol,
        "volume": round(volume, 2),
        "type": order_type,
        "price": price,
        "deviation": deviation,
        "magic": magic,
        "comment": f"AT4_{signal.signal_id[:8]}",
        "type_time": 0,  # GTC
        "type_filling": filling_type,
    }

    if signal.stop_loss is not None:
        request["sl"] = signal.stop_loss
    if signal.take_profit is not None:
        request["tp"] = signal.take_profit

    return request
