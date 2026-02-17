/** 指標パネルコンポーネント */

import { useState, useEffect } from 'react'
import type { Indicators, Timeframe } from '../types'
import { getIndicators } from '../api/client'

interface IndicatorPanelProps {
  symbol: string
  timeframe: Timeframe
}

/** 指標パネル - コンパクトゲージ表示 */
export function IndicatorPanel({ symbol, timeframe }: IndicatorPanelProps) {
  const [indicators, setIndicators] = useState<Indicators | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const fetchIndicators = async () => {
      setIsLoading(true)
      try {
        const data = await getIndicators(symbol, timeframe)
        setIndicators(data)
      } catch {
        setIndicators(null)
      } finally {
        setIsLoading(false)
      }
    }

    fetchIndicators()
    const interval = setInterval(fetchIndicators, 30000)
    return () => clearInterval(interval)
  }, [symbol, timeframe])

  if (isLoading) {
    return (
      <div className="card animate-pulse">
        <div className="h-5 bg-gray-700 rounded w-24 mb-3" />
        <div className="grid grid-cols-4 gap-3">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="h-16 bg-gray-700 rounded" />
          ))}
        </div>
      </div>
    )
  }

  if (!indicators) {
    return (
      <div className="card">
        <p className="text-gray-500 text-sm">指標データなし</p>
      </div>
    )
  }

  return (
    <div className="card">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
        Indicators ({timeframe})
      </h2>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {/* オシレーター */}
        <GaugeIndicator
          label="RSI"
          value={indicators.rsi}
          min={0}
          max={100}
          zones={[
            { from: 0, to: 30, color: 'green' },
            { from: 30, to: 70, color: 'neutral' },
            { from: 70, to: 100, color: 'red' },
          ]}
          format={(v) => v.toFixed(1)}
        />
        <GaugeIndicator
          label="ADX"
          value={indicators.adx}
          min={0}
          max={60}
          zones={[
            { from: 0, to: 20, color: 'red' },
            { from: 20, to: 40, color: 'neutral' },
            { from: 40, to: 60, color: 'green' },
          ]}
          format={(v) => v.toFixed(1)}
        />

        {/* MACD */}
        <ValueIndicator
          label="MACD"
          value={indicators.macd}
          subLabel="Signal"
          subValue={indicators.macd_signal}
          color={indicators.macd !== null && indicators.macd > 0 ? 'green' : 'red'}
        />
        <ValueIndicator
          label="MACD Hist"
          value={indicators.macd_hist}
          color={indicators.macd_hist !== null && indicators.macd_hist > 0 ? 'green' : 'red'}
        />

        {/* DI */}
        <DualIndicator
          label="+DI / -DI"
          value1={indicators.plus_di}
          value2={indicators.minus_di}
          label1="+DI"
          label2="-DI"
          color1="green"
          color2="red"
        />

        {/* BB */}
        <BollingerIndicator
          upper={indicators.bb_upper}
          middle={indicators.bb_middle}
          lower={indicators.bb_lower}
        />

        {/* ATR */}
        <ValueIndicator
          label="ATR"
          value={indicators.atr}
          format={(v) => v.toFixed(4)}
        />

        {/* EMA */}
        <DualIndicator
          label="EMA"
          value1={indicators.ema_fast}
          value2={indicators.ema_slow}
          label1="Fast"
          label2="Slow"
          color1={
            indicators.ema_fast !== null && indicators.ema_slow !== null && indicators.ema_fast > indicators.ema_slow
              ? 'green' : 'red'
          }
          color2="neutral"
        />
      </div>
    </div>
  )
}

/** ゲージ型指標 */
interface Zone {
  from: number
  to: number
  color: 'green' | 'red' | 'neutral'
}

interface GaugeIndicatorProps {
  label: string
  value: number | null
  min: number
  max: number
  zones: Zone[]
  format?: (v: number) => string
}

