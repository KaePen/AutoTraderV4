/** 通知システム */

const NotificationManager = {
  notifications: [],
  unreadCount: 0,
  hasPermission: false,
  enabled: true,
  soundEnabled: true,
  wsClient: null,

  /** 初期化 */
  init() {
    // 権限チェック
    if ('Notification' in window) {
      this.hasPermission = Notification.permission === 'granted';
    }

    // DOM要素
    this.bellBtn = document.getElementById('notification-bell-btn');
    this.badge = document.getElementById('notification-badge');
    this.dropdown = document.getElementById('notification-dropdown');
    this.listEl = document.getElementById('notification-list');
    this.clearBtn = document.getElementById('notification-clear-btn');

    if (!this.bellBtn) return;

    // イベント
    this.bellBtn.addEventListener('click', () => this.toggleDropdown());
    if (this.clearBtn) {
      this.clearBtn.addEventListener('click', () => this.clearAll());
    }

    // オーバーレイクリックで閉じる
    document.addEventListener('click', (e) => {
      const container = document.getElementById('notification-bell-container');
      if (container && !container.contains(e.target)) {
        this.closeDropdown();
      }
    });

    // WebSocket接続
    this.wsClient = createWebSocketClient('/ws/signals');
    this.wsClient.on('*', (message) => this.handleMessage(message));
    this.wsClient.connect();
  },

  /** メッセージ処理 */
  handleMessage(message) {
    if (message.type === 'alert') {
      const alertData = message.data;
      this.addNotification({
        id: 'alert-' + Date.now(),
        title: alertData.title,
        message: alertData.message,
        type: 'alert',
        timestamp: new Date(),
        read: false,
      });
    }
  },

  /** 通知追加 */
  addNotification(item) {
    this.notifications.unshift(item);
    if (this.notifications.length > 100) {
      this.notifications = this.notifications.slice(0, 100);
    }
    this.unreadCount = this.notifications.filter((n) => !n.read).length;
    this.updateBadge();
    this.renderList();

    // ブラウザ通知
    if (this.hasPermission && this.enabled) {
      const notification = new Notification(item.title, {
        body: item.message,
        icon: '/favicon.ico',
        tag: item.id,
      });

      // サウンド
      if (this.soundEnabled) {
        try {
          const ctx = new AudioContext();
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.frequency.value = 800;
          osc.type = 'sine';
          gain.gain.value = 0.1;
          osc.start();
          osc.stop(ctx.currentTime + 0.1);
        } catch (e) { /* 無視 */ }
      }

      setTimeout(() => notification.close(), 5000);
    }
  },

  /** バッジ更新 */
  updateBadge() {
    if (!this.badge) return;
    if (this.unreadCount > 0) {
      this.badge.classList.remove('hidden');
      this.badge.textContent = this.unreadCount > 9 ? '9+' : String(this.unreadCount);
    } else {
      this.badge.classList.add('hidden');
    }
  },

  /** リスト描画 */
  renderList() {
    if (!this.listEl) return;
    if (this.notifications.length === 0) {
      this.listEl.innerHTML = '<p class="p-4 text-sm text-gray-400 text-center">通知はありません</p>';
      return;
    }

    const typeColors = {
      alert: 'border-yellow-500',
      info: 'border-gray-500',
    };

    this.listEl.innerHTML = '<ul>' + this.notifications.map((n) => {
      const borderColor = typeColors[n.type] || 'border-gray-500';
      const unreadBg = !n.read ? 'bg-gray-700/30' : '';

      return `<li class="p-3 border-l-2 hover:bg-gray-700/50 cursor-pointer transition-colors ${borderColor} ${unreadBg}"
                  data-id="${n.id}">
        <div class="flex items-start justify-between gap-2">
          <div class="flex-1 min-w-0">
            <p class="text-sm font-medium truncate">${this.escapeHtml(n.title)}</p>
            <p class="text-xs text-gray-400 mt-0.5">${this.escapeHtml(n.message)}</p>
          </div>
          <span class="text-xs text-gray-500 whitespace-nowrap">${this.formatTime(n.timestamp)}</span>
        </div>
      </li>`;
    }).join('') + '</ul>';

    // 既読マーク
    this.listEl.querySelectorAll('li[data-id]').forEach((li) => {
      li.addEventListener('click', () => {
        this.markAsRead(li.dataset.id);
      });
    });
  },

  /** 既読にする */
  markAsRead(id) {
    const n = this.notifications.find((x) => x.id === id);
    if (n) n.read = true;
    this.unreadCount = this.notifications.filter((x) => !x.read).length;
    this.updateBadge();
    this.renderList();
  },

  /** 全クリア */
  clearAll() {
    this.notifications = [];
    this.unreadCount = 0;
    this.updateBadge();
    this.renderList();
  },

  /** ドロップダウン切り替え */
  toggleDropdown() {
    if (!this.dropdown) return;
    this.dropdown.classList.toggle('hidden');
  },

  closeDropdown() {
    if (this.dropdown) this.dropdown.classList.add('hidden');
  },

  /** 権限リクエスト */
  async requestPermission() {
    if (!('Notification' in window)) return false;
    const permission = await Notification.requestPermission();
    this.hasPermission = permission === 'granted';
    return this.hasPermission;
  },

  /** 時間フォーマット */
  formatTime(date) {
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return '今';
    if (diffMin < 60) return diffMin + '分前';
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return diffHour + '時間前';
    return date.toLocaleDateString('ja-JP', { month: 'short', day: 'numeric' });
  },

  /** HTMLエスケープ */
  escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },
};

// ページ読み込み時に初期化
document.addEventListener('DOMContentLoaded', () => {
  NotificationManager.init();
});
