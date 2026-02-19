"""MT5設定"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.adapters.mt5.constants import (
    DEFAULT_DEVIATION,
    DEFAULT_MAGIC_NUMBER,
    DEFAULT_SYMBOL,
)


@dataclass(frozen=True)
class MT5Config:
    """MT5接続設定

    Attributes:
        login: MT5ログインID
        password: パスワード
        server: サーバー名
        terminal_path: MT5ターミナルパス
        transport: トランスポート種別（"direct"）
        timeout_sec: タイムアウト（秒）
        magic_number: マジックナンバー（AutoTraderV4識別用）
        deviation: 許容スリッページ（ポイント）
        symbol: デフォルト通貨ペア
        retry_count: リトライ回数
        retry_delay_sec: リトライ間隔（秒）
        health_check_interval_sec: ヘルスチェック間隔（秒）
    """

    login: int = 0
    password: str = ""
    server: str = ""
    terminal_path: str = ""
    transport: str = "direct"
    timeout_sec: float = 10.0
    magic_number: int = DEFAULT_MAGIC_NUMBER
    deviation: int = DEFAULT_DEVIATION
    symbol: str = DEFAULT_SYMBOL
    retry_count: int = 3
    retry_delay_sec: float = 1.0
    health_check_interval_sec: float = 30.0
