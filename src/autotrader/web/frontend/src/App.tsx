/** メインアプリケーション */

import { useState, useEffect, useCallback } from 'react'
import { Layout } from './components/Layout'
import { AccountInfo } from './components/AccountInfo'
import { Chart } from './components/Chart'
import { SignalPanel } from './components/SignalPanel'
import { PositionTable } from './components/PositionTable'
import { TradeHistory } from './components/TradeHistory'
import { IndicatorPanel } from './components/IndicatorPanel'
import { NotificationBell } from './components/NotificationBell'
import { SettingsModal } from './components/SettingsModal'
import { useSignals } from './hooks/useSignals'
import { useNotification } from './hooks/useNotification'
import { BacktestPage } from './components/BacktestPage'
import type {
  Dashboard,
  Position,
  Trade,
  TradeSummary,
  Timeframe,
} from './types'
import {
  getDashboard,
  getPositions,
  getTrades,
  getTradeSummary,
} from './api/client'

const SYMBOLS = ['USDJPY', 'EURUSD', 'GBPUSD', 'AUDUSD', 'EURJPY'] as const
const DEFAULT_TIMEFRAME: Timeframe = 'M15'

type Page = 'dashboard' | 'backtest'

/** メインアプリ */
export function App() {
  const [currentPage, setCurrentPage] = useState<Page>('dashboard')
  const [symbol, setSymbol] = useState<string>('USDJPY')

  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [tradeSummary, setTradeSummary] = useState<TradeSummary | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const [settingsOpen, setSettingsOpen] = useState(false)

  const { currentSignals, isLoading: signalsLoading } = useSignals(symbol)

  const {
    notifications,
    unreadCount,
    hasPermission,
    requestPermission,
    clearNotifications,
    markAsRead,
  } = useNotification({
    minConfidence: 0.5,
    enabled: true,
    soundEnabled: true,
  })

  const fetchData = useCallback(async () => {
    try {
      const [dashboardData, positionsData, tradesData, summaryData] =
        await Promise.all([
          getDashboard(),
          getPositions(symbol),
          getTrades(symbol, 20),
          getTradeSummary(symbol, 30),
        ])
      setDashboard(dashboardData)
      setPositions(positionsData)
      setTrades(tradesData)
      setTradeSummary(summaryData)
    } catch {
      // エラー時は既存データを維持
    } finally {
      setIsLoading(false)
    }
  }, [symbol])

  useEffect(() => {
    setIsLoading(true)
    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [fetchData])

  const headerRight = (
    <div className="flex items-center gap-3">
      {/* シンボルセレクター */}
      <select
        value={symbol}
        onChange={(e) => setSymbol(e.target.value)}
        className="bg-gray-700 border border-gray-600 text-sm rounded-lg px-2 py-1.5 text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500"
      >
        {SYMBOLS.map((s) => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>

      {/* ページナビゲーション */}
      <div className="flex gap-1">
        <button
          onClick={() => setCurrentPage('dashboard')}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            currentPage === 'dashboard'
              ? 'bg-blue-600 text-white'
              : 'text-gray-400 hover:text-white hover:bg-gray-700'
          }`}
        >
          Dashboard
        </button>
        <button
          onClick={() => setCurrentPage('backtest')}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            currentPage === 'backtest'
              ? 'bg-blue-600 text-white'
              : 'text-gray-400 hover:text-white hover:bg-gray-700'
          }`}
        >
          Backtest
        </button>
      </div>

      <NotificationBell
        notifications={notifications}
        unreadCount={unreadCount}
        hasPermission={hasPermission}
        onRequestPermission={requestPermission}
        onClear={clearNotifications}
        onMarkAsRead={markAsRead}
      />
      <button
        onClick={() => setSettingsOpen(true)}
        className="p-2 rounded-lg hover:bg-gray-700 transition-colors"
        aria-label="Settings"
      >
        <svg
          className="w-5 h-5"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
          />
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
          />
        </svg>
      </button>
    </div>
  )

  return (
    <Layout headerRight={headerRight} symbol={symbol}>
      {currentPage === 'dashboard' ? (
        <div className="space-y-4">
          {/* メトリクスストリップ */}
          <AccountInfo dashboard={dashboard} isLoading={isLoading} />

          {/* メインコンテンツ */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* 左カラム - チャート */}
            <div className="lg:col-span-2 space-y-4">
              <Chart symbol={symbol} signals={currentSignals} />
              <IndicatorPanel symbol={symbol} timeframe={DEFAULT_TIMEFRAME} />
            </div>

            {/* 右カラム - シグナル・ポジション */}
            <div className="space-y-4">
              <SignalPanel signals={currentSignals} isLoading={signalsLoading} />
              <PositionTable positions={positions} isLoading={isLoading} />
            </div>
          </div>

          {/* トレード履歴（全幅） */}
          <TradeHistory
            trades={trades}
            summary={tradeSummary}
            isLoading={isLoading}
          />
        </div>
      ) : (
        <BacktestPage />
      )}

      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </Layout>
  )
}

export default App
