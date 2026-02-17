/** ポジションテーブルコンポーネント */

import type { Position } from '../types'

interface PositionTableProps {
  positions: Position[]
  isLoading: boolean
}

/** ポジションテーブル */
export function PositionTable({ positions, isLoading }: PositionTableProps) {
  if (isLoading) {
    return (
      <div className="card animate-pulse">
        <div className="h-5 bg-gray-700 rounded w-40 mb-4" />
        <div className="h-32 bg-gray-700 rounded" />
      </div>
    )
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Positions
        </h2>
        <span className="text-xs text-gray-500">
          {positions.length > 0 ? `${positions.length} open` : 'no open'}
        </span>
      </div>

      {positions.length === 0 ? (
        <div className="flex items-center justify-center h-16 text-gray-500 text-sm">
          ポジションなし
        </div>
      ) : (
        <div className="space-y-2">
          {positions.map((pos) => (
            <PositionCard key={pos.position_id} position={pos} />
          ))}
        </div>
      )}
    </div>
  )
}

interface PositionCardProps {
  position: Position
}

function PositionCard({ position }: PositionCardProps) {
  const isProfit = position.unrealized_pnl >= 0
  const borderColor = position.signal_type === 'BUY' ? 'border-l-green-500' : 'border-l-red-500'
  const dirColor = position.signal_type === 'BUY' ? 'text-green-400' : 'text-red-400'

  // SL/TP進捗計算
  const slTpProgress = calculateSlTpProgress(position)
  const holdTime = getHoldTime(position.opened_at)

  return (
    <div className={`border-l-2 ${borderColor} bg-gray-800/50 rounded-r-lg p-3`}>
      {/* ヘッダー */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`text-sm font-bold ${dirColor}`}>
            {position.signal_type}
          </span>
          <span className="text-xs text-gray-500">{position.symbol}</span>
          <span className="text-xs text-gray-600">{position.volume.toFixed(2)} lot</span>
        </div>
        <span className={`text-sm font-bold tabular-nums ${isProfit ? 'text-green-400' : 'text-red-400'}`}>
          {isProfit ? '+' : ''}{formatCurrency(position.unrealized_pnl)}
        </span>
      </div>

      {/* 価格 */}
      <div className="flex items-center gap-4 text-xs tabular-nums mb-2">
        <span className="text-gray-400">
          Entry {position.entry_price.toFixed(3)}
        </span>
        <span className="text-gray-300 font-medium">
          Now {position.current_price.toFixed(3)}
        </span>
        <span className={`${isProfit ? 'text-green-400' : 'text-red-400'}`}>
          {position.unrealized_pnl_pips >= 0 ? '+' : ''}{position.unrealized_pnl_pips.toFixed(1)} pips
        </span>
      </div>

      {/* SL/TP進捗バー */}
      {slTpProgress !== null && (
        <div className="mb-2">
          <div className="w-full h-1.5 bg-gray-700 rounded-full relative overflow-hidden">
            {/* SL側（赤） */}
            <div
              className="absolute top-0 left-0 h-full bg-red-500/30 rounded-l-full"
              style={{ width: `${slTpProgress.slPct}%` }}
            />
            {/* TP側（緑） */}
            <div
              className="absolute top-0 right-0 h-full bg-green-500/30 rounded-r-full"
              style={{ width: `${slTpProgress.tpPct}%` }}
            />
            {/* 現在位置マーカー */}
            <div
              className={`absolute top-0 w-0.5 h-full ${isProfit ? 'bg-green-400' : 'bg-red-400'}`}
              style={{ left: `${slTpProgress.currentPct}%` }}
            />
          </div>
          <div className="flex justify-between text-[10px] text-gray-600 mt-0.5">
            <span>SL {position.stop_loss?.toFixed(3) ?? '-'}</span>
            <span>TP {position.take_profit?.toFixed(3) ?? '-'}</span>
          </div>
        </div>
      )}

      {/* 保有時間 */}
      <div className="text-[10px] text-gray-600">
        {holdTime}
      </div>
    </div>
  )
}

interface SlTpProgress {
  slPct: number
  tpPct: number
  currentPct: number
}

function calculateSlTpProgress(position: Position): SlTpProgress | null {
  if (position.stop_loss === null || position.take_profit === null) return null

  const sl = position.stop_loss
  const tp = position.take_profit
  const range = Math.abs(tp - sl)
  if (range === 0) return null

  const currentPct = Math.max(0, Math.min(100,
    ((position.current_price - Math.min(sl, tp)) / range) * 100
  ))

  // BUYの場合: SL < entry < TP
  // SELLの場合: TP < entry < SL
  const isBuy = position.signal_type === 'BUY'
  return {
    slPct: isBuy ? 30 : 70,
    tpPct: isBuy ? 30 : 30,
    currentPct: isBuy ? currentPct : 100 - currentPct,
  }
}

function getHoldTime(openedAt: string): string {
  const opened = new Date(openedAt)
  const now = new Date()
  const diffMs = now.getTime() - opened.getTime()
  const diffMin = Math.floor(diffMs / 60000)

  if (diffMin < 60) return `${diffMin}m`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}h ${diffMin % 60}m`
  const diffDay = Math.floor(diffHour / 24)
  return `${diffDay}d ${diffHour % 24}h`
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('ja-JP', {
    style: 'currency',
    currency: 'JPY',
    maximumFractionDigits: 0,
  }).format(value)
}
