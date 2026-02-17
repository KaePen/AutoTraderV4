/** シグナルフック */

import { useState, useEffect, useCallback } from 'react'
import type { Signal, WebSocketMessage } from '../types'
import { getCurrentSignals, getSignalHistory } from '../api/client'
import { useWebSocket } from './useWebSocket'

interface UseSignalsReturn {
  currentSignals: Signal[]
  signalHistory: Signal[]
  isLoading: boolean
  error: string | null
  refresh: () => Promise<void>
}

/** シグナルデータ取得フック */
export function useSignals(symbol: string = 'USDJPY'): UseSignalsReturn {
  const [currentSignals, setCurrentSignals] = useState<Signal[]>([])
  const [signalHistory, setSignalHistory] = useState<Signal[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // WebSocket接続
  useWebSocket('/ws/signals', {
    onMessage: (message: WebSocketMessage) => {
      if (message.type === 'signal_update') {
        const signal = message.data as Signal
        if (signal.symbol === symbol) {
          setCurrentSignals((prev) => {
            // 同じIDがあれば更新、なければ追加
            const exists = prev.find((s) => s.signal_id === signal.signal_id)
            if (exists) {
              return prev.map((s) =>
                s.signal_id === signal.signal_id ? signal : s
              )
            }
            return [signal, ...prev].slice(0, 10)
          })
        }
      }
    },
  })

  const fetchData = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const [current, history] = await Promise.all([
        getCurrentSignals(symbol),
        getSignalHistory(symbol, 50),
      ])
      setCurrentSignals(current)
      setSignalHistory(history)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setIsLoading(false)
    }
  }, [symbol])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  return {
    currentSignals,
    signalHistory,
    isLoading,
    error,
    refresh: fetchData,
  }
}
