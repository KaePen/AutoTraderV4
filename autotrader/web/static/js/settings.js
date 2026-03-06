/** 設定モーダル */

const SettingsManager = {
  settings: null,
  activeTab: 'entry',
  isLoading: true,
  isSaving: false,
  notificationEnabled: true,
  minConfidence: 0.5,
  soundEnabled: true,
  _NICK_KEY: 'at4_account_nicknames',

  /** 初期化 */
  init() {
    this.overlay = document.getElementById('settings-modal-overlay');
    this.modal = document.getElementById('settings-modal');
    this.content = document.getElementById('settings-content');
    this.saveBtn = document.getElementById('settings-save-btn');
    this.cancelBtn = document.getElementById('settings-cancel-btn');
    this.openBtn = document.getElementById('settings-btn');
    this.closeBtn = document.getElementById('settings-close-btn');
    this.tabsEl = document.getElementById('settings-tabs');

    if (!this.modal) return;

    // イベント
    if (this.openBtn) this.openBtn.addEventListener('click', () => this.open());
    if (this.overlay) this.overlay.addEventListener('click', () => this.close());
    if (this.cancelBtn) this.cancelBtn.addEventListener('click', () => this.close());
    if (this.closeBtn) this.closeBtn.addEventListener('click', () => this.close());
    if (this.saveBtn) this.saveBtn.addEventListener('click', () => this.save());

    // タブ切り替え
    if (this.tabsEl) {
      this.tabsEl.querySelectorAll('button[data-tab]').forEach((btn) => {
        btn.addEventListener('click', () => this.switchTab(btn.dataset.tab));
      });
    }
  },

  /** ニックネーム取得 */
  getNickname(login) {
    try {
      const map = JSON.parse(
        localStorage.getItem(this._NICK_KEY) || '{}'
      );
      return map[String(login)] || '';
    } catch (_) {
      return '';
    }
  },

  /** ニックネーム保存 */
  setNickname(login, name) {
    try {
      const map = JSON.parse(
        localStorage.getItem(this._NICK_KEY) || '{}'
      );
      if (name) {
        map[String(login)] = name;
      } else {
        delete map[String(login)];
      }
      localStorage.setItem(this._NICK_KEY, JSON.stringify(map));
    } catch (_) {}
  },

  /** 開く */
  async open() {
    if (this.overlay) this.overlay.classList.remove('hidden');
    if (this.modal) this.modal.classList.remove('hidden');
    this.activeTab = 'entry';
    this.updateTabStyles();
    await this.fetchSettings();
  },

  /** 指定タブを開く */
  openToTab(tab) {
    if (this.overlay) this.overlay.classList.remove('hidden');
    if (this.modal) this.modal.classList.remove('hidden');
    this.activeTab = tab;
    this.updateTabStyles();
    if (tab === 'connection') {
      this.renderConnectionTab();
    } else {
      this.fetchSettings();
    }
  },

  /** 閉じる */
  close() {
    if (this.overlay) this.overlay.classList.add('hidden');
    if (this.modal) this.modal.classList.add('hidden');
  },

  /** 設定取得 */
  async fetchSettings() {
    this.isLoading = true;
    this.renderLoading();
    try {
      this.settings = await getSettings();
      this.notificationEnabled = this.settings.notification.enabled;
      this.minConfidence = this.settings.notification.min_confidence;
      this.soundEnabled = this.settings.notification.sound_enabled;
    } catch (e) {
      // エラー時はデフォルト値
    } finally {
      this.isLoading = false;
      this.renderTab();
    }
  },

  /** タブ切替 */
  switchTab(tab) {
    this.activeTab = tab;
    this.updateTabStyles();
    if (tab === 'connection') {
      this.renderConnectionTab();
    } else {
      if (!this.settings) {
        this.fetchSettings();
      } else {
        this.renderTab();
      }
    }
    if (this.saveBtn) {
      this.saveBtn.classList.toggle('hidden', tab !== 'notification');
    }
  },

  /** タブスタイル更新 */
  updateTabStyles() {
    if (!this.tabsEl) return;
    this.tabsEl.querySelectorAll('button[data-tab]').forEach((btn) => {
      if (btn.dataset.tab === this.activeTab) {
        btn.className = 'flex-1 px-4 py-2 text-sm font-medium text-blue-400 border-b-2 border-blue-400';
      } else {
        btn.className = 'flex-1 px-4 py-2 text-sm font-medium text-gray-400 hover:text-gray-200';
      }
    });
    if (this.saveBtn) {
      this.saveBtn.classList.toggle('hidden', this.activeTab !== 'notification');
    }
  },

  /** ローディング描画 */
  renderLoading() {
    if (!this.content) return;
    this.content.innerHTML = `
      <div class="flex items-center justify-center h-40">
        <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500"></div>
      </div>`;
  },

  /** タブ描画 */
  renderTab() {
    if (!this.content || !this.settings) return;
    const s = this.settings;

    switch (this.activeTab) {
      case 'entry':
        this.content.innerHTML = this.renderEntryFilter(s.trading.entry_filter);
        break;
      case 'capital':
        this.content.innerHTML = this.renderCapital(s.trading.capital_management);
        break;
      case 'position':
        this.content.innerHTML = this.renderPosition(s.trading.position_management);
        break;
      case 'notification':
        this.content.innerHTML = this.renderNotification();
        this.bindNotificationEvents();
        break;
      case 'connection':
        this.renderConnectionTab();
        break;
    }
  },

  /** エントリーフィルター */
  renderEntryFilter(c) {
    return `
      <div class="space-y-4">
        <h3 class="text-sm font-medium text-gray-300">エントリーフィルター</h3>
        <div class="grid grid-cols-2 gap-4">
          ${this.settingItem('RANGE×DAY BBW閾値', c.range_day_bbw_threshold)}
          ${this.settingItem('RANGE×DAY スコアプレミアム', c.range_day_score_premium)}
          ${this.settingItem('Weak Hours', c.weak_hours_enabled ? 'ON' : 'OFF')}
          ${this.settingItem('Weak Hours プレミアム', c.weak_hours_score_premium)}
        </div>
        <p class="text-xs text-gray-500">※ 変更にはアプリの再起動が必要です</p>
      </div>`;
  },

  /** 資金管理 */
  renderCapital(c) {
    return `
      <div class="space-y-4">
        <h3 class="text-sm font-medium text-gray-300">資金管理</h3>
        <div class="grid grid-cols-2 gap-4">
          ${this.settingItem('動的ロット', c.use_dynamic_lot ? 'ON' : 'OFF')}
          ${this.settingItem('基本リスク率', (c.base_risk_pct * 100).toFixed(1) + '%')}
          ${this.settingItem('最大ロット/トレード', c.max_lot_per_trade)}
          ${this.settingItem('最大エクスポージャー', c.max_total_exposure_lot + ' lot')}
          ${this.settingItem('エクイティフロア', (c.equity_floor_pct * 100).toFixed(0) + '%')}
          ${this.settingItem('SLバッファ', c.slippage_buffer_pips + ' pips')}
        </div>
      </div>`;
  },

  /** ポジション管理 */
  renderPosition(c) {
    return `
      <div class="space-y-4">
        <h3 class="text-sm font-medium text-gray-300">ポジション管理</h3>
        <div class="grid grid-cols-2 gap-4">
          ${this.settingItem('PM有効', c.enable_position_manager ? 'ON' : 'OFF')}
          ${this.settingItem('停滞最小MFE', c.stagnation_min_mfe_r + 'R')}
          ${this.settingItem('早期BE閾値', c.range_day_early_be_r + 'R')}
          ${this.settingItem('保険トリガー', c.insurance_trigger_r + 'R')}
          ${this.settingItem('1R部分利確比率', (c.partial_close_1r_ratio * 100).toFixed(0) + '%')}
          ${this.settingItem('トレーリング開始', c.trailing_start_r + 'R')}
        </div>
      </div>`;
  },

  /** 通知設定 */
  renderNotification() {
    return `
      <div class="space-y-4">
        <div class="flex items-center justify-between">
          <label class="text-sm">通知を有効にする</label>
          <button id="toggle-notification-enabled" role="switch" aria-checked="${this.notificationEnabled}"
                  class="toggle">
            <span class="toggle-knob"></span>
          </button>
        </div>
        <div>
          <label class="text-sm text-gray-400">
            最小確度: <span id="min-confidence-display">${(this.minConfidence * 100).toFixed(0)}</span>%
          </label>
          <input type="range" id="min-confidence-range" min="0" max="1" step="0.05"
                 value="${this.minConfidence}"
                 class="w-full accent-blue-500 mt-1">
          <div class="flex justify-between text-xs text-gray-400 mt-1">
            <span>0%</span><span>50%</span><span>100%</span>
          </div>
        </div>
        <div class="flex items-center justify-between">
          <label class="text-sm">サウンドを有効にする</label>
          <button id="toggle-sound-enabled" role="switch" aria-checked="${this.soundEnabled}"
                  class="toggle">
            <span class="toggle-knob"></span>
          </button>
        </div>
      </div>`;
  },

  /** 通知イベントバインド */
  bindNotificationEvents() {
    const toggleNotif = document.getElementById('toggle-notification-enabled');
    const toggleSound = document.getElementById('toggle-sound-enabled');
    const rangeEl = document.getElementById('min-confidence-range');
    const displayEl = document.getElementById('min-confidence-display');

    if (toggleNotif) {
      toggleNotif.addEventListener('click', () => {
        this.notificationEnabled = !this.notificationEnabled;
        toggleNotif.setAttribute('aria-checked', String(this.notificationEnabled));
      });
    }
    if (toggleSound) {
      toggleSound.addEventListener('click', () => {
        this.soundEnabled = !this.soundEnabled;
        toggleSound.setAttribute('aria-checked', String(this.soundEnabled));
      });
    }
    if (rangeEl) {
      rangeEl.addEventListener('input', (e) => {
        this.minConfidence = parseFloat(e.target.value);
        if (displayEl) displayEl.textContent = (this.minConfidence * 100).toFixed(0);
      });
    }
  },

  /** 保存 */
  async save() {
    if (this.isSaving) return;
    this.isSaving = true;
    if (this.saveBtn) {
      this.saveBtn.textContent = '保存中...';
      this.saveBtn.disabled = true;
    }
    try {
      const updated = await updateSettings({
        notification: {
          enabled: this.notificationEnabled,
          min_confidence: this.minConfidence,
          sound_enabled: this.soundEnabled,
        },
      });
      this.settings = updated;
      // NotificationManagerにも反映
      if (typeof NotificationManager !== 'undefined') {
        NotificationManager.enabled = this.notificationEnabled;
        NotificationManager.minConfidence = this.minConfidence;
        NotificationManager.soundEnabled = this.soundEnabled;
      }
    } catch (e) {
      // エラー処理
    } finally {
      this.isSaving = false;
      if (this.saveBtn) {
        this.saveBtn.textContent = '保存';
        this.saveBtn.disabled = false;
      }
    }
  },

  /** 接続タブ描画 */
  async renderConnectionTab() {
    if (!this.content) return;
    this.renderLoading();

    let presets = [];
    try {
      const res = await getAccountPresets();
      presets = (res && res.accounts) ? res.accounts : [];
    } catch (_) {}

    const dash = (typeof DashboardApp !== 'undefined')
      ? DashboardApp.dashboard
      : null;
    const mode = (typeof DashboardApp !== 'undefined')
      ? DashboardApp.tradingMode
      : null;
    const isConnected = mode && mode.connected;
    const acct = isConnected && dash && dash.account ? dash.account : null;
    const nick = acct ? this.getNickname(acct.login) : '';

    const lev = acct ? (acct.leverage ? '1:' + acct.leverage : '-') : '-';
    const cur = acct ? (acct.currency || '-') : '-';
    const currentSection = acct ? `
      <div class="space-y-2">
        <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          現在の接続
        </h3>
        <div class="bg-gray-700/40 rounded-lg p-3 space-y-1.5 sm:space-y-2">
          ${this._connRow('ID', String(acct.login || '-'))}
          ${this._connRow('サーバー', acct.server || '-')}
          <div class="flex items-center justify-between text-sm">
            <span class="text-gray-400">${acct.name || '-'}</span>
            <span class="text-gray-500 text-xs tabular-nums">${cur} / ${lev}</span>
          </div>
        </div>
        <div class="flex gap-2">
          <input id="conn-nickname-input" type="text"
            class="flex-1 bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500 placeholder-gray-500"
            placeholder="ヘッダー表示名 (例: XMデモ)"
            value="${nick}" maxlength="20" />
          <button id="conn-nickname-save"
            class="px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-700 text-sm text-white font-medium transition-colors whitespace-nowrap">
            名前を保存
          </button>
        </div>
      </div>` : `
      <div class="bg-gray-700/20 rounded-lg p-3 text-sm text-gray-500 text-center">
        MT5未接続
      </div>`;

    const selectOptions = presets.length
      ? presets.map((p) => {
          const label = p.name
            ? `${p.name} (${p.login})`
            : `${p.login} @ ${p.server}`;
          return `<option value="${p.login}" data-server="${p.server}">${label}</option>`;
        }).join('')
      : '<option value="" disabled>登録済みの口座がありません</option>';

    const switchSection = `
      <div class="space-y-2">
        <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          口座切替
        </h3>
        <div class="flex gap-2">
          <select id="conn-preset-select"
            class="flex-1 bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
            ${presets.length ? '' : 'disabled'}>
            ${presets.length ? '' : '<option value="">-- 口座なし --</option>'}
            ${selectOptions}
          </select>
          <button id="conn-switch-btn"
            class="px-3 py-1.5 rounded bg-indigo-600 hover:bg-indigo-700 text-sm text-white font-medium transition-colors whitespace-nowrap disabled:opacity-50"
            ${presets.length ? '' : 'disabled'}>
            切替
          </button>
        </div>
        <p class="text-xs text-gray-500">
          MT5ターミナルに「パスワードを保存」済みの口座を使用します
        </p>
      </div>`;

    const presetRows = presets.map((p) => {
      const label = p.name || `${p.login}`;
      const sub = `${p.login} @ ${p.server}`;
      return `
        <div class="flex items-center justify-between py-1.5 border-b border-gray-700/50 last:border-0">
          <div>
            <div class="text-sm text-gray-200">${label}</div>
            <div class="text-xs text-gray-500 tabular-nums">${sub}</div>
          </div>
          <button class="conn-delete-btn px-2.5 py-1 rounded bg-red-900/60 hover:bg-red-700 text-xs text-red-300 hover:text-white transition-colors"
            data-login="${p.login}">
            削除
          </button>
        </div>`;
    }).join('');

    const addSection = `
      <div class="space-y-2">
        <button id="conn-add-toggle" class="flex items-center gap-1.5 group">
          <svg id="conn-add-chevron" class="w-3.5 h-3.5 transition-transform -rotate-90 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
          </svg>
          <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wider group-hover:text-gray-300 transition-colors">
            口座を登録
          </h3>
        </button>
        <div id="conn-add-fields" class="hidden space-y-1.5">
          <input id="conn-add-login" type="number" placeholder="ログインID *"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2.5 py-1 sm:py-1.5 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500 placeholder-gray-500" />
          <input id="conn-add-server" type="text" placeholder="サーバー名 * (例: XMTrading-MT5)"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2.5 py-1 sm:py-1.5 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500 placeholder-gray-500" />
          <input id="conn-add-name" type="text" placeholder="表示名 (省略可)"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2.5 py-1 sm:py-1.5 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500 placeholder-gray-500" maxlength="30" />
          <button id="conn-add-btn"
            class="w-full py-1 sm:py-1.5 rounded bg-blue-600 hover:bg-blue-700 text-sm text-white font-medium transition-colors">
            登録
          </button>
        </div>
      </div>`;

    const listSection = presets.length ? `
      <div class="space-y-1">
        <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          登録済み口座
        </h3>
        <div class="bg-gray-700/20 rounded-lg px-3">
          ${presetRows}
        </div>
      </div>` : '';

    this.content.innerHTML = `
      <div class="space-y-3 sm:space-y-5">
        ${currentSection}
        ${switchSection}
        ${addSection}
        ${listSection}
      </div>`;

    this.bindConnectionEvents(acct ? acct.login : null);
  },

  /** 接続タブの行HTML */
  _connRow(label, value) {
    return `
      <div class="flex items-center justify-between text-sm">
        <span class="text-gray-400">${label}</span>
        <span class="text-gray-200 font-medium tabular-nums">${value}</span>
      </div>`;
  },

  /** 接続タブのイベントバインド */
  bindConnectionEvents(currentLogin) {
    // 口座登録セクションの折りたたみ
    const addToggle = document.getElementById('conn-add-toggle');
    const addFields = document.getElementById('conn-add-fields');
    const addChevron = document.getElementById('conn-add-chevron');
    if (addToggle && addFields) {
      addToggle.addEventListener('click', () => {
        const isHidden = addFields.classList.toggle('hidden');
        if (addChevron) addChevron.style.transform = isHidden ? 'rotate(-90deg)' : '';
      });
    }
    // ヘッダー表示名保存
    const nickSave = document.getElementById('conn-nickname-save');
    const nickInput = document.getElementById('conn-nickname-input');
    if (nickSave && nickInput && currentLogin) {
      nickSave.addEventListener('click', () => {
        const name = nickInput.value.trim();
        this.setNickname(currentLogin, name);
        if (typeof DashboardApp !== 'undefined') {
          DashboardApp.updateHeaderAccountName();
        }
        const orig = nickSave.className;
        nickSave.textContent = '保存済み';
        nickSave.className = orig.replace('bg-blue-600 hover:bg-blue-700', 'bg-green-700');
        setTimeout(() => {
          nickSave.textContent = '名前を保存';
          nickSave.className = orig;
        }, 1500);
      });
    }

    // 口座切替
    const switchBtn = document.getElementById('conn-switch-btn');
    const select = document.getElementById('conn-preset-select');
    if (switchBtn && select) {
      switchBtn.addEventListener('click', async () => {
        const login = parseInt(select.value, 10);
        const opt = select.options[select.selectedIndex];
        const server = opt ? (opt.dataset.server || '') : '';
        if (!login || !server) return;
        switchBtn.textContent = '切替中...';
        switchBtn.disabled = true;
        try {
          await switchAccountPreset(login, server);
          // ダッシュボード全体を再取得して口座情報・ヘッダーを更新
          if (typeof DashboardApp !== 'undefined') {
            await Promise.all([
              DashboardApp.fetchTradingMode(),
              DashboardApp.fetchAll(),
            ]);
          }
          // 接続タブも最新情報で再描画
          this.renderConnectionTab();
        } catch (e) {
          console.error('口座切替エラー:', e);
          switchBtn.textContent = 'エラー';
          setTimeout(() => {
            switchBtn.textContent = '切替';
            switchBtn.disabled = false;
          }, 2000);
        }
      });
    }

    // 口座登録
    const addBtn = document.getElementById('conn-add-btn');
    if (addBtn) {
      addBtn.addEventListener('click', async () => {
        const loginVal = parseInt(
          (document.getElementById('conn-add-login') || {}).value, 10
        );
        const serverVal = (
          (document.getElementById('conn-add-server') || {}).value || ''
        ).trim();
        const nameVal = (
          (document.getElementById('conn-add-name') || {}).value || ''
        ).trim();
        if (!loginVal || !serverVal) {
          const loginEl = document.getElementById('conn-add-login');
          if (loginEl) loginEl.focus();
          return;
        }
        addBtn.textContent = '登録中...';
        addBtn.disabled = true;
        try {
          await addAccountPreset(loginVal, serverVal, nameVal);
          this.renderConnectionTab();
        } catch (e) {
          console.error('口座登録エラー:', e);
          addBtn.textContent = 'エラー';
          setTimeout(() => {
            addBtn.textContent = '登録';
            addBtn.disabled = false;
          }, 2000);
        }
      });
    }

    // 口座削除
    document.querySelectorAll('.conn-delete-btn').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const login = parseInt(btn.dataset.login, 10);
        if (!login) return;
        btn.textContent = '削除中...';
        btn.disabled = true;
        try {
          await deleteAccountPreset(login);
          this.renderConnectionTab();
        } catch (e) {
          console.error('口座削除エラー:', e);
          btn.textContent = 'エラー';
        }
      });
    });
  },

  /** 設定項目HTML */
  settingItem(label, value) {
    return `
      <div>
        <p class="text-xs text-gray-400">${label}</p>
        <p class="text-sm font-medium">${value}</p>
      </div>`;
  },
};

// ページ読み込み時に初期化
document.addEventListener('DOMContentLoaded', () => {
  SettingsManager.init();
});
