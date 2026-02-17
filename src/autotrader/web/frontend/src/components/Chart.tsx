/** チャートコンポーネント */

import { useEffect, useRef, useState, useCallback } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type Time,
  ColorType,
} from 'lightweight-charts'
import type { Candle, Timeframe, Signal } from '../types'
import { getCandles } from '../api/client'

interface ChartProps {
  symbol: string
  signals?: Signal[]
}

const TIMEFRAMES: Timeframe[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']

/** トレーディングチャート */
export function Chart({ symbol, signals = [] }: ChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  const [timeframe, setTimeframe] = useState<Timeframe>('M15')
  const [candles, setCandles] = useState<Candle[]>([])
  const [isLoading, setIsLoading] = useState(true)

  // データ取得
  const fetchCandles = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await getCandles(symbol, timeframe, 500)
      setCandles(data)
    } catch {
      // エラー時は空配列
      setCandles([])
    } finally {
      setIsLoading(false)
    }
  }, [symbol, timeframe])

  useEffect(() => {
    fetchCandles()
  }, [fetchCandles])

  // チャート初期化
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#1f2937' },
        textColor: '#9ca3af',
      },
      grid: {
        vertLines: { color: '#374151' },
        horzLines: { color: '#374151' },
      },
      crosshair: {
        mode: 1,
      },
      rightPriceScale: {
        borderColor: '#374151',
      },
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
        secondsVisible: false,
      },
    })

    chartRef.current = chart

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    })

    candleSeriesRef.current = candleSeries

    // リサイズ対応
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        chart.applyOptions({ width, height })
      }
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
    }
  }, [])

  // データ更新
  useEffect(() => {
    if (!candleSeriesRef.current || candles.length === 0) return

    const chartData: CandlestickData[] = candles.map((c) => ({
      time: (new Date(c.time).getTime() / 1000) as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))

    candleSeriesRef.current.setData(chartData)

    // シグナルマーカー
    const markers = signals
      .filter((s) => s.timeframe === timeframe && s.signal_type !== 'HOLD')
      .map((s) => ({
        time: (new Date(s.created_at).getTime() / 1000) as Time,
        position: s.signal_type === 'BUY' ? 'belowBar' as const : 'aboveBar' as const,
        color: s.signal_type === 'BUY' ? '#22c55e' : '#ef4444',
        shape: s.signal_type === 'BUY' ? 'arrowUp' as const : 'arrowDown' as const,
        text: `${s.signal_type} ${(s.confidence * 100).toFixed(0)}%`,
      }))

    candleSeriesRef.current.setMarkers(markers)
  }, [candles, signals, timeframe])

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-gray-100">
          {symbol} チャート
        </h2>
        <div className="flex gap-1">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-2 py-1 text-xs rounded transition-colors ${
                timeframe === tf
                  ? 'bg-primary-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>
      <div
        ref={containerRef}
        className="h-96 w-full relative"
      >
        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-gray-800/50">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500" />
          </div>
        )}
      </div>
    </div>
  )
}
