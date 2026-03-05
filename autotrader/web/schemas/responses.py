"""レスポンススキーマ"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from autotrader.core.enums import (
    ConfidenceLevel,
    ExitReason,
    SignalType,
    Timeframe,
)

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """API共通レスポンス

    Attributes:
        success: 成功フラグ
        data: レスポンスデータ
        error: エラーメッセージ
        timestamp: タイムスタンプ
    """

    success: bool = True
    data: T | None = None
    error: str | None = None
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class HealthResponse(BaseModel):
    """ヘルスチェックレスポンス

    Attributes:
        status: ステータス
        version: バージョン
        uptime: 稼働時間（秒）
    """

    status: str = "ok"
    version: str = "1.0.0"
    uptime: float = 0.0


class AccountPresetResponse(BaseModel):
    """口座プリセットレスポンス

    Attributes:
        login: MT5ログインID
        server: サーバー名
        name: 表示名
    """

    login: int
    server: str
    name: str = ""


class AccountPresetsResponse(BaseModel):
    """口座プリセット一覧レスポンス

    Attributes:
        accounts: 口座プリセットリスト
    """

    accounts: list[AccountPresetResponse] = Field(default_factory=list)


class AccountInfoResponse(BaseModel):
    """口座情報レスポンス

    Attributes:
        balance: 残高
        equity: 有効証拠金
        margin: 使用証拠金
        free_margin: 余剰証拠金
        margin_level: 証拠金維持率
        profit: 含み損益
        login: ログインID
        server: サーバー名
        name: 口座名義
        currency: 口座通貨
        leverage: レバレッジ
    """

    balance: float
    equity: float
    margin: float = 0.0
    free_margin: float = 0.0
    margin_level: float = 0.0
    profit: float = 0.0
    login: int = 0
    server: str = ""
    name: str = ""
    currency: str = "JPY"
    leverage: int = 0


class DashboardResponse(BaseModel):
    """ダッシュボードレスポンス

    Attributes:
        account: 口座情報
        daily_pnl: 本日の損益
        daily_pnl_pct: 本日の損益率
        weekly_pnl: 今週（月曜起算）の損益
        monthly_pnl: 今月の損益
        total_pnl: 全履歴の損益
        total_trades: 全履歴のトレード数
        active_signals: アクティブシグナル数
        open_positions: オープンポジション数
        today_trades: 本日のトレード数
        win_rate: 勝率
    """

    account: AccountInfoResponse
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    weekly_pnl: float = 0.0
    monthly_pnl: float = 0.0
    total_pnl: float = 0.0
    total_trades: int = 0
    active_signals: int = 0
    open_positions: int = 0
    today_trades: int = 0
    win_rate: float = 0.0


class SignalResponse(BaseModel):
    """シグナルレスポンス

    Attributes:
        signal_id: シグナルID
        symbol: 通貨ペア
        timeframe: 時間足
        signal_type: シグナル種別
        confidence: 確度
        confidence_level: 確度レベル
        stop_loss: 損切価格
        take_profit: 利確価格
        reasoning: 判断理由
        created_at: 生成時刻
        indicators_snapshot: 指標スナップショット
        regime: 市場レジーム
        mode: トレードモード
        consensus_score: コンセンサススコア
        lot: ロット数
    """

    signal_id: str
    symbol: str
    timeframe: Timeframe
    signal_type: SignalType
    confidence: float
    confidence_level: ConfidenceLevel
    stop_loss: float | None = None
    take_profit: float | None = None
    reasoning: str = ""
    created_at: datetime
    indicators_snapshot: dict[str, Any] = Field(default_factory=dict)
    regime: str | None = None
    mode: str | None = None
    consensus_score: float | None = None
    lot: float | None = None


class PositionResponse(BaseModel):
    """ポジションレスポンス

    Attributes:
        position_id: ポジションID
        ticket: チケットID
        symbol: 通貨ペア
        signal_type: 方向
        volume: ロット数
        entry_price: エントリー価格
        current_price: 現在価格
        stop_loss: 損切価格
        take_profit: 利確価格
        opened_at: オープン時刻
        unrealized_pnl: 未実現損益
        unrealized_pnl_pips: 未実現損益（pips）
        signal_id: シグナルID
        regime: 市場レジーム
        mode: トレードモード
        consensus_score: コンセンサススコア
        remaining_minutes: 残り保有時間（分）
        max_hold_minutes: 最大保有時間（分）
        elapsed_minutes: 経過時間（分）
    """

    position_id: str
    ticket: int = 0
    trade_id: str = ""
    symbol: str
    signal_type: SignalType
    volume: float
    entry_price: float
    current_price: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    opened_at: datetime
    unrealized_pnl: float = 0.0
    unrealized_pnl_pips: float = 0.0
    signal_id: str | None = None
    regime: str | None = None
    mode: str | None = None
    consensus_score: float | None = None
    remaining_minutes: int | None = None
    max_hold_minutes: int | None = None
    elapsed_minutes: int | None = None


class TradeResponse(BaseModel):
    """トレードレスポンス

    Attributes:
        trade_id: トレードID
        ticket: チケットID
        symbol: 通貨ペア
        signal_type: 方向
        volume: ロット数
        entry_price: エントリー価格
        exit_price: 決済価格
        stop_loss: 損切価格
        take_profit: 利確価格
        profit_loss: 損益
        profit_loss_pips: 損益（pips）
        exit_reason: 決済理由
        opened_at: オープン時刻
        closed_at: クローズ時刻
        signal_id: シグナルID
        regime: 市場レジーム
        mode: トレードモード
        consensus_score: コンセンサススコア
        parent_trade_id: 部分決済の親トレードID
        position_id: ポジションID
        mfe_pips: 最大含み益（pips）
        mae_pips: 最大含み損（pips）
        entry_spread: エントリー時スプレッド
    """

    trade_id: str
    ticket: int = 0
    is_open: bool = False
    symbol: str
    signal_type: SignalType
    volume: float
    entry_price: float
    exit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    profit_loss: float | None = None
    profit_loss_pips: float | None = None
    exit_reason: ExitReason | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    signal_id: str | None = None
    regime: str | None = None
    mode: str | None = None
    consensus_score: float | None = None
    parent_trade_id: str | None = None
    position_id: str | None = None
    mfe_pips: float | None = None
    mae_pips: float | None = None
    entry_spread: float | None = None


class TradeSummaryResponse(BaseModel):
    """トレードサマリーレスポンス

    Attributes:
        total_trades: 総トレード数
        winning_trades: 勝ちトレード数
        losing_trades: 負けトレード数
        win_rate: 勝率
        total_profit: 総利益
        total_loss: 総損失
        net_profit: 純利益
        profit_factor: プロフィットファクター
        average_win: 平均勝ちトレード
        average_loss: 平均負けトレード
        max_drawdown: 最大ドローダウン
    """

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_profit: float = 0.0
    total_loss: float = 0.0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    max_drawdown: float = 0.0


class IndicatorResponse(BaseModel):
    """指標レスポンス

    Attributes:
        symbol: 通貨ペア
        timeframe: 時間足
        timestamp: タイムスタンプ
        rsi: RSI
        macd: MACD
        macd_signal: MACDシグナル
        macd_hist: MACDヒストグラム
        adx: ADX
        plus_di: +DI
        minus_di: -DI
        bb_upper: ボリンジャーバンド上限
        bb_middle: ボリンジャーバンド中央
        bb_lower: ボリンジャーバンド下限
        atr: ATR
        ema_fast: 短期EMA
        ema_slow: 長期EMA
    """

    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    atr: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None


class CandleResponse(BaseModel):
    """ローソク足レスポンス

    Attributes:
        symbol: 通貨ペア
        timeframe: 時間足
        time: タイムスタンプ
        open: 始値
        high: 高値
        low: 安値
        close: 終値
        volume: 出来高
    """

    symbol: str
    timeframe: Timeframe
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class RiskConfigResponse(BaseModel):
    """リスク設定レスポンス"""

    max_daily_loss_pct: float
    max_position_count: int
    min_margin_ratio: float


class NotificationConfigResponse(BaseModel):
    """通知設定レスポンス"""

    enabled: bool = True
    min_confidence: float = 0.5
    sound_enabled: bool = True


# --- 新スキーマ（UnifiedBotConfig対応） ---
class EntryFilterConfigResponse(BaseModel):
    """エントリーフィルター設定レスポンス"""

    range_day_bbw_threshold: float = 0.20
    range_day_score_premium: float = 0.55
    weak_hours_enabled: bool = True
    weak_hours_score_premium: float = 0.5


class CapitalManagementConfigResponse(BaseModel):
    """資金管理設定レスポンス"""

    use_dynamic_lot: bool = True
    base_risk_pct: float = 0.04
    max_lot_per_trade: float = 5.0
    max_total_exposure_lot: float = 5.0
    equity_floor_pct: float = 0.30
    slippage_buffer_pips: float = 2.0


class PositionManagementConfigResponse(BaseModel):
    """ポジション管理設定レスポンス（全PMフィールド）"""

    enable_position_manager: bool = True
    # 部分利確
    partial_close_1r_ratio: float = 0.05
    partial_close_2r_ratio: float = 0.05
    breakeven_at_1r: bool = True
    # トレーリング
    trailing_start_r: float = 1.5
    trailing_atr_multiplier: float = 1.5
    # 時間決済
    time_exit_enabled: bool = True
    # コスト
    spread_pips: float = 1.5
    slippage_pips: float = 0.5
    # BE基本設定
    early_breakeven_r: float = 0.5
    early_breakeven_enabled: bool = True
    disable_tp_after_partial: bool = True
    signal_rev_close_ratio: float = 0.5
    # Stagnation
    stagnation_exit_minutes: float = 120.0
    stagnation_min_mfe_r: float = 0.15
    # BE制御（RANGE×DAY）
    range_day_be_disabled: bool = True
    range_day_early_be_r: float = 0.3
    range_day_fast_be_enabled: bool = True
    range_day_fast_be_minutes: float = 90.0
    # RANGE×DAY stagnation段階化
    range_day_stagnation_enabled: bool = False
    range_day_stagnation_stage1_minutes: float = 45.0
    range_day_stagnation_stage1_min_mfe_r: float = 0.05
    range_day_stagnation_stage2_minutes: float = 60.0
    range_day_stagnation_stage2_min_mfe_r: float = 0.10
    # 0.5R早期部分利確
    early_partial_close_enabled: bool = False
    early_partial_close_ratio: float = 0.25
    # RANGE×DAY 保険
    range_day_insurance_enabled: bool = True
    range_day_insurance_max_minutes: float = 30.0
    range_day_insurance_sl_offset_r: float = -0.1
    range_day_insurance_partial_ratio: float = 0.20
    # TP_EARLY厳格化
    insurance_trigger_r: float = 1.0
    insurance_block_high_mfe_r: float = 0.8
    insurance_min_holding_minutes: float = 15.0
    # 0.5R部分利確
    range_day_half_r_partial_enabled: bool = True
    range_day_half_r_partial_ratio: float = 0.20
    range_day_half_r_trigger: float = 0.5


class TradingConfigResponse(BaseModel):
    """トレーディング設定レスポンス（UnifiedBotConfig対応）"""

    entry_filter: EntryFilterConfigResponse
    capital_management: CapitalManagementConfigResponse
    position_management: PositionManagementConfigResponse


class SettingsResponse(BaseModel):
    """設定レスポンス

    Attributes:
        trading: トレーディング設定（新）
        risk: リスク設定
        notification: 通知設定
        strategy: 戦略設定（レガシー互換）
    """

    trading: TradingConfigResponse
    risk: RiskConfigResponse
    notification: NotificationConfigResponse


# --- MT5/ライブトレーディング用スキーマ ---
class MT5StatusResponse(BaseModel):
    """MT5接続状態レスポンス

    Attributes:
        connected: MT5接続状態
        transport: トランスポート種別
        account: 口座情報（接続時）
        symbol_info: シンボル情報（接続時）
    """

    connected: bool = False
    transport: str = "direct"
    account: AccountInfoResponse | None = None
    symbol_info: dict[str, Any] | None = None


class AnalysisResponse(BaseModel):
    """分析状態レスポンス（直近tick結果）

    Attributes:
        symbol: 分析対象の通貨ペア
        direction: シグナル方向（"HOLD"|"BUY"|"SELL"）
        confidence: 確度
        consensus_score: コンセンサススコア
        entry_threshold: エントリー閾値
        regime: 市場レジーム
        mode: トレードモード
        rationale: 判断理由
        htf_alignment: 上位足整合度
        penalty_total: ペナルティ合計
        penalty_breakdown: ペナルティ内訳
        trend_strength: トレンド強度
        aligned_tfs: 方向一致時間足
        tf_scores: 時間足別スコア
        tf_breakdowns: 時間足別スコア内訳
        last_tick_time: 最終tick時刻（ISO形式）
        demo_mode: デモモード状態
        buy_score: 買いスコア
        sell_score: 売りスコア
        mode: トレードモード
    """

    symbol: str | None = None
    direction: str = "HOLD"
    confidence: float = 0.0
    consensus_score: float | None = None
    entry_threshold: float = 0.0
    regime: str | None = None
    mode: str | None = None
    rationale: str = "データなし"
    buy_score: float = 0.0
    sell_score: float = 0.0
    htf_alignment: float = 0.0
    penalty_total: float = 0.0
    penalty_breakdown: dict[str, float] = Field(default_factory=dict)
    trend_strength: float = 0.0
    aligned_tfs: list[str] = Field(default_factory=list)
    tf_scores: dict[str, float] = Field(default_factory=dict)
    tf_breakdowns: dict[str, dict[str, float]] = Field(default_factory=dict)
    tf_directions: dict[str, str] = Field(default_factory=dict)
    last_tick_time: str | None = None
    demo_mode: bool = False
    engine_running: bool = False
    auto_trade_enabled: bool = False
    mt5_connected: bool = False


class TradingModeResponse(BaseModel):
    """トレーディングモードレスポンス

    Attributes:
        mode: モード（"backtest"|"live"|"demo"）
        label: 表示ラベル
        connected: 接続状態
        auto_trade: 自動取引ON/OFF
        engine_running: エンジン実行中
    """

    mode: str = "backtest"
    label: str = "Backtest Mode"
    connected: bool = False
    auto_trade: bool = False
    engine_running: bool = False
    demo_mode: bool = False
    symbol_auto_trade: dict[str, bool] = Field(default_factory=dict)
    symbol_demo_mode: dict[str, bool] = Field(default_factory=dict)


class ReloadLogicResponse(BaseModel):
    """ホットリロード実行レスポンス

    Attributes:
        success: リロード成功フラグ
        reloaded_at: リロード完了時刻（ISO8601）
        error: エラーメッセージ（失敗時）
        results: シンボル別リロード結果
    """

    success: bool
    reloaded_at: str | None = None
    error: str | None = None
    results: dict[str, Any] = Field(default_factory=dict)


class ReloadStatusResponse(BaseModel):
    """ホットリロード状態レスポンス

    Attributes:
        reloading: リロード実行中フラグ
        last_reload: 最終リロード時刻
        changed_files: 変更検知済みファイル
    """

    reloading: bool = False
    last_reload: datetime | None = None
    changed_files: list[str] = Field(default_factory=list)


class IndicatorPoint(BaseModel):
    """時系列指標の1点

    Attributes:
        time: UNIX秒タイムスタンプ
        value: 指標値
    """

    time: float
    value: float


class IndicatorSeriesResponse(BaseModel):
    """チャートオーバーレイ用指標時系列レスポンス

    Attributes:
        ema12: EMA(12)時系列
        ema26: EMA(26)時系列
        ema50: EMA(50)時系列
        ema200: EMA(200)時系列
        bb_upper: BB上限時系列
        bb_middle: BB中央時系列
        bb_lower: BB下限時系列
        rsi: RSI時系列
        vwap: VWAP時系列
    """

    ema12: list[IndicatorPoint] = Field(default_factory=list)
    ema26: list[IndicatorPoint] = Field(default_factory=list)
    ema50: list[IndicatorPoint] = Field(default_factory=list)
    ema200: list[IndicatorPoint] = Field(default_factory=list)
    bb_upper: list[IndicatorPoint] = Field(default_factory=list)
    bb_middle: list[IndicatorPoint] = Field(default_factory=list)
    bb_lower: list[IndicatorPoint] = Field(default_factory=list)
    rsi: list[IndicatorPoint] = Field(default_factory=list)
    vwap: list[IndicatorPoint] = Field(default_factory=list)


# --- ファンダメンタル統合スキーマ ---


class NewsItemResponse(BaseModel):
    """ニュースアイテムレスポンス

    Attributes:
        news_id: ニュースID
        published_at: 公開日時
        title: タイトル
        source_name: ソース名
        source_url: ソースURL
        currencies: 関連通貨リスト
        snippet: 本文スニペット
        sentiment_score: センチメントスコア
    """

    news_id: str
    published_at: datetime
    title: str
    source_name: str
    source_url: str
    currencies: list[str] = Field(default_factory=list)
    snippet: str | None = None
    sentiment_score: float | None = None


class EconomicEventResponse(BaseModel):
    """経済イベントレスポンス

    Attributes:
        event_id: イベントID
        event_time: イベント予定時刻
        currency: 通貨コード
        event_name: イベント名称
        impact: インパクトレベル
        actual: 実績値
        forecast: 予測値
        previous: 前回値
        is_released: 発表済みフラグ
        minutes_until: イベントまでの分数
    """

    event_id: str
    event_time: datetime
    currency: str
    event_name: str
    impact: str  # "high" | "medium" | "low"
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    is_released: bool = False
    minutes_until: float = 0.0


class FundamentalNewsResponse(BaseModel):
    """ファンダメンタルニュース一覧レスポンス

    Attributes:
        items: ニュースアイテムリスト
        total: 合計件数
        symbol: 対象シンボル
    """

    items: list[NewsItemResponse] = Field(default_factory=list)
    total: int = 0
    symbol: str = ""


class FundamentalCalendarResponse(BaseModel):
    """経済カレンダーレスポンス

    Attributes:
        events: 経済イベントリスト
        symbol: 対象シンボル
        next_high_impact_minutes: 次のHIGHイベントまでの分数
    """

    events: list[EconomicEventResponse] = Field(default_factory=list)
    symbol: str = ""
    next_high_impact_minutes: float | None = None
