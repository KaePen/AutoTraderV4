"""ライブトレーディング設定"""

from __future__ import annotations

from dataclasses import dataclass, field

from autotrader.adapters.mt5.config import MT5Config
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.risk.position_manager import (
    PositionManagerConfig,
)
from autotrader.live.tick_entry_config import TickEntryConfig


@dataclass(frozen=True)
class ReloadConfig:
    """ホットリロード設定

    Attributes:
        enabled: ホットリロード機能の有効/無効
        auto_reload_on_change: ファイル変更時に自動リロードするか
        auto_reload_poll_interval_sec: 変更検知ポーリング間隔（秒）
        reload_timeout_sec: リロードタイムアウト（秒）
    """

    enabled: bool = True
    auto_reload_on_change: bool = False  # 本番は False（安全側）
    auto_reload_poll_interval_sec: float = 5.0
    reload_timeout_sec: float = 30.0


@dataclass(frozen=True)
class FundamentalConfig:
    """ファンダメンタルデータ収集・利用設定

    Attributes:
        enabled: ファンダメンタル機能の有効/無効（デフォルトOFF）
        use_mt5_calendar: MT5カレンダーを使用するか
        use_forex_factory: ForexFactoryを使用するか（デフォルトOFF）
        use_ff_holidays: ForexFactoryから休日データのみ取得するか（非推奨）
        use_exchange_calendars: exchange_calendarsで休日データを取得するか
        fetch_interval_minutes: データ収集間隔（分）
        morning_update_utc_hour: 毎朝LLM市場観更新の時刻（UTC）
        event_guard_minutes: 重要指標前の取引停止分数
        use_rss_news: RSSニュースポーリングを有効にするか
        rss_poll_interval_minutes: RSSポーリング間隔（分）
        rss_sentiment_ttl_hours: センチメントスコアのTTL（時間）
    """

    enabled: bool = False
    use_mt5_calendar: bool = True
    use_forex_factory: bool = False
    use_ff_holidays: bool = False
    use_exchange_calendars: bool = True
    fetch_interval_minutes: int = 60
    morning_update_utc_hour: int = 21  # UTC21時=日本時間6時
    event_guard_minutes: int = 30
    use_rss_news: bool = False
    rss_poll_interval_minutes: int = 5
    rss_sentiment_ttl_hours: int = 4


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
        reload_config: ホットリロード設定
    """

    symbol: str = "USDJPY"
    check_interval_sec: float = 1.0
    candle_lookback: int = 500
    # エントリー判定を M1 確定時のみ行う (BT-ライブ乖離対策)
    # True: bot.generate_signal() を M1 が新規確定したタイミングでのみ実行
    #       (BT と評価頻度を一致させる、推奨)
    # False: 従来動作 (毎秒 generate_signal) — 1秒スパイク逆張りBUY等を発生
    # ポジション管理 (SL/TP/EDGE_DECAY 等) は引き続き毎秒実行される
    entry_on_m1_close_only: bool = True
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
    reload_config: ReloadConfig = field(
        default_factory=ReloadConfig
    )
