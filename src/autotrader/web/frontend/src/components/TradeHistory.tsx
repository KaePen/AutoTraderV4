/** トレード履歴コンポーネント */

import { useState } from 'react'
import type { Trade, TradeSummary, ExitReason } from '../types'

interface TradeHistoryProps {
  trades: Trade[]
  summary: TradeSummary | null
  isLoading: boolean
}

/** トレード履歴パネル */
export function TradeHistory({ trades, summary, isLoading }: TradeHistoryProps) {
  const [isExpanded, setIsExpanded] = useState(true)

  if (isLoading) {
    return (
      <div className="card animate-pulse">
        <div className="h-5 bg-gray-700 rounded w-32 mb-4" />
        <div className="h-48 bg-gray-700 rounded" />
      </div>
    )
  }

  return (
    <div className="card">
      {/* ヘッダー */}
      <div className="flex items-center justify-between mb-3">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-2 text-sm font-semibold text-gray-300 uppercase tracking-wider hover:text-white transition-colors"
        >
          <svg
            className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-0' : '-rotate-90'}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
          Trade History
        </button>

        {/* サマリーメトリクス */}
        {summary && (
          <div className="flex items-center gap-4 text-xs tabular-nums">
            <SummaryChip
              label="WR"
              value={`${summary.win_rate.toFixed(1)}%`}
              variant={summary.win_rate >= 50 ? 'profit' : 'loss'}
            />
            <SummaryChip
              label="PF"
              value={summary.profit_factor.toFixed(2)}
              variant={summary.profit_factor >= 1 ? 'profit' : 'loss'}
            />
            <SummaryChip
              label="Net"
              value={formatCurrency(summary.net_profit)}
              variant={summary.net_profit >= 0 ? 'profit' : 'loss'}
            />
            <span className="text-gray-500">
              {summary.total_trades} trades ({summary.winning_trades}W/{summary.losing_trades}L)
            </span>
          </div>
        )}
      </div>

      {/* テーブル */}
      {isExpanded && (
        <>
          {trades.length === 0 ? (
            <div className="flex items-center justify-center h-16 text-gray-500 text-sm">
              トレードなし
            </div>
          ) : (
            <div className="overflow-x-auto max-h-80">
              <table className="table">
                <thead className="sticky top-0 bg-gray-800 z-10">
                  <tr>
                    <th>日時</th>
                    <th>方向</th>
                    <th>Lot</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>理由</th>
                    <th className="text-right">損益</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((trade) => (
                    <TradeRow key={trade.trade_id} trade={trade} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

interface SummaryChipProps {
  label: string
  value: string
  variant: 'profit' | 'loss'
}

function SummaryChip({ label, value, variant }: SummaryChipProps) {
  const color = variant === 'profit' ? 'text-green-400' : 'text-red-400'
  return (
    <span className="text-gray-500">
      {label}: <span className={color}>{value}</span>
    </span>
  )
}

interface TradeRowProps {
  trade: Trade
}

function TradeRow({ trade }: TradeRowProps) {
  const pnl = trade.profit_loss ?? 0
  const isProfit = pnl >= 0

  return (
    <tr>
      <td className="text-xs text-gray-400 whitespace-nowrap tabular-nums">
        {formatDateTime(trade.closed_at ?? trade.opened_at)}
      </td>
      <td>
        <span className={`text-xs font-bold ${
          trade.signal_type === 'BUY' ? 'text-green-400' : 'text-red-400'
        }`}>
          {trade.signal_type}
        </span>
      </td>
      <td className="text-xs text-gray-400 tabular-nums">
        {trade.volume.toFixed(2)}
      </td>
      <td className="text-xs tabular-nums">{trade.entry_price.toFixed(3)}</td>
      <td className="text-xs tabular-nums">{trade.exit_price?.toFixed(3) ?? '-'}</td>
      <td>
        {trade.exit_reason && (
          <ExitReasonBadge reason={trade.exit_reason} />
        )}
      </td>
      <td className={`text-right text-xs font-medium tabular-nums ${isProfit ? 'text-green-400' : 'text-red-400'}`}>
        {isProfit ? '+' : ''}{formatCurrency(pnl)}
        {trade.profit_loss_pips !== null && (
          <span className="text-gray-500 ml-1">
            ({trade.profit_loss_pips.toFixed(1)}p)
          </span>
        )}
      </td>
    </tr>
  )
}

const EXIT_REASON_CONFIG: Record<ExitReason, { label: string; color: string }> = {
  STOP_LOSS: { label: 'SL', color: 'bg-red-900/40 text-red-400 border-red-800/50' },
  TAKE_PROFIT: { label: 'TP', color: 'bg-green-900/40 text-green-400 border-green-800/50' },
  TRAILING_STOP: { label: 'TSL', color: 'bg-cyan-900/40 text-cyan-400 border-cyan-800/50' },
  TIME_EXIT: { label: 'TIME', color: 'bg-gray-700 text-gray-400 border-gray-600' },
  MANUAL: { label: 'MAN', color: 'bg-gray-700 text-gray-400 border-gray-600' },
  SIGNAL_REVERSAL: { label: 'REV', color: 'bg-yellow-900/40 text-yellow-400 border-yellow-800/50' },
  FORCE_CLOSE: { label: 'FORCE', color: 'bg-orange-900/40 text-orange-400 border-orange-800/50' },
  STAGNATION: { label: 'STAG', color: 'bg-purple-900/40 text-purple-400 border-purple-800/50' },
  TP_EARLY: { label: 'TP_E', color: 'bg-emerald-900/40 text-emerald-400 border-emerald-800/50' },
  INSURANCE: { label: 'INS', color: 'bg-blue-900/40 text-blue-400 border-blue-800/50' },
}

function ExitReasonBadge({ reason }: { reason: ExitReason }) {
  const config = EXIT_REASON_CONFIG[reason] ?? { label: reason, color: 'bg-gray-700 text-gray-400' }
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border ${config.color}`}>
      {config.label}
    </span>
  )
}

function formatDateTime(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleDateString('ja-JP', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('ja-JP', {
    style: 'currency',
    currency: 'JPY',
    maximumFractionDigits: 0,
  }).format(value)
}
