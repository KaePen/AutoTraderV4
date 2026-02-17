/** 共通型定義 */

/** 時間足 */
export type Timeframe = 'M1' | 'M5' | 'M15' | 'M30' | 'H1' | 'H4' | 'D1' | 'W1'

/** シグナル種別 */
export type SignalType = 'BUY' | 'SELL' | 'HOLD'

/** 確度レベル */
export type ConfidenceLevel = 'HIGH' | 'MEDIUM' | 'LOW'

/** 決済理由 */
export type ExitReason =
  | 'STOP_LOSS'
  | 'TAKE_PROFIT'
  | 'TRAILING_STOP'
  | 'TIME_EXIT'
  | 'MANUAL'
  | 'SIGNAL_REVERSAL'
  | 'FORCE_CLOSE'
  | 'STAGNATION'
  | 'TP_EARLY'
  | 'INSURANCE'

/** APIレスポンス */
export interface ApiResponse<T> {
  success: boolean
  data: T | null
  error: string | null
  timestamp: string
}

/** 口座情報 */
export interface AccountInfo {
  balance: number
  equity: number
  margin: number
  free_margin: number
  margin_level: number
  profit: number
}

/** ダッシュボード */
export interface Dashboard {
  account: AccountInfo
  daily_pnl: number
  daily_pnl_pct: number
  active_signals: number
  open_positions: number
  today_trades: number
  win_rate: number
}

/** シグナル */
export interface Signal {
  signal_id: string
  symbol: string
  timeframe: Timeframe
  signal_type: SignalType
  confidence: number
  confidence_level: ConfidenceLevel
  stop_loss: number | null
  take_profit: number | null
  reasoning: string
  created_at: string
  indicators_snapshot: Record<string, unknown>
}

/** ポジション */
export interface Position {
  position_id: string
  ticket: number
  symbol: string
  signal_type: SignalType
  volume: number
  entry_price: number
  current_price: number
  stop_loss: number | null
  take_profit: number | null
  opened_at: string
  unrealized_pnl: number
  unrealized_pnl_pips: number
}

/** トレード */
export interface Trade {
  trade_id: string
  ticket: number
  symbol: string
  signal_type: SignalType
  volume: number
  entry_price: number
  exit_price: number | null
  stop_loss: number | null
  take_profit: number | null
  profit_loss: number | null
  profit_loss_pips: number | null
  exit_reason: ExitReason | null
  opened_at: string
  closed_at: string | null
}

/** トレードサマリー */
export interface TradeSummary {
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  total_profit: number
  total_loss: number
  net_profit: number
  profit_factor: number
  average_win: number
  average_loss: number
  max_drawdown: number
}

/** 指標 */
export interface Indicators {
  symbol: string
  timeframe: Timeframe
  timestamp: string
  rsi: number | null
  macd: number | null
  macd_signal: number | null
  macd_hist: number | null
  adx: number | null
  plus_di: number | null
  minus_di: number | null
  bb_upper: number | null
  bb_middle: number | null
  bb_lower: number | null
  atr: number | null
  ema_fast: number | null
  ema_slow: number | null
}

/** ローソク足 */
export interface Candle {
  symbol: string
  timeframe: Timeframe
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/** --- 新スキーマ（UnifiedBotConfig対応） --- */

/** エントリーフィルター設定 */
export interface EntryFilterConfig {
  range_day_bbw_threshold: number
  range_day_score_premium: number
  weak_hours_enabled: boolean
  weak_hours_score_premium: number
  tokyo_night_swing_enabled: boolean
  tokyo_night_swing_premium: number
}

/** 資金管理設定 */
export interface CapitalManagementConfig {
  use_dynamic_lot: boolean
  base_risk_pct: number
  max_lot_per_trade: number
  max_total_exposure_lot: number
  equity_floor_pct: number
  slippage_buffer_pips: number
}

/** ポジション管理設定 */
export interface PositionManagementConfig {
  enable_position_manager: boolean
  stagnation_min_mfe_r: number
  range_day_early_be_r: number
  insurance_trigger_r: number
  partial_close_1r_ratio: number
  trailing_start_r: number
}

/** トレーディング設定 */
export interface TradingConfig {
  entry_filter: EntryFilterConfig
  capital_management: CapitalManagementConfig
  position_management: PositionManagementConfig
}

/** リスク設定 */
export interface RiskConfig {
  max_daily_loss_pct: number
  max_position_count: number
  min_margin_ratio: number
}

/** 通知設定 */
export interface NotificationConfig {
  enabled: boolean
  min_confidence: number
  sound_enabled: boolean
}

/** 設定 */
export interface Settings {
  trading: TradingConfig
  risk: RiskConfig
  notification: NotificationConfig
}

/** バックテスト履歴項目 */
export interface BacktestHistoryItem {
  id: number
  name: string
  symbol: string
  start_date: string
  end_date: string
  total_trades: number
  win_rate: number | null
  profit_factor: number | null
  net_profit: number | null
  max_drawdown_pct: number | null
  sharpe_ratio: number | null
  status: string
  created_at: string
}

/** バックテストWebSocketイベント */
export type BacktestEventType =
  | 'backtest_start'
  | 'backtest_end'
  | 'backtest_progress'
  | 'backtest_year_start'
  | 'backtest_year_end'
  | 'backtest_month_end'
  | 'backtest_trade_open'
  | 'backtest_trade_close'
  | 'backtest_signal'
  | 'backtest_metrics'

/** WebSocketイベント種別 */
export type WebSocketEventType =
  | 'candle_update'
  | 'signal_update'
  | 'position_update'
  | 'indicator_update'
  | 'account_update'
  | 'alert'
  | 'heartbeat'
  | BacktestEventType

/** WebSocketメッセージ */
export interface WebSocketMessage<T = unknown> {
  type: WebSocketEventType
  data: T
  timestamp: string
}
