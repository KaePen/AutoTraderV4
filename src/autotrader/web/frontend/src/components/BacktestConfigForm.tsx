/** バックテスト設定フォームコンポーネント */

import { useState, useEffect } from 'react'

interface BacktestConfig {
  start_year: number
  end_year: number
  initial_balance: number
  volume: number
  use_short_timeframe: boolean
  // エントリーフィルター
  range_day_bbw_threshold: number | null
  range_day_score_premium: number | null
  weak_hours_enabled: boolean | null
  weak_hours_score_premium: number | null
  // 資金管理
  use_dynamic_lot: boolean | null
  base_risk_pct: number | null
  max_lot_per_trade: number | null
  // ポジション管理
  enable_position_manager: boolean | null
  stagnation_min_mfe_r: number | null
  insurance_trigger_r: number | null
}

const DEFAULT_CONFIG: BacktestConfig = {
  start_year: 2020,
  end_year: 2025,
  initial_balance: 1_000_000,
  volume: 1.0,
  use_short_timeframe: true,
  range_day_bbw_threshold: null,
  range_day_score_premium: null,
  weak_hours_enabled: null,
  weak_hours_score_premium: null,
  use_dynamic_lot: null,
  base_risk_pct: null,
  max_lot_per_trade: null,
  enable_position_manager: null,
  stagnation_min_mfe_r: null,
  insurance_trigger_r: null,
}

const STORAGE_KEY = 'autotrader_backtest_config'

interface BacktestConfigFormProps {
  onSubmit: (config: BacktestConfig) => void
  disabled: boolean
}

