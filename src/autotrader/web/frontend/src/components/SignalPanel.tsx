/** シグナルパネルコンポーネント */

import type { Signal, ConfidenceLevel } from '../types'

interface SignalPanelProps {
  signals: Signal[]
  isLoading: boolean
}

/** シグナルパネル */
export function SignalPanel({ signals, isLoading }: SignalPanelProps) {
  if (isLoading) {
    return (
      <div className="card animate-pulse">
        <div className="h-5 bg-gray-700 rounded w-32 mb-4" />
        <div className="space-y-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-24 bg-gray-700 rounded" />
          ))}
        </div>
      </div>
    )
  }

  const activeSignals = signals.filter((s) => s.signal_type !== 'HOLD')
  const holdSignals = signals.filter((s) => s.signal_type === 'HOLD')

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Signals
        </h2>
        <span className="text-xs text-gray-500">{signals.length} 件</span>
      </div>

      {signals.length === 0 ? (
        <div className="flex items-center justify-center h-20 text-gray-500 text-sm">
          シグナル待機中...
        </div>
      ) : (
        <div className="space-y-2">
          {activeSignals.map((signal) => (
            <SignalCard key={signal.signal_id} signal={signal} />
          ))}
          {holdSignals.length > 0 && activeSignals.length > 0 && (
            <div className="border-t border-gray-700 pt-2 mt-2" />
          )}
          {holdSignals.map((signal) => (
            <SignalCard key={signal.signal_id} signal={signal} />
          ))}
        </div>
      )}
    </div>
  )
}

interface SignalCardProps {
  signal: Signal
}

function SignalCard({ signal }: SignalCardProps) {
  const borderColors = {
    BUY: 'border-l-green-500',
    SELL: 'border-l-red-500',
    HOLD: 'border-l-gray-600',
  }

  const bgColors = {
    BUY: 'bg-green-950/30',
    SELL: 'bg-red-950/30',
    HOLD: 'bg-gray-800/50',
  }

  const dirColors = {
    BUY: 'text-green-400',
    SELL: 'text-red-400',
    HOLD: 'text-gray-500',
  }

  return (
    <div
      className={`border-l-2 ${borderColors[signal.signal_type]} ${bgColors[signal.signal_type]} rounded-r-lg p-3`}
    >
      {/* ヘッダー行 */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`text-sm font-bold ${dirColors[signal.signal_type]}`}>
            {signal.signal_type}
          </span>
          <span className="text-xs text-gray-500">{signal.timeframe}</span>
        </div>
        <ConfidenceBadge
          confidence={signal.confidence}
          level={signal.confidence_level}
        />
      </div>

      {/* スコアバー */}
      <div className="mb-2">
        <div className="flex items-center justify-between text-xs mb-1">
          <span className="text-gray-500">Score</span>
          <span className="text-gray-400 tabular-nums">
            {(signal.confidence * 10).toFixed(1)}
          </span>
        </div>
        <div className="w-full h-1.5 bg-gray-700 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              signal.confidence >= 0.7
                ? 'bg-green-500'
                : signal.confidence >= 0.4
                  ? 'bg-yellow-500'
                  : 'bg-red-500'
            }`}
            style={{ width: `${signal.confidence * 100}%` }}
          />
        </div>
      </div>

      {/* 説明 */}
      {signal.reasoning && (
        <p className="text-xs text-gray-400 mb-2 line-clamp-2">
          {signal.reasoning}
        </p>
      )}

      {/* SL/TP */}
      <div className="flex items-center gap-3 text-xs tabular-nums">
        {signal.stop_loss !== null && (
          <span className="text-red-400/70">
            SL {signal.stop_loss.toFixed(3)}
          </span>
        )}
        {signal.take_profit !== null && (
          <span className="text-green-400/70">
            TP {signal.take_profit.toFixed(3)}
          </span>
        )}
        <span className="text-gray-600 ml-auto">{formatTime(signal.created_at)}</span>
      </div>
    </div>
  )
}

interface ConfidenceBadgeProps {
  confidence: number
  level: ConfidenceLevel
}

function ConfidenceBadge({ confidence, level }: ConfidenceBadgeProps) {
  const colors = {
    HIGH: 'bg-green-900/40 text-green-400 border-green-700/50',
    MEDIUM: 'bg-yellow-900/40 text-yellow-400 border-yellow-700/50',
    LOW: 'bg-red-900/40 text-red-400 border-red-700/50',
  }

  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${colors[level]}`}>
      {(confidence * 100).toFixed(0)}%
    </span>
  )
}

function formatTime(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleTimeString('ja-JP', {
    hour: '2-digit',
    minute: '2-digit',
  })
}
