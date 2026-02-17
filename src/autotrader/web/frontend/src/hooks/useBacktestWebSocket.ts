/** バックテスト専用WebSocketフック */

import { useCallback, useRef } from 'react'
import type { ConnectionState } from '../api/websocket'
import { useWebSocket } from './useWebSocket'

interface BacktestProgress {
  percentage: number
  currentYear: number | null
}

interface BacktestMetrics {
  balance: number
  equity: number
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  max_drawdown: number
}

interface MonthResult {
  year: number
  month: number
  trades: number
  pnl: number
  return_pct: number
}

interface TradeEvent {
  timestamp: string
  trade_id: string
  symbol: string
  direction: string
  entry_price: number
  exit_price?: number
  volume: number
  profit_loss?: number
  exit_reason?: string
  opened_at?: string
}

interface BacktestCallbacks {
  onStart?: () => void
  onEnd?: (cancelled: boolean) => void
  onProgress?: (progress: BacktestProgress) => void
  onMetrics?: (metrics: BacktestMetrics) => void
  onMonthEnd?: (month: MonthResult) => void
  onTradeClose?: (trade: TradeEvent) => void
  onYearStart?: (year: number) => void
}

interface UseBacktestWebSocketReturn {
  connectionState: ConnectionState
  isConnected: boolean
}

/** バックテストWebSocketフック */
export function useBacktestWebSocket(
  callbacks: BacktestCallbacks
): UseBacktestWebSocketReturn {
  const callbacksRef = useRef(callbacks)
  callbacksRef.current = callbacks

  const handleMessage = useCallback((message: { type: string; data: unknown }) => {
    const cb = callbacksRef.current
    const data = message.data as Record<string, unknown>

    switch (message.type) {
      case 'backtest_start':
        cb.onStart?.()
        break
      case 'backtest_end':
        cb.onEnd?.(!!data?.cancelled)
        break
      case 'backtest_progress':
        cb.onProgress?.({
          percentage: (data?.percentage as number) ?? 0,
          currentYear: null,
        })
        break
      case 'backtest_year_start':
        cb.onYearStart?.((data?.year as number) ?? 0)
        break
      case 'backtest_metrics':
        cb.onMetrics?.(data as unknown as BacktestMetrics)
        break
      case 'backtest_month_end':
        cb.onMonthEnd?.(data as unknown as MonthResult)
        break
      case 'backtest_trade_close':
        cb.onTradeClose?.(data as unknown as TradeEvent)
        break
    }
  }, [])

  const { connectionState, isConnected } = useWebSocket('/ws/backtest', {
    onMessage: handleMessage,
  })

  return { connectionState, isConnected }
}

export type { BacktestProgress, BacktestMetrics, MonthResult, TradeEvent }
