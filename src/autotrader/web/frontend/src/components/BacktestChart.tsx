/** バックテストチャートコンポーネント */

import { useEffect, useRef } from 'react'
import { createChart, IChartApi, ISeriesApi, LineData } from 'lightweight-charts'

interface BacktestChartProps {
  /** 月別結果データ */
  monthlyResults: MonthResult[]
  /** ローディング状態 */
  isLoading?: boolean
  /** リアルタイム更新モード */
  isRealtime?: boolean
}

interface MonthResult {
  year: number
  month: number
  trades: number
  pnl: number
  return_pct: number
}

/** バックテストチャート */
export function BacktestChart({
  monthlyResults,
  isLoading,
  isRealtime = false
}: BacktestChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const equitySeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const returnSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const prevDataLengthRef = useRef<number>(0)
  const lastCumulativeRef = useRef<number>(0)

  // チャート初期化
  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: 400,
      layout: {
        background: { color: '#1a1a2e' },
        textColor: '#e0e0e0',
      },
      grid: {
        vertLines: { color: '#2a2a4e' },
        horzLines: { color: '#2a2a4e' },
      },
      rightPriceScale: {
        borderColor: '#3a3a5e',
      },
      timeScale: {
        borderColor: '#3a3a5e',
        timeVisible: true,
      },
    })

    const equitySeries = chart.addLineSeries({
      color: '#4ade80',
      lineWidth: 2,
      priceScaleId: 'right',
    })

    const returnSeries = chart.addHistogramSeries({
      priceScaleId: 'left',
      color: '#3b82f6',
    })

    chart.priceScale('left').applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    })

    chartRef.current = chart
    equitySeriesRef.current = equitySeries
    returnSeriesRef.current = returnSeries

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [])

  // データ空時にrefリセット
  useEffect(() => {
    if (monthlyResults.length === 0) {
      prevDataLengthRef.current = 0
      lastCumulativeRef.current = 0
    }
  }, [monthlyResults.length === 0])

  // データ更新
  useEffect(() => {
    if (!equitySeriesRef.current || !returnSeriesRef.current) return
    if (monthlyResults.length === 0) {
      equitySeriesRef.current.setData([])
      returnSeriesRef.current.setData([])
      return
    }

    // リアルタイム増分更新
    if (isRealtime && monthlyResults.length > prevDataLengthRef.current) {
      const newPoints = monthlyResults.slice(prevDataLengthRef.current)
      let cumulative = lastCumulativeRef.current

      newPoints.forEach((result) => {
        cumulative += result.pnl
        const timeStr = `${result.year}-${String(result.month).padStart(2, '0')}-01`

        equitySeriesRef.current?.update({
          time: timeStr,
          value: cumulative,
        })
        returnSeriesRef.current?.update({
          time: timeStr,
          value: result.return_pct,
          color: result.return_pct >= 0 ? '#4ade80' : '#ef4444',
        })
      })

      prevDataLengthRef.current = monthlyResults.length
      lastCumulativeRef.current = cumulative
    } else if (!isRealtime || prevDataLengthRef.current === 0) {
      // 非リアルタイム or 初回: 全データセット
      let cumulative = 0
      const equityData: LineData[] = []
      const returnData: { time: string; value: number; color: string }[] = []

      monthlyResults.forEach((result) => {
        cumulative += result.pnl
        const timeStr = `${result.year}-${String(result.month).padStart(2, '0')}-01`

        equityData.push({
          time: timeStr,
          value: cumulative,
        })

        returnData.push({
          time: timeStr,
          value: result.return_pct,
          color: result.return_pct >= 0 ? '#4ade80' : '#ef4444',
        })
      })

      equitySeriesRef.current.setData(equityData)
      returnSeriesRef.current.setData(returnData)
      prevDataLengthRef.current = monthlyResults.length
      lastCumulativeRef.current = cumulative
    }
  }, [monthlyResults, isRealtime])

  if (isLoading) {
    return (
      <div className="bg-gray-800 rounded-lg p-4 h-[400px] flex flex-col items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500 mb-4" />
        <p className="text-gray-400 text-sm">
          {isRealtime ? 'データ待機中...' : '読み込み中...'}
        </p>
      </div>
    )
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-lg font-semibold mb-4 text-white">
        エクイティカーブ & 月次リターン
      </h3>
      <div ref={chartContainerRef} className="w-full" />
      <div className="flex gap-4 mt-2 text-sm text-gray-400">
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 bg-green-400 rounded" />
          <span>累積損益</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-3 bg-blue-500 rounded" />
          <span>月次リターン(%)</span>
        </div>
      </div>
    </div>
  )
}