function GaugeIndicator({ label, value, min, max, zones, format = (v) => v.toFixed(1) }: GaugeIndicatorProps) {
  const pct = value !== null ? Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100)) : 0
  const zone = value !== null ? zones.find((z) => value >= z.from && value < z.to) : null
  const zoneColor = zone?.color ?? 'neutral'

  const colors = {
    green: 'text-green-400',
    red: 'text-red-400',
    neutral: 'text-gray-300',
  }

  const barColors = {
    green: 'bg-green-500',
    red: 'bg-red-500',
    neutral: 'bg-gray-500',
  }

  return (
    <div className="bg-gray-800/50 rounded-lg p-2.5">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] text-gray-500 uppercase">{label}</span>
        <span className={`text-sm font-bold tabular-nums ${colors[zoneColor]}`}>
          {value !== null ? format(value) : '-'}
        </span>
      </div>
      <div className="w-full h-1 bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${barColors[zoneColor]}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

/** 値表示型指標 */
interface ValueIndicatorProps {
  label: string
  value: number | null
  subLabel?: string
  subValue?: number | null
  color?: 'green' | 'red' | 'neutral'
  format?: (v: number) => string
}

function ValueIndicator({
  label,
  value,
  subLabel,
  subValue,
  color = 'neutral',
  format = (v) => v.toFixed(4),
}: ValueIndicatorProps) {
  const colors = {
    green: 'text-green-400',
    red: 'text-red-400',
    neutral: 'text-gray-300',
  }

  return (
    <div className="bg-gray-800/50 rounded-lg p-2.5">
      <span className="text-[10px] text-gray-500 uppercase">{label}</span>
      <p className={`text-sm font-bold tabular-nums ${colors[color]}`}>
        {value !== null ? format(value) : '-'}
      </p>
      {subLabel && (
        <p className="text-[10px] text-gray-500 tabular-nums">
          {subLabel}: {subValue !== null && subValue !== undefined ? format(subValue) : '-'}
        </p>
      )}
    </div>
  )
}

/** デュアル値指標 */
interface DualIndicatorProps {
  label: string
  value1: number | null
  value2: number | null
  label1: string
  label2: string
  color1?: 'green' | 'red' | 'neutral'
  color2?: 'green' | 'red' | 'neutral'
}

function DualIndicator({
  label,
  value1,
  value2,
  label1,
  label2,
  color1 = 'neutral',
  color2 = 'neutral',
}: DualIndicatorProps) {
  const colors = {
    green: 'text-green-400',
    red: 'text-red-400',
    neutral: 'text-gray-300',
  }

  return (
    <div className="bg-gray-800/50 rounded-lg p-2.5">
      <span className="text-[10px] text-gray-500 uppercase">{label}</span>
      <div className="flex items-baseline gap-2 mt-0.5">
        <span className={`text-xs font-bold tabular-nums ${colors[color1]}`}>
          {label1} {value1?.toFixed(1) ?? '-'}
        </span>
        <span className={`text-xs tabular-nums ${colors[color2]}`}>
          {label2} {value2?.toFixed(1) ?? '-'}
        </span>
      </div>
    </div>
  )
}

/** ボリンジャーバンド */
interface BollingerIndicatorProps {
  upper: number | null
  middle: number | null
  lower: number | null
}

function BollingerIndicator({ upper, middle, lower }: BollingerIndicatorProps) {
  const bbw = upper !== null && lower !== null && middle !== null && middle !== 0
    ? ((upper - lower) / middle * 100).toFixed(3)
    : null

  return (
    <div className="bg-gray-800/50 rounded-lg p-2.5">
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-gray-500 uppercase">BB</span>
        {bbw && (
          <span className="text-[10px] text-gray-500">BBW: {bbw}%</span>
        )}
      </div>
      <div className="text-xs tabular-nums mt-0.5 space-y-0">
        <div className="flex justify-between">
          <span className="text-gray-500">U</span>
          <span className="text-gray-400">{upper?.toFixed(3) ?? '-'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">M</span>
          <span className="text-gray-300 font-medium">{middle?.toFixed(3) ?? '-'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">L</span>
          <span className="text-gray-400">{lower?.toFixed(3) ?? '-'}</span>
        </div>
      </div>
    </div>
  )
}
