/** レイアウトコンポーネント */

import type { ReactNode } from 'react'

interface LayoutProps {
  children: ReactNode
  headerRight?: ReactNode
  symbol?: string
}

/** メインレイアウト */
export function Layout({ children, headerRight, symbol = 'USDJPY' }: LayoutProps) {
  return (
    <div className="min-h-screen bg-gray-900">
      {/* ヘッダー */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-gray-800/95 backdrop-blur-sm border-b border-gray-700 h-14">
        <div className="flex items-center justify-between h-full px-4">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <svg className="w-6 h-6 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <path d="M3 17l6-6 4 4 8-8" />
                <path d="M17 7h4v4" />
              </svg>
              <h1 className="text-lg font-bold bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">
                AutoTrader V4
              </h1>
            </div>
            <div className="hidden md:flex items-center gap-2 ml-4">
              <span className="text-sm font-medium text-gray-300">{symbol}</span>
              <ConnectionStatus />
            </div>
          </div>
          {headerRight}
        </div>
      </header>

      {/* メインコンテンツ */}
      <main className="pt-14 min-h-screen">
        <div className="p-4 max-w-[1600px] mx-auto">{children}</div>
      </main>
    </div>
  )
}

/** 接続ステータス表示 */
function ConnectionStatus() {
  return (
    <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-green-900/30 border border-green-800/50">
      <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" />
      <span className="text-xs text-green-400">Live</span>
    </span>
  )
}
