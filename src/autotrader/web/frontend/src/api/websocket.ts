/** WebSocket管理 */

import type { WebSocketMessage, WebSocketEventType } from '../types'

type MessageHandler = (message: WebSocketMessage) => void

export type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'error'

/** WebSocket接続管理クラス */
export class WebSocketClient {
  private ws: WebSocket | null = null
  private url: string
  private handlers: Map<WebSocketEventType | '*', Set<MessageHandler>> =
    new Map()
  private stateHandlers: Set<(state: ConnectionState) => void> = new Set()
  private reconnectAttempts = 0
  private maxReconnectAttempts = 10
  private baseDelay = 1000
  private maxDelay = 30000
  private pingInterval: number | null = null
  private isManualClose = false
  private _state: ConnectionState = 'disconnected'

  constructor(url: string) {
    this.url = url
  }

  /** 接続状態 */
  get state(): ConnectionState {
    return this._state
  }

  get isConnected(): boolean {
    return this._state === 'connected'
  }

  /** 接続 */
  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      return
    }

    this.isManualClose = false
    this.setState('connecting')

    try {
      this.ws = new WebSocket(this.url)
    } catch {
      this.setState('error')
      this.attemptReconnect()
      return
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0
      this.setState('connected')
      this.startPing()
    }

    this.ws.onmessage = (event) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data)
        this.notifyHandlers(message)
      } catch {
        // 無効なJSONは無視
      }
    }

    this.ws.onclose = () => {
      this.stopPing()
      if (!this.isManualClose) {
        this.setState('disconnected')
        this.attemptReconnect()
      } else {
        this.setState('disconnected')
      }
    }

    this.ws.onerror = () => {
      this.setState('error')
    }
  }

  /** 切断 */
  disconnect(): void {
    this.isManualClose = true
    this.stopPing()
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    this.setState('disconnected')
  }

  /** イベントハンドラー登録 */
  on(eventType: WebSocketEventType | '*', handler: MessageHandler): () => void {
    if (!this.handlers.has(eventType)) {
      this.handlers.set(eventType, new Set())
    }
    this.handlers.get(eventType)!.add(handler)

    return () => {
      this.handlers.get(eventType)?.delete(handler)
    }
  }

  /** イベントハンドラー削除 */
  off(eventType: WebSocketEventType | '*', handler: MessageHandler): void {
    this.handlers.get(eventType)?.delete(handler)
  }

  /** 接続状態変更ハンドラー */
  onStateChange(handler: (state: ConnectionState) => void): () => void {
    this.stateHandlers.add(handler)
    return () => {
      this.stateHandlers.delete(handler)
    }
  }

  private setState(state: ConnectionState): void {
    this._state = state
    this.stateHandlers.forEach((handler) => handler(state))
  }

  private notifyHandlers(message: WebSocketMessage): void {
    this.handlers.get(message.type)?.forEach((handler) => handler(message))
    this.handlers.get('*')?.forEach((handler) => handler(message))
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.setState('error')
      return
    }

    this.reconnectAttempts++
    // 指数バックオフ + jitter
    const delay = Math.min(
      this.baseDelay * Math.pow(2, this.reconnectAttempts - 1) + Math.random() * 500,
      this.maxDelay
    )

    setTimeout(() => {
      if (!this.isManualClose) {
        this.connect()
      }
    }, delay)
  }

  private startPing(): void {
    this.pingInterval = window.setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send('ping')
      }
    }, 30000)
  }

  private stopPing(): void {
    if (this.pingInterval !== null) {
      clearInterval(this.pingInterval)
      this.pingInterval = null
    }
  }
}

/** WebSocketクライアントインスタンス生成 */
export function createWebSocketClient(path: string): WebSocketClient {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  const url = `${protocol}//${host}${path}`
  return new WebSocketClient(url)
}
