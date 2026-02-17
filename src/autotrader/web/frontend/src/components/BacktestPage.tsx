/** バックテストページ */

import { useState, useCallback } from 'react'
import { BacktestConfigForm, type BacktestConfig } from './BacktestConfigForm'
import { BacktestResults } from './BacktestResults'
import { BacktestHistory } from './BacktestHistory'
import { BacktestChart } from './BacktestChart'
import {
  useBacktestWebSocket,
  type BacktestMetrics,
  type TradeEvent,
} from '../hooks/useBacktestWebSocket'

interface BacktestStatus {
  running: boolean
  progress: number
  current_year: number | null
}

interface MonthResult {
  year: number
  month: number
  trades: number
  pnl: number
  return_pct: number
}

interface BacktestResult {
  total_trades: number
  win_rate: number
  profit_factor: number
  net_profit: number
  max_drawdown: number
  sharpe_ratio: number
  annual_return: number
  yearly_results: {
    year: number
    trades: number
    win_rate: number
    profit_factor: number
    net_profit: number
    max_drawdown: number
    sharpe: number
  }[]
  monthly_results: MonthResult[]
}

type PageTab = 'run' | 'history'

/** バックテストページ */
export function BacktestPage() {
  const [activeTab, setActiveTab] = useState<PageTab>('run')
  const [status, setStatus] = useState<BacktestStatus>({ running: false, progress: 0, current_year: null })
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isCancelling, setIsCancelling] = useState(false)

  // リアルタイムデータ
  const [realtimeMetrics, setRealtimeMetrics] = useState<BacktestMetrics | null>(null)
  const [realtimeMonthly, setRealtimeMonthly] = useState<MonthResult[]>([])
  const [recentTrades, setRecentTrades] = useState<TradeEvent[]>([])

  // 結果取得
  const fetchResults = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/backtest/results')
      const data = await response.json()
      if (data.data) {
        setResult(data.data)
      }
    } catch {
      // 結果取得失敗は無視
    }
  }, [])

  // WebSocket接続
  useBacktestWebSocket({
    onStart: () => {
      setError(null)
      setRecentTrades([])
      setRealtimeMetrics(null)
      setRealtimeMonthly([])
      setResult(null)
    },
    onEnd: (cancelled) => {
      setIsCancelling(false)
      setStatus((prev) => ({ ...prev, running: false, progress: 100 }))
      if (cancelled) {
        setError('バックテストがキャンセルされました')
      } else {
        fetchResults()
      }
    },
    onProgress: (progress) => {
      setStatus((prev) => ({ ...prev, progress: progress.percentage }))
    },
    onYearStart: (year) => {
      setStatus((prev) => ({ ...prev, current_year: year }))
    },
    onMetrics: (metrics) => {
      setRealtimeMetrics(metrics)
    },
    onMonthEnd: (month) => {
      setRealtimeMonthly((prev) => [...prev, month])
    },
    onTradeClose: (trade) => {
      setRecentTrades((prev) => [trade, ...prev].slice(0, 50))
    },
  })

  // バックテスト開始
  const handleStart = async (config: BacktestConfig) => {
    try {
      setError(null)
      setResult(null)
      setRecentTrades([])
      setRealtimeMetrics(null)
      setRealtimeMonthly([])

      // null値を除外してリクエストボディ構築
      const body: Record<string, unknown> = {
        start_year: config.start_year,
        end_year: config.end_year,
        initial_balance: config.initial_balance,
        volume: config.volume,
        use_short_timeframe: config.use_short_timeframe,
      }

      const overrideKeys: (keyof BacktestConfig)[] = [
        'range_day_bbw_threshold', 'range_day_score_premium',
        'weak_hours_enabled', 'weak_hours_score_premium',
        'use_dynamic_lot', 'base_risk_pct', 'max_lot_per_trade',
        'enable_position_manager', 'stagnation_min_mfe_r', 'insurance_trigger_r',
      ]

      for (const key of overrideKeys) {
        if (config[key] !== null && config[key] !== undefined) {
          body[key] = config[key]
        }
      }

      const response = await fetch('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'バックテスト開始に失敗しました')
      }

      setStatus({ running: true, progress: 0, current_year: null })
      pollStatus()
    } catch (err) {
      setError(err instanceof Error ? err.message : '不明なエラー')
    }
  }

  // キャンセル
  const handleCancel = async () => {
    try {
      setIsCancelling(true)
      const response = await fetch('/api/v1/backtest/cancel', { method: 'POST' })
      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'キャンセルに失敗しました')
      }
    } catch (err) {
      setIsCancelling(false)
      setError(err instanceof Error ? err.message : '不明なエラー')
    }
  }

  // ポーリング
  const pollStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/backtest/status')
      const data = await response.json()
      const s = data.data
      setStatus({
        running: s.running,
        progress: s.progress,
        current_year: s.current_year,
      })

      if (s.running) {
        setTimeout(pollStatus, 2000)
      } else if (s.error) {
        setError(s.error)
      } else {
        fetchResults()
      }
    } catch {
      // ポーリング失敗は無視
    }
  }, [fetchResults])

  return (
    <div className="space-y-4">
      {/* タブ */}
      <div className="flex gap-2">
        <button
          onClick={() => setActiveTab('run')}
          className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            activeTab === 'run'
              ? 'bg-gray-700 text-white'
              : 'text-gray-400 hover:text-white hover:bg-gray-800'
          }`}
        >
          Run
        </button>
        <button
          onClick={() => setActiveTab('history')}
          className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            activeTab === 'history'
              ? 'bg-gray-700 text-white'
              : 'text-gray-400 hover:text-white hover:bg-gray-800'
          }`}
        >
          History
        </button>
      </div>

      {activeTab === 'run' ? (
        <>
          {/* 設定フォーム */}
          <BacktestConfigForm
            onSubmit={handleStart}
            disabled={status.running}
          />

          {/* キャンセルボタン */}
          {status.running && (
            <button
              onClick={handleCancel}
              disabled={isCancelling}
              className={`w-full py-2 rounded-lg font-semibold text-sm transition-all ${
                isCancelling
                  ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                  : 'bg-red-600/80 hover:bg-red-600 text-white'
              }`}
            >
              {isCancelling ? '停止中...' : '停止'}
            </button>
          )}

          {/* 進捗 */}
          {status.running && (
            <div className="card">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm text-gray-400">
                  {status.current_year ? `${status.current_year}年 処理中` : '準備中...'}
                </span>
                <span className="text-sm font-medium tabular-nums text-gray-300">
                  {status.progress.toFixed(1)}%
                </span>
              </div>
              <div className="w-full h-2 bg-gray-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-blue-500 to-cyan-500 rounded-full transition-all duration-500"
                  style={{ width: `${status.progress}%` }}
                />
              </div>
            </div>
          )}

          {/* リアルタイムメトリクス */}
          {status.running && realtimeMetrics && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <RealtimeKpi label="残高" value={`¥${realtimeMetrics.balance.toLocaleString()}`} />
              <RealtimeKpi label="取引数" value={String(realtimeMetrics.total_trades)} />
              <RealtimeKpi
                label="勝率"
                value={`${realtimeMetrics.win_rate.toFixed(1)}%`}
                variant={realtimeMetrics.win_rate >= 55 ? 'profit' : 'neutral'}
              />
              <RealtimeKpi
                label="最大DD"
                value={`${realtimeMetrics.max_drawdown.toFixed(2)}%`}
                variant="loss"
              />
            </div>
          )}

          {/* リアルタイムチャート */}
          {status.running && realtimeMonthly.length > 0 && (
            <BacktestChart
              monthlyResults={realtimeMonthly}
              isLoading={false}
              isRealtime={true}
            />
          )}

          {/* エラー */}
          {error && (
            <div className="card border-red-800/50 bg-red-950/20">
              <p className="text-red-400 text-sm">{error}</p>
            </div>
          )}

          {/* リアルタイムトレード（実行中） */}
          {recentTrades.length > 0 && status.running && (
            <RecentTradesTable trades={recentTrades} />
          )}

          {/* 結果 */}
          {result && !status.running && (
            <BacktestResults result={result} />
          )}
        </>
      ) : (
        <BacktestHistory />
      )}
    </div>
  )
}