/** バックテスト設定フォーム */
export function BacktestConfigForm({ onSubmit, disabled }: BacktestConfigFormProps) {
  const [config, setConfig] = useState<BacktestConfig>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) return { ...DEFAULT_CONFIG, ...JSON.parse(saved) }
    } catch {
      // パースエラー時はデフォルト
    }
    return DEFAULT_CONFIG
  })

  const [showAdvanced, setShowAdvanced] = useState(false)

  // localStorage保存
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
    } catch {
      // 保存エラーは無視
    }
  }, [config])

  const update = <K extends keyof BacktestConfig>(key: K, value: BacktestConfig[K]) => {
    setConfig((prev) => ({ ...prev, [key]: value }))
  }

  const handleSubmit = () => {
    onSubmit(config)
  }

  const resetToDefaults = () => {
    setConfig(DEFAULT_CONFIG)
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">
          Configuration
        </h2>
        <button
          onClick={resetToDefaults}
          className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
          disabled={disabled}
        >
          Reset
        </button>
      </div>

      {/* 基本設定 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <FormField label="開始年">
          <select
            value={config.start_year}
            onChange={(e) => update('start_year', Number(e.target.value))}
            className="input w-full text-sm"
            disabled={disabled}
          >
            {yearOptions()}
          </select>
        </FormField>
        <FormField label="終了年">
          <select
            value={config.end_year}
            onChange={(e) => update('end_year', Number(e.target.value))}
            className="input w-full text-sm"
            disabled={disabled}
          >
            {yearOptions()}
          </select>
        </FormField>
        <FormField label="初期残高">
          <input
            type="number"
            value={config.initial_balance}
            onChange={(e) => update('initial_balance', Number(e.target.value))}
            className="input w-full text-sm"
            disabled={disabled}
            step={100000}
          />
        </FormField>
        <FormField label="Volume (lot)">
          <input
            type="number"
            value={config.volume}
            onChange={(e) => update('volume', Number(e.target.value))}
            className="input w-full text-sm"
            disabled={disabled}
            step={0.1}
            min={0.01}
          />
        </FormField>
      </div>

      {/* 詳細設定トグル */}
      <button
        onClick={() => setShowAdvanced(!showAdvanced)}
        className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200 transition-colors"
      >
        <svg
          className={`w-3 h-3 transition-transform ${showAdvanced ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        パラメータオーバーライド
      </button>

      {showAdvanced && (
        <div className="space-y-4 pt-2 border-t border-gray-700">
          {/* エントリーフィルター */}
          <div>
            <h3 className="text-xs font-medium text-gray-400 mb-2">Entry Filter</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <NullableNumber
                label="BBW閾値"
                value={config.range_day_bbw_threshold}
                onChange={(v) => update('range_day_bbw_threshold', v)}
                disabled={disabled}
                step={0.01}
                placeholder="0.20"
              />
              <NullableNumber
                label="Score Premium"
                value={config.range_day_score_premium}
                onChange={(v) => update('range_day_score_premium', v)}
                disabled={disabled}
                step={0.05}
                placeholder="0.55"
              />
              <NullableToggle
                label="Weak Hours"
                value={config.weak_hours_enabled}
                onChange={(v) => update('weak_hours_enabled', v)}
                disabled={disabled}
              />
              <NullableNumber
                label="WH Premium"
                value={config.weak_hours_score_premium}
                onChange={(v) => update('weak_hours_score_premium', v)}
                disabled={disabled}
                step={0.1}
                placeholder="0.5"
              />
            </div>
          </div>

          {/* 資金管理 */}
          <div>
            <h3 className="text-xs font-medium text-gray-400 mb-2">Capital</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <NullableToggle
                label="Dynamic Lot"
                value={config.use_dynamic_lot}
                onChange={(v) => update('use_dynamic_lot', v)}
                disabled={disabled}
              />
              <NullableNumber
                label="Risk %"
                value={config.base_risk_pct}
                onChange={(v) => update('base_risk_pct', v)}
                disabled={disabled}
                step={0.005}
                placeholder="0.02"
              />
              <NullableNumber
                label="Max Lot"
                value={config.max_lot_per_trade}
                onChange={(v) => update('max_lot_per_trade', v)}
                disabled={disabled}
                step={0.1}
                placeholder="2.0"
              />
            </div>
          </div>

          {/* ポジション管理 */}
          <div>
            <h3 className="text-xs font-medium text-gray-400 mb-2">Position Mgmt</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <NullableToggle
                label="PM有効"
                value={config.enable_position_manager}
                onChange={(v) => update('enable_position_manager', v)}
                disabled={disabled}
              />
              <NullableNumber
                label="Stag MFE"
                value={config.stagnation_min_mfe_r}
                onChange={(v) => update('stagnation_min_mfe_r', v)}
                disabled={disabled}
                step={0.05}
                placeholder="0.15"
              />
              <NullableNumber
                label="Insurance R"
                value={config.insurance_trigger_r}
                onChange={(v) => update('insurance_trigger_r', v)}
                disabled={disabled}
                step={0.1}
                placeholder="1.0"
              />
            </div>
          </div>
        </div>
      )}

      {/* 実行ボタン */}
      <button
        onClick={handleSubmit}
        disabled={disabled}
        className={`w-full py-2.5 rounded-lg font-semibold text-sm transition-all ${
          disabled
            ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
            : 'bg-blue-600 hover:bg-blue-700 text-white shadow-lg shadow-blue-600/20 active:scale-[0.99]'
        }`}
      >
        {disabled ? '実行中...' : 'バックテスト実行'}
      </button>
    </div>
  )
}

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-[10px] text-gray-500 uppercase mb-1">{label}</label>
      {children}
    </div>
  )
}

interface NullableNumberProps {
  label: string
  value: number | null
  onChange: (value: number | null) => void
  disabled: boolean
  step?: number
  placeholder?: string
}

function NullableNumber({ label, value, onChange, disabled, step = 0.01, placeholder }: NullableNumberProps) {
  return (
    <FormField label={label}>
      <input
        type="number"
        value={value ?? ''}
        onChange={(e) => {
          const v = e.target.value
          onChange(v === '' ? null : Number(v))
        }}
        className="input w-full text-sm"
        disabled={disabled}
        step={step}
        placeholder={placeholder ?? 'default'}
      />
    </FormField>
  )
}

interface NullableToggleProps {
  label: string
  value: boolean | null
  onChange: (value: boolean | null) => void
  disabled: boolean
}

function NullableToggle({ label, value, onChange, disabled }: NullableToggleProps) {
  const options: { label: string; val: boolean | null }[] = [
    { label: 'Default', val: null },
    { label: 'ON', val: true },
    { label: 'OFF', val: false },
  ]

  return (
    <FormField label={label}>
      <div className="flex gap-1">
        {options.map((opt) => (
          <button
            key={opt.label}
            onClick={() => onChange(opt.val)}
            disabled={disabled}
            className={`flex-1 px-1.5 py-1.5 text-[10px] rounded transition-colors ${
              value === opt.val
                ? 'bg-blue-600 text-white'
                : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
            } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </FormField>
  )
}

function yearOptions() {
  return Array.from({ length: 20 }, (_, i) => 2010 + i).map((year) => (
    <option key={year} value={year}>{year}</option>
  ))
}

export type { BacktestConfig }
