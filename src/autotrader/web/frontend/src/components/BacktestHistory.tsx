/** バックテスト履歴コンポーネント */

import { useState, useEffect } from 'react'
import type { BacktestHistoryItem } from '../types'
import { getBacktestHistory } from '../api/client'

interface BacktestHistoryProps {
  onSelect?: (backtestId: number) => void
}

/** バックテスト履歴一覧 */
export function BacktestHistory({ onSelect }: BacktestHistoryProps) {
  const [history, setHistory] = useState<BacktestHistoryItem[]>([])
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const fetchHistory = async () => {
      setIsLoading(true)
      try {
        const data = await getBacktestHistory(20)
        setHistory(data)
      } catch {
        setHistory([])
      } finally {
        setIsLoading(false)
      }
    }
    fetchHistory()
  }, [])

  if (isLoading) {
    return (
      <div className="card animate-pulse">
        <div className="h-5 bg-gray-700 rounded w-32 mb-3" />
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-12 bg-gray-700 rounded" />
          ))}
        </div>
      </div>
    )
  }

  if (history.length === 0) {
    return (
      <div className="card">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
          History
        </h3>
        <p className="text-gray-500 text-sm text-center py-4">実行履歴なし</p>
      </div>
    )
  }

  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
        History
      </h3>
      <div className="overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Period</th>
              <th className="text-right">Trades</th>
              <th className="text-right">WR</th>
              <th className="text-right">PF</th>
              <th className="text-right">Net</th>
              <th className="text-right">DD</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {history.map((item) => (
              <tr
                key={item.id}
                onClick={() => onSelect?.(item.id)}
                className={onSelect ? 'cursor-pointer' : ''}
              >
                <td className="text-sm font-medium">{item.name || `#${item.id}`}</td>
                <td className="text-xs text-gray-400">
                  {item.start_date} ~ {item.end_date}
                </td>
                <td className="text-right tabular-nums">{item.total_trades}</td>
                <td className={`text-right tabular-nums ${
                  (item.win_rate ?? 0) >= 55 ? 'text-green-400' : 'text-gray-300'
                }`}>
                  {item.win_rate?.toFixed(1) ?? '-'}%
                </td>
                <td className={`text-right tabular-nums ${
                  (item.profit_factor ?? 0) >= 1.5 ? 'text-green-400' : 'text-gray-300'
                }`}>
                  {item.profit_factor?.toFixed(2) ?? '-'}
                </td>
                <td className={`text-right tabular-nums ${
                  (item.net_profit ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'
                }`}>
                  {item.net_profit !== null ? `¥${item.net_profit.toLocaleString()}` : '-'}
                </td>
                <td className="text-right tabular-nums text-red-400">
                  {item.max_drawdown_pct?.toFixed(2) ?? '-'}%
                </td>
                <td>
                  <StatusBadge status={item.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const configs: Record<string, { label: string; color: string }> = {
    completed: { label: 'Done', color: 'bg-green-900/40 text-green-400 border-green-800/50' },
    running: { label: 'Running', color: 'bg-blue-900/40 text-blue-400 border-blue-800/50' },
    failed: { label: 'Failed', color: 'bg-red-900/40 text-red-400 border-red-800/50' },
    cancelled: { label: 'Cancelled', color: 'bg-yellow-900/40 text-yellow-400 border-yellow-800/50' },
  }

  const config = configs[status] ?? { label: status, color: 'bg-gray-700 text-gray-400' }

  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border ${config.color}`}>
      {config.label}
    </span>
  )
}
