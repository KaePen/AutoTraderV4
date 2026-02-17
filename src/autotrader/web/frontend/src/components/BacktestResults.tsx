/** バックテスト結果表示コンポーネント */

import { BacktestChart } from './BacktestChart'

interface YearResult {
  year: number
  trades: number
  win_rate: number
  profit_factor: number
  net_profit: number
  max_drawdown: number
  sharpe: number
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
  yearly_results: YearResult[]
  monthly_results: MonthResult[]
}

interface BacktestResultsProps {
  result: BacktestResult
}

/** バックテスト結果表示 */
export function BacktestResults({ result }: BacktestResultsProps) {
  return (
    <div className="space-y-4">
      {/* KPIカード */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="Total Trades" value={String(result.total_trades)} />
        <KpiCard
          label="Win Rate"
          value={`${result.win_rate.toFixed(1)}%`}
          variant={result.win_rate >= 55 ? 'profit' : result.win_rate >= 45 ? 'neutral' : 'loss'}
        />
        <KpiCard
          label="Profit Factor"
          value={result.profit_factor.toFixed(2)}
          variant={result.profit_factor >= 1.5 ? 'profit' : result.profit_factor >= 1.0 ? 'neutral' : 'loss'}
        />
        <KpiCard
          label="Net Profit"
          value={`¥${result.net_profit.toLocaleString()}`}
          variant={result.net_profit >= 0 ? 'profit' : 'loss'}
        />
        <KpiCard
          label="Annual Return"
          value={`${result.annual_return.toFixed(1)}%`}
          variant={result.annual_return >= 0 ? 'profit' : 'loss'}
        />
        <KpiCard
          label="Max Drawdown"
          value={`${result.max_drawdown.toFixed(2)}%`}
          variant={result.max_drawdown <= 3 ? 'profit' : result.max_drawdown <= 5 ? 'neutral' : 'loss'}
        />
        <KpiCard
          label="Sharpe Ratio"
          value={result.sharpe_ratio.toFixed(2)}
          variant={result.sharpe_ratio >= 2 ? 'profit' : result.sharpe_ratio >= 1 ? 'neutral' : 'loss'}
        />
        <KpiCard
          label="Avg/Year"
          value={result.yearly_results.length > 0
            ? `${(result.yearly_results.reduce((s, y) => s + y.trades, 0) / result.yearly_results.length).toFixed(0)} trades`
            : '-'}
        />
      </div>

      {/* エクイティチャート */}
      <BacktestChart
        monthlyResults={result.monthly_results}
        isLoading={false}
      />

      {/* 月次ヒートマップ */}
      <MonthlyHeatmap monthlyResults={result.monthly_results} />

      {/* 年別テーブル */}
      <div className="card">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
          Yearly Results
        </h3>
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Year</th>
                <th className="text-right">Trades</th>
                <th className="text-right">Win Rate</th>
                <th className="text-right">PF</th>
                <th className="text-right">Net Profit</th>
                <th className="text-right">Max DD</th>
                <th className="text-right">Sharpe</th>
              </tr>
            </thead>
            <tbody>
              {result.yearly_results.map((yr) => (
                <tr key={yr.year}>
                  <td className="font-medium">{yr.year}</td>
                  <td className="text-right tabular-nums">{yr.trades}</td>
                  <td className={`text-right tabular-nums ${yr.win_rate >= 55 ? 'text-green-400' : yr.win_rate >= 45 ? 'text-gray-300' : 'text-red-400'}`}>
                    {yr.win_rate.toFixed(1)}%
                  </td>
                  <td className={`text-right tabular-nums ${yr.profit_factor >= 1.5 ? 'text-green-400' : yr.profit_factor >= 1 ? 'text-gray-300' : 'text-red-400'}`}>
                    {yr.profit_factor.toFixed(2)}
                  </td>
                  <td className={`text-right tabular-nums ${yr.net_profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    ¥{yr.net_profit.toLocaleString()}
                  </td>
                  <td className="text-right tabular-nums text-red-400">
                    {yr.max_drawdown.toFixed(2)}%
                  </td>
                  <td className="text-right tabular-nums">
                    {yr.sharpe.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

/** KPIカード */
interface KpiCardProps {
  label: string
  value: string
  variant?: 'profit' | 'loss' | 'neutral'
}

function KpiCard({ label, value, variant = 'neutral' }: KpiCardProps) {
  const colors = {
    profit: 'text-green-400',
    loss: 'text-red-400',
    neutral: 'text-gray-100',
  }

  return (
    <div className="card py-3">
      <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-xl font-bold tabular-nums ${colors[variant]}`}>{value}</p>
    </div>
  )
}

/** 月次ヒートマップ */
interface MonthlyHeatmapProps {
  monthlyResults: MonthResult[]
}

function MonthlyHeatmap({ monthlyResults }: MonthlyHeatmapProps) {
  if (monthlyResults.length === 0) return null

  const years = [...new Set(monthlyResults.map((m) => m.year))].sort()
  const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

  const getResult = (year: number, month: number) =>
    monthlyResults.find((m) => m.year === year && m.month === month)

  const maxAbsPct = Math.max(
    ...monthlyResults.map((m) => Math.abs(m.return_pct)),
    1
  )

  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
        Monthly Returns
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr>
              <th className="px-2 py-1 text-left text-gray-500">Year</th>
              {monthNames.map((m) => (
                <th key={m} className="px-2 py-1 text-center text-gray-500">{m}</th>
              ))}
              <th className="px-2 py-1 text-right text-gray-500">Total</th>
            </tr>
          </thead>
          <tbody>
            {years.map((year) => {
              const yearResults = monthlyResults.filter((m) => m.year === year)
              const yearTotal = yearResults.reduce((s, m) => s + m.return_pct, 0)

              return (
                <tr key={year}>
                  <td className="px-2 py-1 font-medium text-gray-300">{year}</td>
                  {Array.from({ length: 12 }, (_, i) => i + 1).map((month) => {
                    const r = getResult(year, month)
                    if (!r) {
                      return <td key={month} className="px-1 py-1" />
                    }
                    const intensity = Math.min(Math.abs(r.return_pct) / maxAbsPct, 1)
                    const bg = r.return_pct >= 0
                      ? `rgba(34, 197, 94, ${0.1 + intensity * 0.5})`
                      : `rgba(239, 68, 68, ${0.1 + intensity * 0.5})`

                    return (
                      <td
                        key={month}
                        className="px-1 py-1 text-center tabular-nums rounded"
                        style={{ backgroundColor: bg }}
                        title={`${year}/${month}: ${r.return_pct >= 0 ? '+' : ''}${r.return_pct.toFixed(2)}% (${r.trades} trades)`}
                      >
                        <span className={r.return_pct >= 0 ? 'text-green-300' : 'text-red-300'}>
                          {r.return_pct >= 0 ? '+' : ''}{r.return_pct.toFixed(1)}
                        </span>
                      </td>
                    )
                  })}
                  <td className={`px-2 py-1 text-right font-medium tabular-nums ${yearTotal >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {yearTotal >= 0 ? '+' : ''}{yearTotal.toFixed(1)}%
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
