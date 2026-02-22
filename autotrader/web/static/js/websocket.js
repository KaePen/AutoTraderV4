/** WebSocket管理 */

class WebSocketClient {
  constructor(url) {
    this.url = url;
    this.ws = null;
    this.handlers = new Map();
    this.stateHandlers = new Set();
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 10;
    this.baseDelay = 1000;
    this.maxDelay = 30000;
    this.pingInterval = null;
    this.isManualClose = false;
    this._state = 'disconnected';
  }

  /** 接続状態 */
  get state() { return this._state; }
  get isConnected() { return this._state === 'connected'; }

  /** 接続 */
  connect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

    this.isManualClose = false;
    this._setState('connecting');

    try {
      this.ws = new WebSocket(this.url);
    } catch (e) {
      this._setState('error');
      this._attemptReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this._setState('connected');
      this._startPing();
    };

    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        this._notifyHandlers(message);
      } catch (e) {
        // 無効なJSONは無視
      }
    };

    this.ws.onclose = () => {
      this._stopPing();
      if (!this.isManualClose) {
        this._setState('disconnected');
        this._attemptReconnect();
      } else {
        this._setState('disconnected');
      }
    };

    this.ws.onerror = () => {
      this._setState('error');
    };
  }

  /** 切断 */
  disconnect() {
    this.isManualClose = true;
    this._stopPing();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this._setState('disconnected');
  }

  /** イベントハンドラー登録 */
  on(eventType, handler) {
    if (!this.handlers.has(eventType)) {
      this.handlers.set(eventType, new Set());
    }
    this.handlers.get(eventType).add(handler);
    return () => {
      const set = this.handlers.get(eventType);
      if (set) set.delete(handler);
    };
  }

  /** 接続状態変更ハンドラー */
  onStateChange(handler) {
    this.stateHandlers.add(handler);
    return () => this.stateHandlers.delete(handler);
  }

  _setState(state) {
    this._state = state;
    this.stateHandlers.forEach((h) => h(state));
  }

  _notifyHandlers(message) {
    const typeHandlers = this.handlers.get(message.type);
    if (typeHandlers) typeHandlers.forEach((h) => h(message));
    const allHandlers = this.handlers.get('*');
    if (allHandlers) allHandlers.forEach((h) => h(message));
  }

  _attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this._setState('error');
      return;
    }
    this.reconnectAttempts++;
    const delay = Math.min(
      this.baseDelay * Math.pow(2, this.reconnectAttempts - 1) + Math.random() * 500,
      this.maxDelay
    );
    setTimeout(() => {
      if (!this.isManualClose) this.connect();
    }, delay);
  }

  _startPing() {
    this.pingInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send('ping');
      }
    }, 30000);
  }

  _stopPing() {
    if (this.pingInterval !== null) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }
}

/** WebSocketクライアント生成 */
function createWebSocketClient(path) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  const url = `${protocol}//${host}${path}`;
  return new WebSocketClient(url);
}
