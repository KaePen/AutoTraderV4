/** WebSocketフック */

import { useEffect, useRef, useState, useCallback } from 'react'
import type { WebSocketMessage, WebSocketEventType } from '../types'
import { WebSocketClient, createWebSocketClient, type ConnectionState } from '../api/websocket'

interface UseWebSocketOptions {
  autoConnect?: boolean
  onMessage?: (message: WebSocketMessage) => void
  onConnect?: () => void
  onDisconnect?: () => void
}

interface UseWebSocketReturn {
  connectionState: ConnectionState
  isConnected: boolean
  connect: () => void
  disconnect: () => void
  subscribe: (
    eventType: WebSocketEventType,
    handler: (message: WebSocketMessage) => void
  ) => () => void
}

/** WebSocket接続フック */
export function useWebSocket(
  path: string,
  options: UseWebSocketOptions = {}
): UseWebSocketReturn {
  const { autoConnect = true, onMessage, onConnect, onDisconnect } = options
  const wsRef = useRef<WebSocketClient | null>(null)
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected')

  useEffect(() => {
    const client = createWebSocketClient(path)
    wsRef.current = client

    // 接続状態追跡
    const unsubState = client.onStateChange((state) => {
      setConnectionState(state)
      if (state === 'connected') onConnect?.()
      if (state === 'disconnected') onDisconnect?.()
    })

    // メッセージハンドラー
    const unsubAll = client.on('*', (message) => {
      onMessage?.(message)
    })

    if (autoConnect) {
      client.connect()
    }

    return () => {
      unsubState()
      unsubAll()
      client.disconnect()
    }
  }, [path, autoConnect, onMessage, onConnect, onDisconnect])

  const connect = useCallback(() => {
    wsRef.current?.connect()
  }, [])

  const disconnect = useCallback(() => {
    wsRef.current?.disconnect()
  }, [])

  const subscribe = useCallback(
    (
      eventType: WebSocketEventType,
      handler: (message: WebSocketMessage) => void
    ) => {
      return wsRef.current?.on(eventType, handler) ?? (() => {})
    },
    []
  )

  return {
    connectionState,
    isConnected: connectionState === 'connected',
    connect,
    disconnect,
    subscribe,
  }
}
