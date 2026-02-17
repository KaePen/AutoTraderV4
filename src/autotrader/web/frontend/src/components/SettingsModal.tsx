/** 設定モーダルコンポーネント */

import { useState, useEffect } from 'react'
import type { Settings } from '../types'
import { getSettings, updateSettings } from '../api/client'

interface SettingsModalProps {
  isOpen: boolean
  onClose: () => void
}

/** 設定モーダル */
export function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [activeTab, setActiveTab] = useState<
    'entry' | 'capital' | 'position' | 'notification'
  >('entry')

  // 通知設定のローカル状態
  const [notificationEnabled, setNotificationEnabled] = useState(true)
  const [minConfidence, setMinConfidence] = useState(0.5)
  const [soundEnabled, setSoundEnabled] = useState(true)

  useEffect(() => {
    if (isOpen) {
      fetchSettings()
    }
  }, [isOpen])

  const fetchSettings = async () => {
    setIsLoading(true)
    try {
      const data = await getSettings()
      setSettings(data)
      setNotificationEnabled(data.notification.enabled)
      setMinConfidence(data.notification.min_confidence)
      setSoundEnabled(data.notification.sound_enabled)
    } catch {
      // エラー時はデフォルト値
    } finally {
      setIsLoading(false)
    }
  }

  const handleSaveNotification = async () => {
    setIsSaving(true)
    try {
      const updated = await updateSettings({
        notification: {
          enabled: notificationEnabled,
          min_confidence: minConfidence,
          sound_enabled: soundEnabled,
        },
      })
      setSettings(updated)
    } catch {
      // エラー処理
    } finally {
      setIsSaving(false)
    }
  }

  if (!isOpen) return null

  const tabs = [
    { key: 'entry' as const, label: 'エントリー' },
    { key: 'capital' as const, label: '資金管理' },
    { key: 'position' as const, label: 'PM' },
    { key: 'notification' as const, label: '通知' },
  ]

  return (
    <>
      {/* オーバーレイ */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
      />

      {/* モーダル */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          className="bg-gray-800 border border-gray-700 rounded-lg shadow-xl w-full max-w-lg"
          onClick={(e) => e.stopPropagation()}
        >
          {/* ヘッダー */}
          <div className="flex items-center justify-between p-4 border-b border-gray-700">
            <h2 className="text-lg font-semibold">設定</h2>
            <button
              onClick={onClose}
              className="p-1 rounded hover:bg-gray-700 transition-colors"
              aria-label="Close"
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
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
          </div>

          {/* タブ */}
          <div className="flex border-b border-gray-700">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex-1 px-4 py-2 text-sm font-medium transition-colors ${
                  activeTab === tab.key
                    ? 'text-blue-400 border-b-2 border-blue-400'
                    : 'text-gray-400 hover:text-gray-200'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* コンテンツ */}
          <div className="p-4">
            {isLoading ? (
              <div className="flex items-center justify-center h-40">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500" />
              </div>
            ) : (
              <>
                {activeTab === 'entry' && settings && (
                  <EntryFilterSettings config={settings.trading.entry_filter} />
                )}

                {activeTab === 'capital' && settings && (
                  <CapitalSettings
                    config={settings.trading.capital_management}
                  />
                )}

                {activeTab === 'position' && settings && (
                  <PositionSettings
                    config={settings.trading.position_management}
                  />
                )}

                {activeTab === 'notification' && (
                  <NotificationSettings
                    enabled={notificationEnabled}
                    minConfidence={minConfidence}
                    soundEnabled={soundEnabled}
                    onEnabledChange={setNotificationEnabled}
                    onMinConfidenceChange={setMinConfidence}
                    onSoundEnabledChange={setSoundEnabled}
                  />
                )}
              </>
            )}
          </div>

          {/* フッター */}
          <div className="flex justify-end gap-2 p-4 border-t border-gray-700">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded bg-gray-600 hover:bg-gray-500 transition-colors"
            >
              閉じる
            </button>
            {activeTab === 'notification' && (
              <button
                onClick={handleSaveNotification}
                disabled={isSaving}
                className="px-4 py-2 rounded bg-blue-600 hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {isSaving ? '保存中...' : '保存'}
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

/** エントリーフィルター設定 */
function EntryFilterSettings({
  config,
}: {
  config: Settings['trading']['entry_filter']
}) {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium text-gray-300">エントリーフィルター</h3>
      <div className="grid grid-cols-2 gap-4">
        <SettingItem
          label="RANGE×DAY BBW閾値"
          value={config.range_day_bbw_threshold}
        />
        <SettingItem
          label="RANGE×DAY スコアプレミアム"
          value={config.range_day_score_premium}
        />
        <SettingItem
          label="Weak Hours"
          value={config.weak_hours_enabled ? 'ON' : 'OFF'}
        />
        <SettingItem
          label="Weak Hours プレミアム"
          value={config.weak_hours_score_premium}
        />
        <SettingItem
          label="東京深夜SWING"
          value={config.tokyo_night_swing_enabled ? 'ON' : 'OFF'}
        />
        <SettingItem
          label="東京深夜 プレミアム"
          value={config.tokyo_night_swing_premium}
        />
      </div>
      <p className="text-xs text-gray-500">
        ※ 変更にはアプリの再起動が必要です
      </p>
    </div>
  )
}

/** 資金管理設定 */
function CapitalSettings({
  config,
}: {
  config: Settings['trading']['capital_management']
}) {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium text-gray-300">資金管理</h3>
      <div className="grid grid-cols-2 gap-4">
        <SettingItem
          label="動的ロット"
          value={config.use_dynamic_lot ? 'ON' : 'OFF'}
        />
        <SettingItem
          label="基本リスク率"
          value={`${(config.base_risk_pct * 100).toFixed(1)}%`}
        />
        <SettingItem
          label="最大ロット/トレード"
          value={config.max_lot_per_trade}
        />
        <SettingItem
          label="最大エクスポージャー"
          value={`${config.max_total_exposure_lot} lot`}
        />
        <SettingItem
          label="エクイティフロア"
          value={`${(config.equity_floor_pct * 100).toFixed(0)}%`}
        />
        <SettingItem
          label="SLバッファ"
          value={`${config.slippage_buffer_pips} pips`}
        />
      </div>
    </div>
  )
}

/** ポジション管理設定 */
function PositionSettings({
  config,
}: {
  config: Settings['trading']['position_management']
}) {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium text-gray-300">ポジション管理</h3>
      <div className="grid grid-cols-2 gap-4">
        <SettingItem
          label="PM有効"
          value={config.enable_position_manager ? 'ON' : 'OFF'}
        />
        <SettingItem
          label="停滞最小MFE"
          value={`${config.stagnation_min_mfe_r}R`}
        />
        <SettingItem
          label="早期BE閾値"
          value={`${config.range_day_early_be_r}R`}
        />
        <SettingItem
          label="保険トリガー"
          value={`${config.insurance_trigger_r}R`}
        />
        <SettingItem
          label="1R部分利確比率"
          value={`${(config.partial_close_1r_ratio * 100).toFixed(0)}%`}
        />
        <SettingItem
          label="トレーリング開始"
          value={`${config.trailing_start_r}R`}
        />
      </div>
    </div>
  )
}

/** 通知設定 */
interface NotificationSettingsProps {
  enabled: boolean
  minConfidence: number
  soundEnabled: boolean
  onEnabledChange: (value: boolean) => void
  onMinConfidenceChange: (value: number) => void
  onSoundEnabledChange: (value: boolean) => void
}

function NotificationSettings({
  enabled,
  minConfidence,
  soundEnabled,
  onEnabledChange,
  onMinConfidenceChange,
  onSoundEnabledChange,
}: NotificationSettingsProps) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <label className="text-sm">通知を有効にする</label>
        <Toggle checked={enabled} onChange={onEnabledChange} />
      </div>

      <div>
        <label className="text-sm text-gray-400">
          最小確度: {(minConfidence * 100).toFixed(0)}%
        </label>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          value={minConfidence}
          onChange={(e) => onMinConfidenceChange(parseFloat(e.target.value))}
          className="w-full accent-blue-500"
        />
        <div className="flex justify-between text-xs text-gray-400 mt-1">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <label className="text-sm">サウンドを有効にする</label>
        <Toggle checked={soundEnabled} onChange={onSoundEnabledChange} />
      </div>
    </div>
  )
}

interface SettingItemProps {
  label: string
  value: string | number | boolean
}

function SettingItem({ label, value }: SettingItemProps) {
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <p className="text-sm font-medium">{String(value)}</p>
    </div>
  )
}

interface ToggleProps {
  checked: boolean
  onChange: (checked: boolean) => void
}

function Toggle({ checked, onChange }: ToggleProps) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative w-11 h-6 rounded-full transition-colors ${
        checked ? 'bg-blue-600' : 'bg-gray-600'
      }`}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
          checked ? 'translate-x-5' : 'translate-x-0'
        }`}
      />
    </button>
  )
}
