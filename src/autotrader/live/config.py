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
class FundamentalConfig:
    """ファンダメンタルデータ収集・利用設定

    Attributes:
        enabled: ファンダメンタル機能の有効/無効（デフォルトOFF）
        use_mt5_calendar: MT5カレンダーを使用するか
        use_forex_factory: ForexFactoryを使用するか（デフォルトOFF）
        fetch_interval_minutes: データ収集間隔（分）
        morning_update_utc_hour: 毎朝LLM市場観更新の時刻（UTC）
        event_guard_minutes: 重要指標前の取引停止分数
    """

    enabled: bool = False
    use_mt5_calendar: bool = True
    use_forex_factory: bool = False
    fetch_interval_minutes: int = 60
    morning_update_utc_hour: int = 21  # UTC21時=日本時間6時
    event_guard_minutes: int = 30


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
        fundamental_config: ファンダメンタル設定
    """

    symbol: str = "USDJPY"
    check_interval_sec: float = 1.0
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
    fundamental_config: FundamentalConfig = field(
        default_factory=FundamentalConfig
    )
