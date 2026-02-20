"""ライブトレーディング設定"""

from __future__ import annotations

from dataclasses import dataclass, field

from autotrader.adapters.mt5.config import MT5Config
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.position_manager import (
    PositionManagerConfig,
)
from autotrader.live.tick_entry_config import TickEntryConfig


@dataclass(frozen=True)
class LiveTradingConfig:
    """ライブトレーディング設定

    Attributes:
        symbol: 取引対象通貨ペア
        check_interval_sec: メインループ間隔（秒）
        candle_lookback: 起動時の過去データ読込本数
        bot_config: UnifiedTradeBot設定
        mt5_config: MT5接続設定
        pm_config: PositionManager設定
        enable_auto_trade: 自動取引ON/OFF
        require_confirmation: 注文前に確認要求
        tick_entry_config: ティックエントリー最適化設定
    """

    symbol: str = "USDJPY"
    check_interval_sec: float = 0.5
    candle_lookback: int = 500
    bot_config: UnifiedBotConfig = field(
        default_factory=UnifiedBotConfig
    )
    mt5_config: MT5Config = field(default_factory=MT5Config)
    pm_config: PositionManagerConfig = field(
        default_factory=PositionManagerConfig
    )
    enable_auto_trade: bool = False
    require_confirmation: bool = True
    tick_entry_config: TickEntryConfig = field(
        default_factory=TickEntryConfig
    )