/** リアルタイムKPI */
function RealtimeKpi({
  label,
  value,
  variant = 'neutral',
}: {
  label: string
  value: string
  variant?: 'profit' | 'loss' | 'neutral'
}) {
  const colors = {
    profit: 'text-green-400',
    loss: 'text-red-400',
    neutral: 'text-gray-100',
  }

  return (
    <div className="card py-2.5">
      <p className="text-[10px] text-gray-500 uppercase">{label}</p>
      <p className={`text-lg font-bold tabular-nums ${colors[variant]}`}>{value}</p>
    </div>
  )
}

/** 直近トレードテーブル */
function RecentTradesTable({ trades }: { trades: TradeEvent[] }) {
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Recent Trades
        </h3>
        <span className="text-xs text-gray-500">{trades.length} 件</span>
      </div>
      <div className="overflow-x-auto max-h-64">
        <table className="table">
          <thead className="sticky top-0 bg-gray-800">
            <tr>
              <th>Dir</th>
              <th>Vol</th>
              <th>Entry</th>
              <th>Exit</th>
              <th className="text-right">P&L</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade, idx) => {
              const pnl = trade.profit_loss ?? 0
              const isProfit = pnl >= 0
              return (
                <tr key={`${trade.trade_id}-${idx}`}>
                  <td className={`font-bold ${trade.direction === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                    {trade.direction === 'BUY' ? 'B' : 'S'}
                  </td>
                  <td className="text-xs text-gray-400 tabular-nums">{trade.volume.toFixed(2)}</td>
                  <td className="text-xs tabular-nums">
                    <div>{trade.entry_price.toFixed(3)}</div>
                    {trade.opened_at && (
                      <div className="text-[10px] text-gray-600">
                        {new Date(trade.opened_at).toLocaleDateString('ja-JP', { month: 'numeric', day: 'numeric' })}
                      </div>
                    )}
                  </td>
                  <td className="text-xs tabular-nums">
                    <div>{trade.exit_price?.toFixed(3) ?? '-'}</div>
                    <div className="text-[10px] text-gray-600">
                      {new Date(trade.timestamp).toLocaleDateString('ja-JP', { month: 'numeric', day: 'numeric' })}
                    </div>
                  </td>
                  <td className={`text-right text-xs font-medium tabular-nums ${isProfit ? 'text-green-400' : 'text-red-400'}`}>
                    {isProfit ? '+' : ''}¥{Math.round(pnl).toLocaleString()}
                  </td>
                  <td className="text-[10px] text-gray-500">{formatExitReason(trade.exit_reason)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function formatExitReason(reason?: string): string {
  if (!reason) return '-'
  const map: Record<string, string> = {
    STOP_LOSS: 'SL',
    TAKE_PROFIT: 'TP',
    TRAILING_STOP: 'TSL',
    SIGNAL_REVERSAL: 'REV',
    FORCE_CLOSE: 'FORCE',
    STAGNATION: 'STAG',
    TP_EARLY: 'TP_E',
    INSURANCE: 'INS',
    TIME_EXIT: 'TIME',
  }
  return map[reason] ?? reason
}
