/** 口座情報メトリクスストリップ */

import type { Dashboard } from '../types'

interface AccountInfoProps {
  dashboard: Dashboard | null
  isLoading: boolean
}

/** 口座情報パネル - 横一列メトリクスストリップ */
export function AccountInfo({ dashboard, isLoading }: AccountInfoProps) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="card animate-pulse">
            <div className="h-3 bg-gray-700 rounded w-16 mb-2" />
            <div className="h-6 bg-gray-700 rounded w-24" />
          </div>
        ))}
      </div>
    )
  }

  if (!dashboard) {
    return (
      <div className="card">
        <p className="text-gray-400 text-sm">口座データ取得中...</p>
      </div>
    )
  }

  const { account, daily_pnl, daily_pnl_pct, win_rate, open_positions, today_trades } = dashboard

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      <MetricCard
        label="残高"
        value={formatCurrency(account.balance)}
        sub={`有効証拠金: ${formatCurrency(account.equity)}`}
      />
      <MetricCard
        label="本日損益"
        value={`${daily_pnl >= 0 ? '+' : ''}${formatCurrency(daily_pnl)}`}
        sub={`${daily_pnl_pct >= 0 ? '+' : ''}${daily_pnl_pct.toFixed(2)}%`}
        variant={daily_pnl >= 0 ? 'profit' : 'loss'}
      />
      <MetricCard
        label="勝率"
        value={`${win_rate.toFixed(1)}%`}
        sub={`本日 ${today_trades} トレード`}
        variant={win_rate >= 55 ? 'profit' : win_rate >= 45 ? 'neutral' : 'loss'}
      />
      <MetricCard
        label="ポジション"
        value={`${open_positions}`}
        sub={account.profit !== 0 ? `含み: ${formatCurrency(account.profit)}` : '含みなし'}
        variant={account.profit > 0 ? 'profit' : account.profit < 0 ? 'loss' : 'neutral'}
      />
      <MetricCard
        label="証拠金維持率"
        value={`${account.margin_level.toFixed(0)}%`}
        sub={`余剰: ${formatCurrency(account.free_margin)}`}
        variant={account.margin_level > 300 ? 'profit' : account.margin_level > 150 ? 'neutral' : 'loss'}
      />
    </div>
  )
}

type MetricVariant = 'profit' | 'loss' | 'neutral'

interface MetricCardProps {
  label: string
  value: string
  sub?: string
  variant?: MetricVariant
}

function MetricCard({ label, value, sub, variant = 'neutral' }: MetricCardProps) {
  const valueColors: Record<MetricVariant, string> = {
    profit: 'text-green-400',
    loss: 'text-red-400',
    neutral: 'text-gray-100',
  }

  const borderColors: Record<MetricVariant, string> = {
    profit: 'border-l-green-500/50',
    loss: 'border-l-red-500/50',
    neutral: 'border-l-gray-600',
  }

  return (
    <div className={`card border-l-2 ${borderColors[variant]} py-3`}>
      <p className="text-xs text-gray-400 uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-lg font-bold tabular-nums ${valueColors[variant]}`}>
        {value}
      </p>
      {sub && (
        <p className="text-xs text-gray-500 mt-0.5 tabular-nums">{sub}</p>
      )}
    </div>
  )
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('ja-JP', {
    style: 'currency',
    currency: 'JPY',
    maximumFractionDigits: 0,
  }).format(value)
}
