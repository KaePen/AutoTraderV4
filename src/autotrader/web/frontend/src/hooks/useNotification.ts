/** 通知フック */

import { useEffect, useCallback, useState } from 'react'
import type { Signal, WebSocketMessage, ConfidenceLevel } from '../types'
import { useWebSocket } from './useWebSocket'

interface NotificationOptions {
  minConfidence: number
  enabled: boolean
  soundEnabled: boolean
}

interface UseNotificationReturn {
  hasPermission: boolean
  requestPermission: () => Promise<boolean>
  notifications: NotificationItem[]
  clearNotifications: () => void
  markAsRead: (id: string) => void
  unreadCount: number
}

export interface NotificationItem {
  id: string
  title: string
  message: string
  type: 'signal' | 'alert' | 'info'
  confidenceLevel?: ConfidenceLevel
  timestamp: Date
  read: boolean
}

/** 通知フック */
export function useNotification(
  options: NotificationOptions = {
    minConfidence: 0.5,
    enabled: true,
    soundEnabled: true,
  }
): UseNotificationReturn {
  const [hasPermission, setHasPermission] = useState(false)
  const [notifications, setNotifications] = useState<NotificationItem[]>([])

  // 権限チェック
  useEffect(() => {
    if ('Notification' in window) {
      setHasPermission(Notification.permission === 'granted')
    }
  }, [])

  // 権限リクエスト
  const requestPermission = useCallback(async (): Promise<boolean> => {
    if (!('Notification' in window)) {
      return false
    }

    const permission = await Notification.requestPermission()
    const granted = permission === 'granted'
    setHasPermission(granted)
    return granted
  }, [])

  // 通知表示
  const showNotification = useCallback(
    (item: NotificationItem) => {
      // 内部リストに追加
      setNotifications((prev) => [item, ...prev].slice(0, 100))

      // ブラウザ通知
      if (hasPermission && options.enabled) {
        const notification = new Notification(item.title, {
          body: item.message,
          icon: '/favicon.ico',
          tag: item.id,
        })

        // サウンド
        if (options.soundEnabled) {
          // Web Audio APIでビープ音
          try {
            const audioContext = new AudioContext()
            const oscillator = audioContext.createOscillator()
            const gainNode = audioContext.createGain()

            oscillator.connect(gainNode)
            gainNode.connect(audioContext.destination)

            oscillator.frequency.value = 800
            oscillator.type = 'sine'
            gainNode.gain.value = 0.1

            oscillator.start()
            oscillator.stop(audioContext.currentTime + 0.1)
          } catch {
            // オーディオ再生失敗は無視
          }
        }

        // 自動で閉じる
        setTimeout(() => notification.close(), 5000)
      }
    },
    [hasPermission, options.enabled, options.soundEnabled]
  )

  // WebSocketでシグナル監視
  useWebSocket('/ws/signals', {
    onMessage: (message: WebSocketMessage) => {
      if (message.type === 'signal_update' && options.enabled) {
        const signal = message.data as Signal
        if (signal.confidence >= options.minConfidence) {
          showNotification({
            id: `signal-${signal.signal_id}`,
            title: `${signal.signal_type} Signal - ${signal.symbol}`,
            message: `Confidence: ${(signal.confidence * 100).toFixed(1)}% (${signal.confidence_level})`,
            type: 'signal',
            confidenceLevel: signal.confidence_level,
            timestamp: new Date(),
            read: false,
          })
        }
      } else if (message.type === 'alert') {
        const alertData = message.data as { title: string; message: string }
        showNotification({
          id: `alert-${Date.now()}`,
          title: alertData.title,
          message: alertData.message,
          type: 'alert',
          timestamp: new Date(),
          read: false,
        })
      }
    },
  })

  const clearNotifications = useCallback(() => {
    setNotifications([])
  }, [])

  const markAsRead = useCallback((id: string) => {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n))
    )
  }, [])

  const unreadCount = notifications.filter((n) => !n.read).length

  return {
    hasPermission,
    requestPermission,
    notifications,
    clearNotifications,
    markAsRead,
    unreadCount,
  }
}
