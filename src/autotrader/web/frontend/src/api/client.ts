/** APIクライアント */

import type {
  ApiResponse,
  Dashboard,
  Signal,
  Position,
  Trade,
  TradeSummary,
  Indicators,
  Candle,
  Settings,
  Timeframe,
  BacktestHistoryItem,
} from '../types'

const API_BASE = '/api/v1'

/** API呼び出し共通関数 */
async function fetchApi<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    headers: {
      'Content-Type': 'application/json',
    },
    ...options,
  })

  if (!response.ok) {
    throw new Error(`API Error: ${response.status} ${response.statusText}`)
  }

  const json: ApiResponse<T> = await response.json()

  if (!json.success) {
    throw new Error(json.error ?? 'Unknown error')
  }

  return json.data as T
}

/** nullを許容するAPI呼び出し */
export async function fetchApiNullable<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T | null> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    headers: {
      'Content-Type': 'application/json',
    },
    ...options,
  })

  if (!response.ok) {
    throw new Error(`API Error: ${response.status} ${response.statusText}`)
  }

  const json: ApiResponse<T> = await response.json()

  if (!json.success) {
    throw new Error(json.error ?? 'Unknown error')
  }

  return json.data
}

/** ヘルスチェック */
export async function getHealth(): Promise<{
  status: string
  version: string
  uptime: number
}> {
  return fetchApi('/health')
}

/** ダッシュボード取得 */
export async function getDashboard(): Promise<Dashboard> {
  return fetchApi('/dashboard')
}

/** 現在のシグナル取得 */
export async function getCurrentSignals(
  symbol: string = 'USDJPY'
): Promise<Signal[]> {
  return fetchApi(`/signals/current?symbol=${symbol}`)
}

/** シグナル履歴取得 */
export async function getSignalHistory(
  symbol: string = 'USDJPY',
  limit: number = 50,
  offset: number = 0
): Promise<Signal[]> {
  return fetchApi(
    `/signals/history?symbol=${symbol}&limit=${limit}&offset=${offset}`
  )
}

/** ポジション取得 */
export async function getPositions(symbol?: string): Promise<Position[]> {
  const query = symbol ? `?symbol=${symbol}` : ''
  return fetchApi(`/positions${query}`)
}

/** トレード履歴取得 */
export async function getTrades(
  symbol?: string,
  limit: number = 50,
  offset: number = 0
): Promise<Trade[]> {
  const params = new URLSearchParams()
  if (symbol) params.append('symbol', symbol)
  params.append('limit', limit.toString())
  params.append('offset', offset.toString())
  return fetchApi(`/trades?${params.toString()}`)
}

/** トレードサマリー取得 */
export async function getTradeSummary(
  symbol?: string,
  days: number = 30
): Promise<TradeSummary> {
  const params = new URLSearchParams()
  if (symbol) params.append('symbol', symbol)
  params.append('days', days.toString())
  return fetchApi(`/trades/summary?${params.toString()}`)
}

/** 指標取得 */
export async function getIndicators(
  symbol: string,
  timeframe: Timeframe
): Promise<Indicators> {
  return fetchApi(`/indicators/${symbol}/${timeframe}`)
}

/** ローソク足取得 */
export async function getCandles(
  symbol: string,
  timeframe: Timeframe,
  limit: number = 200
): Promise<Candle[]> {
  return fetchApi(`/candles/${symbol}/${timeframe}?limit=${limit}`)
}

/** 設定取得 */
export async function getSettings(): Promise<Settings> {
  return fetchApi('/settings')
}

/** 設定更新 */
export async function updateSettings(
  settings: Partial<{
    trading: Partial<Settings['trading']>
    notification: Partial<Settings['notification']>
  }>
): Promise<Settings> {
  return fetchApi('/settings', {
    method: 'PUT',
    body: JSON.stringify(settings),
  })
}

/** バックテスト履歴取得 */
export async function getBacktestHistory(
  limit: number = 20
): Promise<BacktestHistoryItem[]> {
  return fetchApi(`/backtest/history?limit=${limit}`)
}

/** バックテストトレード取得 */
export async function getBacktestTrades(
  backtestId: number
): Promise<Trade[]> {
  return fetchApi(`/backtest/${backtestId}/trades`)
}
