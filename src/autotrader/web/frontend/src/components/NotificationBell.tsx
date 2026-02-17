/** 通知ベルコンポーネント */

import { useState } from 'react'
import type { NotificationItem } from '../hooks/useNotification'

interface NotificationBellProps {
  notifications: NotificationItem[]
  unreadCount: number
  hasPermission: boolean
  onRequestPermission: () => Promise<boolean>
  onClear: () => void
  onMarkAsRead: (id: string) => void
}

/** 通知ベル */
export function NotificationBell({
  notifications,
  unreadCount,
  hasPermission,
  onRequestPermission,
  onClear,
  onMarkAsRead,
}: NotificationBellProps) {
  const [isOpen, setIsOpen] = useState(false)

  return (
    <div className="relative">
      {/* ベルボタン */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="relative p-2 rounded-lg hover:bg-gray-700 transition-colors"
        aria-label="Notifications"
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
            d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
          />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 rounded-full text-xs flex items-center justify-center font-medium">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {/* ドロップダウン */}
      {isOpen && (
        <>
          {/* オーバーレイ */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setIsOpen(false)}
          />

          {/* パネル */}
          <div className="absolute right-0 top-full mt-2 w-80 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-50">
            <div className="flex items-center justify-between p-3 border-b border-gray-700">
              <h3 className="font-medium">通知</h3>
              {notifications.length > 0 && (
                <button
                  onClick={onClear}
                  className="text-xs text-gray-400 hover:text-gray-200"
                >
                  すべてクリア
                </button>
              )}
            </div>

            {!hasPermission && (
              <div className="p-3 bg-yellow-900/30 border-b border-gray-700">
                <p className="text-xs text-yellow-400 mb-2">
                  ブラウザ通知が許可されていません
                </p>
                <button
                  onClick={onRequestPermission}
                  className="btn btn-primary text-xs py-1"
                >
                  通知を許可
                </button>
              </div>
            )}

            <div className="max-h-80 overflow-y-auto">
              {notifications.length === 0 ? (
                <p className="p-4 text-sm text-gray-400 text-center">
                  通知はありません
                </p>
              ) : (
                <ul>
                  {notifications.map((notification) => (
                    <NotificationItemRow
                      key={notification.id}
                      notification={notification}
                      onMarkAsRead={() => onMarkAsRead(notification.id)}
                    />
                  ))}
                </ul>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

interface NotificationItemRowProps {
  notification: NotificationItem
  onMarkAsRead: () => void
}

function NotificationItemRow({
  notification,
  onMarkAsRead,
}: NotificationItemRowProps) {
  const typeColors = {
    signal: 'border-primary-500',
    alert: 'border-yellow-500',
    info: 'border-gray-500',
  }

  const confidenceColors = {
    HIGH: 'text-green-400',
    MEDIUM: 'text-yellow-400',
    LOW: 'text-red-400',
  }

  return (
    <li
      className={`p-3 border-l-2 hover:bg-gray-700/50 cursor-pointer transition-colors ${
        typeColors[notification.type]
      } ${!notification.read ? 'bg-gray-700/30' : ''}`}
      onClick={onMarkAsRead}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate">{notification.title}</p>
          <p className="text-xs text-gray-400 mt-0.5">{notification.message}</p>
          {notification.confidenceLevel && (
            <span
              className={`text-xs ${
                confidenceColors[notification.confidenceLevel]
              }`}
            >
              {notification.confidenceLevel}
            </span>
          )}
        </div>
        <span className="text-xs text-gray-500 whitespace-nowrap">
          {formatTime(notification.timestamp)}
        </span>
      </div>
    </li>
  )
}

function formatTime(date: Date): string {
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMin = Math.floor(diffMs / 60000)

  if (diffMin < 1) return '今'
  if (diffMin < 60) return `${diffMin}分前`

  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}時間前`

  return date.toLocaleDateString('ja-JP', {
    month: 'short',
    day: 'numeric',
  })
}
