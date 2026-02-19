/** ダッシュボードロジック */

const DashboardApp = {
  symbol: 'USDJPY',
  dashboard: null,
  positions: [],
  trades: [],
  tradeSummary: null,
  currentSignals: [],
  indicators: null,
  isLoading: true,
  tradeHistoryExpanded: true,
  pollInterval: null,
  indicatorInterval: null,
  signalWs: null,
  // トレーディングコントロール状態
  tradingMode: null,
  tcBusy: false,
  // 分析パネル
  lastAnalysis: null,

  /** 初期化 */
  init() {
    // シンボルセレクター
    const selector = document.getElementById('symbol-selector');
    if (selector) {
      selector.addEventListener('change', (e) => {
        this.symbol = e.target.value;
        const headerSymbol = document.getElementById('header-symbol');
        if (headerSymbol) headerSymbol.textContent = this.symbol;
        document.getElementById('chart-title').textContent = this.symbol + ' チャート';
        ChartManager.setSymbol(this.symbol);
        this.renderTradingControl();
        this.fetchAll();
        this.fetchIndicators();
        this.fetchSignals();
      });
    }

    // トレード履歴トグル
    const toggle = document.getElementById('trade-history-toggle');
    if (toggle) {
      toggle.addEventListener('click', () => {
        this.tradeHistoryExpanded = !this.tradeHistoryExpanded;
        const table = document.getElementById('trade-history-table');
        const chevron = document.getElementById('trade-history-chevron');
        if (table) table.classList.toggle('hidden', !this.tradeHistoryExpanded);
        if (chevron) {
          chevron.style.transform = this.tradeHistoryExpanded ? '' : 'rotate(-90deg)';
        }
      });
    }

    // トレーディングコントロール初期化
    this.initTradingControl();

    // チャート初期化
    ChartManager.init('chart-container', this.symbol);

    // データ取得
    this.fetchAll();
    this.fetchSignals();
    this.fetchIndicators();
    this.fetchTradingMode();
    this.fetchAnalysis();

    // ポーリング
    this.pollInterval = setInterval(() => this.fetchAll(), 30000);
    this.indicatorInterval = setInterval(() => this.fetchIndicators(), 30000);
    // トレーディング状態ポーリング（5秒）
    setInterval(() => this.fetchTradingMode(), 5000);
    // 分析ポーリング（1秒 = エンジンtickと同期）
    setInterval(() => this.fetchAnalysis(), 1000);

    // シグナルWebSocket
    this.signalWs = createWebSocketClient('/ws/signals');
    this.signalWs.on('signal_update', (msg) => {
      const signal = msg.data;
      if (signal.symbol === this.symbol) {
        const idx = this.currentSignals.findIndex((s) => s.signal_id === signal.signal_id);
        if (idx >= 0) {
          this.currentSignals[idx] = signal;
        } else {
          this.currentSignals.unshift(signal);
          if (this.currentSignals.length > 3) this.currentSignals.length = 3;
        }
        this.renderSignals();
        ChartManager.setSignals(this.currentSignals);
      }
    });
    this.signalWs.connect();

    // ダッシュボードWebSocket（ポジション・トレード即時更新）
    this.dashWs = createWebSocketClient('/ws/dashboard');
    this.dashWs.on('position_update', () => {
      this.fetchPositionsAndTrades();
    });
    this.dashWs.connect();
  },

  // ── 分析パネル ──

  /** 分析データ取得 */
  async fetchAnalysis() {
    try {
      this.lastAnalysis = await getAnalysis();
    } catch (e) {
      this.lastAnalysis = null;
    }
    this.renderAnalysis();
  },

  /** 分析パネル描画 */
  renderAnalysis() {
    const panel = document.getElementById('analysis-panel');
    if (!panel) return;

    const a = this.lastAnalysis;
    // エンジン未起動ならパネル非表示
    const isLive = this.tradingMode && this.tradingMode.mode === 'live';
    if (!isLive) {
      panel.classList.add('hidden');
      return;
    }
    panel.classList.remove('hidden');

    // エンジン/接続状態バナー（aがnullでも表示）
    const statusBanner = document.getElementById('ap-engine-status');
    if (statusBanner) {
      if (!a) {
        statusBanner.textContent = '⚠️ 分析データ取得中...';
        statusBanner.className = 'text-xs text-yellow-400 mb-2';
      } else {
        const parts = [];
        if (!a.engine_running) parts.push('⚠️ エンジン停止中');
        else if (!a.mt5_connected) parts.push('📡 MT5未接続（デモシグナルモード）');
        else parts.push('✅ MT5接続済み');
        if (a.demo_mode) parts.push('🔵 デモモード');
        if (a.auto_trade_enabled) parts.push('🤖 自動売買ON');
        statusBanner.textContent = parts.join(' | ');
        statusBanner.className = a.engine_running
          ? (a.mt5_connected ? 'text-xs text-green-400 mb-2' : 'text-xs text-yellow-400 mb-2')
          : 'text-xs text-red-400 mb-2';
      }
    }

    // aがない場合はステータスバナーのみ表示して終了
    if (!a) return;

    // 方向バッジ
    const dirBadge = document.getElementById('ap-direction-badge');
    if (dirBadge) {
      const dirStyles = {
        BUY: 'bg-green-900/40 text-green-400 border border-green-700/50',
        SELL: 'bg-red-900/40 text-red-400 border border-red-700/50',
        HOLD: 'bg-gray-700 text-gray-400',
      };
      dirBadge.textContent = a.direction || '--';
      dirBadge.className = 'inline-flex items-center px-2 py-0.5 rounded text-xs font-bold ' + (dirStyles[a.direction] || dirStyles.HOLD);
    }

    // レジーム・モード
    const regime = document.getElementById('ap-regime');
    const mode = document.getElementById('ap-mode');
    if (regime) regime.textContent = a.regime || '--';
    if (mode) mode.textContent = a.mode ? a.mode.replace('_', ' ') : '--';

    // 最終tick時刻
    const tickTime = document.getElementById('ap-tick-time');
    if (tickTime && a.last_tick_time) {
      const d = new Date(a.last_tick_time);
      tickTime.textContent = d.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' UTC';
    }

    // スコアバー
    const score = a.consensus_score || 0;
    const threshold = a.entry_threshold || 1;
    const scoreText = document.getElementById('ap-score-text');
    const scoreBar = document.getElementById('ap-score-bar');
    const thresholdMarker = document.getElementById('ap-threshold-marker');
    if (scoreText) scoreText.textContent = score.toFixed(2) + ' / ' + threshold.toFixed(1);
    if (scoreBar) {
      // バーの最大値は threshold * 1.4 として可視化
      const maxVal = threshold * 1.5;
      const pct = Math.min(100, (score / maxVal) * 100);
      const barColor = score >= threshold ? 'bg-green-500' : score >= threshold * 0.7 ? 'bg-yellow-500' : 'bg-red-500';
      scoreBar.style.width = pct + '%';
      scoreBar.className = 'h-full rounded-full transition-all duration-500 ' + barColor;
    }
    if (thresholdMarker) {
      const maxVal = threshold * 1.5;
      const thPct = Math.min(99, (threshold / maxVal) * 100);
      thresholdMarker.style.left = thPct + '%';
    }

    // サブ指標
    const htfEl = document.getElementById('ap-htf');
    const trendEl = document.getElementById('ap-trend');
    const penaltyEl = document.getElementById('ap-penalty');
    if (htfEl) {
      const htfPct = (a.htf_alignment * 100).toFixed(0) + '%';
      htfEl.textContent = htfPct;
      htfEl.className = 'font-bold tabular-nums ' + (a.htf_alignment >= 0.6 ? 'text-green-400' : a.htf_alignment >= 0.3 ? 'text-yellow-400' : 'text-red-400');
    }
    if (trendEl) {
      trendEl.textContent = (a.trend_strength || 0).toFixed(2);
      trendEl.className = 'font-bold tabular-nums ' + (a.trend_strength >= 0.5 ? 'text-green-400' : 'text-gray-300');
    }
    if (penaltyEl) {
      penaltyEl.textContent = (a.penalty_total || 0).toFixed(2);
      penaltyEl.className = 'font-bold tabular-nums ' + (a.penalty_total >= 0.5 ? 'text-red-400' : 'text-gray-300');
    }

    // 時間足スコア（コンセンサス詳細表示）
    const tfEl = document.getElementById('ap-tf-scores');
    if (tfEl && a.tf_scores) {
      const tfOrder = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
      const tfs = Object.entries(a.tf_scores).sort((x, y) => {
        return tfOrder.indexOf(x[0]) - tfOrder.indexOf(y[0]);
      });
      const bd = a.tf_breakdowns || {};
      const aligned = a.aligned_tfs || [];

      // TF方向を内訳合計から推定
      const tfDirs = {};
      for (const [tf] of tfs) {
        const detail = bd[tf];
        if (detail) {
          const sum = Object.values(detail).reduce((s, v) => s + v, 0);
          tfDirs[tf] = sum > 0.5 ? 'BUY' : sum < -0.5 ? 'SELL' : 'HOLD';
        } else {
          tfDirs[tf] = 'HOLD';
        }
      }

      // コンセンサス投票サマリー
      let buyCount = 0, sellCount = 0, holdCount = 0;
      for (const [tf] of tfs) {
        if (tfDirs[tf] === 'BUY') buyCount++;
        else if (tfDirs[tf] === 'SELL') sellCount++;
        else holdCount++;
      }
      const total = tfs.length;
      const buyPct = total > 0 ? (buyCount / total * 100) : 0;
      const sellPct = total > 0 ? (sellCount / total * 100) : 0;
      const holdPct = total > 0 ? (holdCount / total * 100) : 0;

      // ペナルティ内訳
      const pb = a.penalty_breakdown || {};
      const penaltyItems = Object.entries(pb).filter(([, v]) => v > 0);
      const penaltyHtml = penaltyItems.length > 0
        ? `<div class="flex items-center gap-2 text-[10px] mb-2">
            <span class="text-gray-500">Penalty:</span>
            ${penaltyItems.map(([k, v]) => `<span class="text-red-400/80 bg-red-900/20 px-1.5 py-0.5 rounded">${k} -${v.toFixed(2)}</span>`).join('')}
          </div>`
        : '';

      // 投票サマリーバー + ペナルティ
      const summaryHtml = `
        <div class="mb-2">
          <div class="flex items-center gap-2 text-[10px] mb-1">
            <span class="text-gray-500">Vote:</span>
            <span class="text-green-400 font-bold">${buyCount} BUY</span>
            <span class="text-gray-500">|</span>
            <span class="text-red-400 font-bold">${sellCount} SELL</span>
            <span class="text-gray-500">|</span>
            <span class="text-gray-400">${holdCount} HOLD</span>
            <span class="text-gray-600 ml-auto">${aligned.length}/${total} aligned</span>
          </div>
          <div class="w-full h-1.5 rounded-full overflow-hidden flex">
            <div class="bg-green-500/70 h-full" style="width:${buyPct}%"></div>
            <div class="bg-gray-600/50 h-full" style="width:${holdPct}%"></div>
            <div class="bg-red-500/70 h-full" style="width:${sellPct}%"></div>
          </div>
        </div>
        ${penaltyHtml}`;

      // 指標ラベル（短縮名）
      const indLabel = {
        trend: 'TRD', adx: 'ADX', rsi: 'RSI',
        macd_slope: 'MACD', divergence: 'DIV',
        ema_cross: 'EMA', stochastic: 'STO', htf: 'HTF',
      };

      // TFカード
      const cardsHtml = tfs.map(([tf, sc]) => {
        const isAligned = aligned.includes(tf);
        const dir = tfDirs[tf];
        const dirIcon = dir === 'BUY' ? '&#9650;' : dir === 'SELL' ? '&#9660;' : '&#9644;';
        const dirColor = dir === 'BUY' ? 'text-green-400' : dir === 'SELL' ? 'text-red-400' : 'text-gray-500';
        const borderCls = isAligned
          ? 'border-green-600/60 bg-green-900/10'
          : 'border-gray-700 bg-gray-800/60';
        const alignBadge = isAligned
          ? '<span class="text-[8px] text-green-400 font-bold ml-1">&#10003;</span>'
          : '';
        const scColor = sc > 0.5 ? 'text-green-400' : sc > 0.2 ? 'text-yellow-400' : 'text-gray-500';
        const barW = Math.min(100, sc * 100);
        const barColor = dir === 'BUY' ? 'bg-green-500/60' : dir === 'SELL' ? 'bg-red-500/60' : 'bg-gray-500/40';

        // 内訳（影響度順にバー表示）
        const detail = bd[tf];
        let detailHtml = '';
        if (detail) {
          const items = Object.entries(detail)
            .filter(([, v]) => Math.abs(v) >= 0.1)
            .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
          // 最大の指標値でバー幅を正規化
          const maxAbs = items.length > 0 ? Math.abs(items[0][1]) : 1;
          detailHtml = items.slice(0, 4).map(([k, v]) => {
            const c = v > 0 ? 'text-green-400' : v < 0 ? 'text-red-400' : 'text-gray-600';
            const bg = v > 0 ? 'bg-green-500/40' : 'bg-red-500/40';
            const w = Math.round(Math.abs(v) / Math.max(maxAbs, 0.1) * 100);
            const lbl = indLabel[k] || k.slice(0, 4).toUpperCase();
            return `<div class="flex items-center gap-1">
              <span class="text-[8px] text-gray-500 w-7 text-right flex-shrink-0">${lbl}</span>
              <div class="flex-1 h-1 bg-gray-700/40 rounded-full overflow-hidden">
                <div class="${bg} h-full rounded-full" style="width:${w}%"></div>
              </div>
              <span class="text-[9px] tabular-nums ${c} w-6 text-right flex-shrink-0">${v >= 0 ? '+' : ''}${v.toFixed(1)}</span>
            </div>`;
          }).join('');
        }

        return `<div class="rounded border ${borderCls} px-2 py-1.5 flex-1 min-w-[100px]">
          <div class="flex items-center justify-between mb-0.5">
            <div class="flex items-center">
              <span class="${dirColor} text-[10px] mr-1">${dirIcon}</span>
              <span class="text-[10px] text-gray-400 uppercase font-bold">${tf}</span>
              ${alignBadge}
            </div>
            <span class="text-xs font-bold tabular-nums ${scColor}">${sc.toFixed(2)}</span>
          </div>
          <div class="w-full bg-gray-700/50 rounded-full h-1 mb-1">
            <div class="${barColor} h-1 rounded-full" style="width:${barW}%"></div>
          </div>
          ${detailHtml ? `<div class="space-y-0.5">${detailHtml}</div>` : ''}
        </div>`;
      }).join('');

      tfEl.innerHTML = summaryHtml + `<div class="flex gap-2 flex-wrap">${cardsHtml}</div>`;
    }

    // 判断理由
    const rationaleEl = document.getElementById('ap-rationale');
    if (rationaleEl) rationaleEl.textContent = a.rationale || '--';
  },

  // ── トレーディングコントロール ──

  /** トレーディングコントロール初期化 */
  initTradingControl() {
    const settingsMt5Btn = document.getElementById('settings-mt5-btn');
    const autoBtn = document.getElementById('tc-auto-trade-btn');
    const demoBtn = document.getElementById('tc-demo-mode-btn');

    if (settingsMt5Btn) {
      settingsMt5Btn.addEventListener('click', () => this.handleMT5Toggle());
    }
    if (autoBtn) {
      autoBtn.addEventListener('click', () => this.handleAutoTradeToggle());
    }
    if (demoBtn) {
      demoBtn.addEventListener('click', () => this.handleDemoModeToggle());
    }
  },

  /** トレーディングモード取得 */
  async fetchTradingMode() {
    try {
      this.tradingMode = await getTradingMode();
    } catch (e) {
      this.tradingMode = null;
    }
    // ユーザー操作中（tcBusy）はポーリングによる再描画をスキップ
    if (!this.tcBusy) {
      this.renderTradingControl();
    }
    this.renderAnalysis();
  },

  /** MT5接続/切断トグル */
  async handleMT5Toggle() {
    if (this.tcBusy) return;
    const isConnected = this.tradingMode && this.tradingMode.connected;

    // 切断時は確認
    if (isConnected) {
      if (!confirm('MT5から切断しますか？')) return;
    }

    this.tcBusy = true;
    try {
      this.renderTradingControl();
      if (isConnected) {
        await disconnectMT5();
      } else {
        await connectMT5();
      }
      await this.fetchTradingMode();
    } catch (e) {
      console.error('MT5操作エラー:', e);
    } finally {
      this.tcBusy = false;
      this.renderTradingControl();
    }
  },

  /** デモモードON/OFFトグル（選択中シンボル対象） */
  async handleDemoModeToggle() {
    if (this.tcBusy) return;
    const m = this.tradingMode;
    const symbolDemoStates = (m && m.symbol_demo_mode) || {};
    const currentDemoOn = Object.prototype.hasOwnProperty.call(symbolDemoStates, this.symbol)
      ? symbolDemoStates[this.symbol]
      : false;

    this.tcBusy = true;
    try {
      this.renderTradingControl();
      const result = await toggleSymbolDemoMode(this.symbol, !currentDemoOn);
      if (result) this.tradingMode = result;
    } catch (e) {
      console.error('デモモード切替エラー:', e);
    } finally {
      this.tcBusy = false;
      this.renderTradingControl();
    }
  },

  /** 自動トレードON/OFFトグル（選択中シンボル対象） */
  async handleAutoTradeToggle() {
    if (this.tcBusy) return;
    const m = this.tradingMode;
    const symbolAutoStates = (m && m.symbol_auto_trade) || {};
    const symbolDemoStates = (m && m.symbol_demo_mode) || {};
    const currentAutoOn = Object.prototype.hasOwnProperty.call(symbolAutoStates, this.symbol)
      ? symbolAutoStates[this.symbol]
      : false;
    const isDemoOn = Object.prototype.hasOwnProperty.call(symbolDemoStates, this.symbol)
      ? symbolDemoStates[this.symbol]
      : false;
    const nextOn = !currentAutoOn;

    // リアルモードでONにする際は確認
    if (nextOn && !isDemoOn) {
      if (!confirm(`${this.symbol} の自動トレード（リアルモード）を開始しますか？\n実際の売買が実行されます。`)) return;
    }

    this.tcBusy = true;
    try {
      this.renderTradingControl();
      const result = await toggleSymbolAutoTrade(this.symbol, nextOn);
      if (result) this.tradingMode = result;
    } catch (e) {
      console.error('自動トレード切替エラー:', e);
    } finally {
      this.tcBusy = false;
      this.renderTradingControl();
    }
  },

  /** トレーディングコントロール描画 */
  renderTradingControl() {
    const modeBadge = document.getElementById('tc-mode-badge');
    const mt5Badge = document.getElementById('tc-mt5-badge');
    const mt5Dot = document.getElementById('tc-mt5-dot');
    const mt5Btn = document.getElementById('settings-mt5-btn');
    const autoBtn = document.getElementById('tc-auto-trade-btn');
    if (!modeBadge) return;

    const m = this.tradingMode;
    const isLive = m && m.mode === 'live';
    const isConnected = m && m.connected;
    const isRunning = m && m.engine_running;
    const symbolAutoStates = (m && m.symbol_auto_trade) || {};
    const symbolDemoStates = (m && m.symbol_demo_mode) || {};
    const isAutoOn = Object.prototype.hasOwnProperty.call(symbolAutoStates, this.symbol)
      ? symbolAutoStates[this.symbol] : false;
    const isDemoOn = Object.prototype.hasOwnProperty.call(symbolDemoStates, this.symbol)
      ? symbolDemoStates[this.symbol] : false;

    // モードバッジ（デモ/リアル × AUTO ON/OFF の4パターン）
    if (!isConnected) {
      modeBadge.textContent = 'STANDBY';
      modeBadge.className = 'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-gray-700 text-gray-400';
    } else if (isDemoOn && isAutoOn) {
      modeBadge.textContent = 'デモオート';
      modeBadge.className = 'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-orange-900/40 text-orange-400 border border-orange-700/50';
    } else if (isDemoOn) {
      modeBadge.textContent = 'デモ';
      modeBadge.className = 'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-orange-900/40 text-orange-400 border border-orange-700/50';
    } else if (isAutoOn) {
      modeBadge.textContent = 'リアルオート';
      modeBadge.className = 'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-green-900/60 text-green-300 border border-green-600/60';
    } else {
      modeBadge.textContent = 'リアル';
      modeBadge.className = 'inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-blue-900/40 text-blue-400 border border-blue-700/50';
    }

    // MT5接続バッジ
    if (isConnected) {
      mt5Badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-900/30 text-green-400 border border-green-800/50';
      mt5Badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>MT5 接続中';
    } else if (isLive) {
      mt5Badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-900/30 text-yellow-400 border border-yellow-800/50';
      mt5Badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-yellow-500"></span>MT5 未接続';
    } else {
      mt5Badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-gray-700 text-gray-400';
      mt5Badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-gray-500"></span>MT5 未接続';
    }

    // 設定モーダル内MT5ボタン
    const btnBase = 'px-4 py-2 rounded text-sm font-semibold transition-all';
    const btnDisabled = btnBase + ' bg-gray-700 text-gray-500 cursor-not-allowed';
    if (mt5Btn) {
      if (this.tcBusy) {
        mt5Btn.disabled = true;
        mt5Btn.textContent = '処理中...';
        mt5Btn.className = btnBase + ' bg-gray-700 text-gray-500 cursor-not-allowed';
      } else if (isConnected) {
        mt5Btn.disabled = false;
        mt5Btn.textContent = 'MT5 切断';
        mt5Btn.className = btnBase + ' bg-gray-600 text-gray-200 hover:bg-gray-500';
      } else {
        mt5Btn.disabled = false;
        mt5Btn.textContent = 'MT5 接続';
        mt5Btn.className = btnBase + ' bg-blue-600 text-white hover:bg-blue-700';
      }
    }

    // 自動トレードボタン
    if (autoBtn) {
      if (this.tcBusy) {
        autoBtn.disabled = true;
        autoBtn.textContent = '処理中...';
        autoBtn.className = btnDisabled;
      } else if (!isConnected) {
        autoBtn.disabled = true;
        autoBtn.textContent = '自動トレード開始';
        autoBtn.className = btnDisabled;
      } else if (isAutoOn) {
        autoBtn.disabled = false;
        autoBtn.textContent = this.symbol + ' 自動トレード停止';
        autoBtn.className = btnBase + ' bg-red-600 hover:bg-red-700 text-white shadow-lg shadow-red-600/20 active:scale-[0.98]';
      } else {
        autoBtn.disabled = false;
        autoBtn.textContent = this.symbol + ' 自動トレード開始';
        autoBtn.className = btnBase + ' bg-green-600 hover:bg-green-700 text-white shadow-lg shadow-green-600/20 active:scale-[0.98]';
      }
    }

    // デモモードボタン
    const demoBtn = document.getElementById('tc-demo-mode-btn');
    if (demoBtn) {
      if (this.tcBusy) {
        demoBtn.disabled = true;
        demoBtn.textContent = '処理中...';
        demoBtn.className = btnDisabled;
      } else if (!isLive) {
        demoBtn.disabled = true;
        demoBtn.textContent = 'デモモード';
        demoBtn.className = btnDisabled;
      } else if (isDemoOn) {
        demoBtn.disabled = false;
        demoBtn.textContent = this.symbol + ' デモ: ON';
        demoBtn.className = btnBase + ' bg-orange-500/20 text-orange-400 border border-orange-600/50 hover:bg-orange-500/30 active:scale-[0.98]';
      } else {
        demoBtn.disabled = false;
        demoBtn.textContent = this.symbol + ' デモ: OFF';
        demoBtn.className = btnBase + ' bg-gray-700 text-gray-300 hover:bg-gray-600 active:scale-[0.98]';
      }
    }

    // ヘッダー口座名表示
    this.updateHeaderAccountName();

    // ヘッダー通貨ペアステータスチップ更新
    // スタンバイ / デモ / リアル の3状態で表示
    const pairStrip = document.getElementById('header-pair-strip');
    if (pairStrip) {
      const pairs = ['USDJPY', 'EURUSD', 'GBPUSD', 'AUDUSD', 'EURJPY'];
      pairStrip.innerHTML = pairs.map((pair) => {
        const pairAutoOn = Object.prototype.hasOwnProperty.call(symbolAutoStates, pair)
          ? symbolAutoStates[pair] : false;
        const pairDemoOn = Object.prototype.hasOwnProperty.call(symbolDemoStates, pair)
          ? symbolDemoStates[pair] : false;
        const isReal = isConnected && pairAutoOn && !pairDemoOn;
        const isDemo = isConnected && pairAutoOn && pairDemoOn;
        const dot = isReal
          ? '<span class="w-1 h-1 rounded-full bg-green-400 animate-pulse flex-shrink-0"></span>'
          : isDemo
            ? '<span class="w-1 h-1 rounded-full bg-orange-400 animate-pulse flex-shrink-0"></span>'
            : '<span class="w-1 h-1 rounded-full bg-gray-600 flex-shrink-0"></span>';
        const label = isReal ? 'リアル' : isDemo ? 'デモ' : 'スタンバイ';
        const chipCls = isReal
          ? 'border-green-800/50 bg-green-900/20 hover:bg-green-900/35 cursor-pointer'
          : isDemo
            ? 'border-orange-800/50 bg-orange-900/20 hover:bg-orange-900/35 cursor-pointer'
            : 'border-gray-700/50 bg-gray-800/60 cursor-default';
        const labelCls = isReal ? 'text-green-400' : isDemo ? 'text-orange-400' : 'text-gray-500';
        const clickAttr = isConnected
          ? `onclick="DashboardApp.handleSymbolAutoTradeToggle('${pair}')"`
          : '';
        return `<div ${clickAttr} class="flex items-center gap-1 px-1.5 py-0.5 rounded border ${chipCls} transition-colors select-none">
          <span class="text-[10px] font-semibold text-gray-300 tabular-nums">${pair}</span>
          ${dot}
          <span class="text-[9px] ${labelCls}">${label}</span>
        </div>`;
      }).join('');
    }
  },

  /** シンボルごとの自動トレードON/OFFトグル */
  async handleSymbolAutoTradeToggle(symbol) {
    if (this.tcBusy) return;
    const m = this.tradingMode;
    const symbolStates = (m && m.symbol_auto_trade) || {};
    const currentOn = Object.prototype.hasOwnProperty.call(symbolStates, symbol)
      ? symbolStates[symbol]
      : false;
    const nextOn = !currentOn;

    this.tcBusy = true;
    try {
      this.renderTradingControl();
      const result = await toggleSymbolAutoTrade(symbol, nextOn);
      if (result) this.tradingMode = result;
    } catch (e) {
      console.error(symbol + ' 自動トレード切替エラー:', e);
    } finally {
      this.tcBusy = false;
      this.renderTradingControl();
    }
  },

  /** ヘッダー口座名を更新 */
  updateHeaderAccountName() {
    const el = document.getElementById('header-account-name');
    if (!el) return;

    const m = this.tradingMode;
    const isConnected = m && m.connected;
    const acct = isConnected && this.dashboard && this.dashboard.account
      ? this.dashboard.account
      : null;

    if (!acct) {
      el.classList.add('hidden');
      return;
    }

    const nick = (typeof SettingsManager !== 'undefined')
      ? SettingsManager.getNickname(acct.login)
      : '';
    el.textContent = nick || '#' + acct.login;
    el.classList.remove('hidden');
  },

  /** ポジション＆トレードのみ即時取得（WebSocket通知時） */
  async fetchPositionsAndTrades() {
    try {
      const [pos, tr] = await Promise.all([
        getPositions(this.symbol),
        getTrades(this.symbol, 20),
      ]);
      this.positions = pos;
      this.trades = tr;
    } catch (e) {
      // エラー時は既存データ維持
    }
    this.renderPositions();
    this.renderTradeHistory();
  },

  /** 全データ取得 */
  async fetchAll() {
    try {
      const [dash, pos, tr, summary] = await Promise.all([
        getDashboard(),
        getPositions(this.symbol),
        getTrades(this.symbol, 20),
        getTradeSummary(this.symbol, 30),
      ]);
      this.dashboard = dash;
      this.positions = pos;
      this.trades = tr;
      this.tradeSummary = summary;
    } catch (e) {
      // エラー時は既存データ維持
    } finally {
      this.isLoading = false;
      this.renderMetrics();
      this.renderPositions();
      this.renderTradeHistory();
    }
  },

  /** シグナル取得 */
  async fetchSignals() {
    try {
      this.currentSignals = await getCurrentSignals(this.symbol);
      ChartManager.setSignals(this.currentSignals);
    } catch (e) {
      this.currentSignals = [];
    }
    this.renderSignals();
  },

  /** 指標取得 */
  async fetchIndicators() {
    const tf = ChartManager.timeframe || 'M15';
    document.getElementById('indicator-timeframe').textContent = tf;
    try {
      this.indicators = await getIndicators(this.symbol, tf);
    } catch (e) {
      this.indicators = null;
    }
    this.renderIndicators();
  },

  // ── 描画メソッド ──

  /** メトリクスストリップ */
  renderMetrics() {
    const el = document.getElementById('metrics-strip');
    if (!el) return;

    if (!this.dashboard) {
      el.innerHTML = '<div class="card"><p class="text-gray-400 text-sm">口座データ取得中...</p></div>';
      return;
    }

    const d = this.dashboard;
    const a = d.account;

    el.innerHTML = `
      ${this.metricCard('残高', this.fmtCurrency(a.balance), '有効証拠金: ' + this.fmtCurrency(a.equity), 'neutral')}
      ${this.metricCard('本日損益',
        (d.daily_pnl >= 0 ? '+' : '') + this.fmtCurrency(d.daily_pnl),
        (d.daily_pnl_pct >= 0 ? '+' : '') + d.daily_pnl_pct.toFixed(2) + '%',
        d.daily_pnl >= 0 ? 'profit' : 'loss')}
      ${this.metricCard('勝率',
        d.win_rate.toFixed(1) + '%',
        '本日 ' + d.today_trades + ' トレード',
        d.win_rate >= 55 ? 'profit' : d.win_rate >= 45 ? 'neutral' : 'loss')}
      ${this.metricCard('ポジション',
        this.positions.length + ' / ' + d.open_positions,
        a.profit !== 0 ? '含み: ' + this.fmtCurrency(a.profit) : '含みなし',
        a.profit > 0 ? 'profit' : a.profit < 0 ? 'loss' : 'neutral',
        '実行中 / 総数')}
      ${this.metricCard('証拠金維持率',
        a.margin_level.toFixed(0) + '%',
        '余剰: ' + this.fmtCurrency(a.free_margin),
        a.margin_level > 300 ? 'profit' : a.margin_level > 150 ? 'neutral' : 'loss')}
    `;
  },

  metricCard(label, value, sub, variant, hint) {
    const valueColors = { profit: 'text-green-400', loss: 'text-red-400', neutral: 'text-gray-100' };
    const borderColors = { profit: 'border-l-green-500/50', loss: 'border-l-red-500/50', neutral: 'border-l-gray-600' };
    return `
      <div class="card border-l-2 ${borderColors[variant]} py-3">
        <p class="text-xs text-gray-400 uppercase tracking-wider mb-1">${label}</p>
        <p class="text-lg font-bold tabular-nums ${valueColors[variant]}">${value}</p>
        ${hint ? `<p class="text-[10px] text-gray-600 tabular-nums">${hint}</p>` : ''}
        ${sub ? `<p class="text-xs text-gray-500 mt-0.5 tabular-nums">${sub}</p>` : ''}
      </div>`;
  },

  /** シグナル描画 */
  renderSignals() {
    const listEl = document.getElementById('signal-list');
    const countEl = document.getElementById('signal-count');
    if (!listEl) return;

    // 最大3件に制限
    const signals = this.currentSignals.slice(0, 3);
    if (countEl) countEl.textContent = signals.length + ' 件';

    if (signals.length === 0) {
      listEl.innerHTML = '<div class="flex items-center justify-center h-12 text-gray-500 text-sm">シグナル待機中...</div>';
      return;
    }

    let html = '<div class="space-y-1">';
    signals.forEach((s) => { html += this.signalCard(s); });
    html += '</div>';
    listEl.innerHTML = html;
  },

  signalCard(s) {
    const borderColors = { BUY: 'border-l-green-500', SELL: 'border-l-red-500', HOLD: 'border-l-gray-600' };
    const bgColors = { BUY: 'bg-green-950/30', SELL: 'bg-red-950/30', HOLD: 'bg-gray-800/50' };
    const dirColors = { BUY: 'text-green-400', SELL: 'text-red-400', HOLD: 'text-gray-500' };
    const confColors = { HIGH: 'bg-green-900/40 text-green-400 border-green-700/50', MEDIUM: 'bg-yellow-900/40 text-yellow-400 border-yellow-700/50', LOW: 'bg-red-900/40 text-red-400 border-red-700/50' };
    const barColor = s.confidence >= 0.7 ? 'bg-green-500' : s.confidence >= 0.4 ? 'bg-yellow-500' : 'bg-red-500';

    const slTp = [
      s.stop_loss !== null ? `<span class="text-red-400/70">SL ${s.stop_loss.toFixed(3)}</span>` : '',
      s.take_profit !== null ? `<span class="text-green-400/70">TP ${s.take_profit.toFixed(3)}</span>` : '',
    ].filter(Boolean).join(' ');

    return `
      <div class="border-l-2 ${borderColors[s.signal_type]} ${bgColors[s.signal_type]} rounded-r px-2 py-1.5">
        <div class="flex items-center gap-2">
          <span class="text-xs font-bold ${dirColors[s.signal_type]}">${s.signal_type}</span>
          <span class="text-[10px] text-gray-500">${s.timeframe}</span>
          <div class="flex-1 h-1 bg-gray-700 rounded-full overflow-hidden mx-1">
            <div class="h-full rounded-full ${barColor}" style="width:${s.confidence * 100}%"></div>
          </div>
          <span class="inline-flex items-center px-1.5 py-0 rounded text-[10px] font-medium border ${confColors[s.confidence_level] || ''}">${(s.confidence * 100).toFixed(0)}%</span>
          <span class="text-[10px] text-gray-600 tabular-nums">${this.fmtTime(s.created_at)}</span>
        </div>
        ${s.reasoning ? `<p class="text-[10px] text-gray-500 mt-0.5 truncate">${this.escapeHtml(s.reasoning)}</p>` : ''}
        ${slTp ? `<div class="flex items-center gap-2 text-[10px] tabular-nums mt-0.5">${slTp}</div>` : ''}
      </div>`;
  },

  /** ポジション描画 */
  renderPositions() {
    const listEl = document.getElementById('position-list');
    const countEl = document.getElementById('position-count');
    if (!listEl) return;

    if (countEl) countEl.textContent = this.positions.length > 0 ? this.positions.length + ' open' : 'no open';

    if (this.positions.length === 0) {
      listEl.innerHTML = '<div class="flex items-center justify-center h-16 text-gray-500 text-sm">ポジションなし</div>';
      return;
    }

    listEl.innerHTML = '<div class="space-y-2">' + this.positions.map((p) => this.positionCard(p)).join('') + '</div>';
  },

  positionCard(p) {
    const isProfit = p.unrealized_pnl >= 0;
    const borderColor = p.signal_type === 'BUY' ? 'border-l-green-500' : 'border-l-red-500';
    const dirColor = p.signal_type === 'BUY' ? 'text-green-400' : 'text-red-400';
    const pnlColor = isProfit ? 'text-green-400' : 'text-red-400';

    let progressHtml = '';
    if (p.stop_loss !== null && p.take_profit !== null) {
      const sl = p.stop_loss, tp = p.take_profit;
      const range = Math.abs(tp - sl);
      if (range > 0) {
        const currentPct = Math.max(0, Math.min(100, ((p.current_price - Math.min(sl, tp)) / range) * 100));
        const isBuy = p.signal_type === 'BUY';
        const displayPct = isBuy ? currentPct : 100 - currentPct;
        progressHtml = `
          <div class="mb-2">
            <div class="w-full h-1.5 bg-gray-700 rounded-full relative overflow-hidden">
              <div class="absolute top-0 left-0 h-full bg-red-500/30 rounded-l-full" style="width:${isBuy ? 30 : 70}%"></div>
              <div class="absolute top-0 right-0 h-full bg-green-500/30 rounded-r-full" style="width:${isBuy ? 30 : 30}%"></div>
              <div class="absolute top-0 w-0.5 h-full ${pnlColor.replace('text-', 'bg-')}" style="left:${displayPct}%"></div>
            </div>
            <div class="flex justify-between text-[10px] text-gray-600 mt-0.5">
              <span>SL ${p.stop_loss.toFixed(3)}</span>
              <span>TP ${p.take_profit.toFixed(3)}</span>
            </div>
          </div>`;
      }
    }

    return `
      <div class="border-l-2 ${borderColor} bg-gray-800/50 rounded-r-lg p-3">
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center gap-2">
            <span class="text-sm font-bold ${dirColor}">${p.signal_type}</span>
            <span class="text-xs text-gray-500">${p.symbol}</span>
            <span class="text-xs text-gray-600">${p.volume.toFixed(2)} lot</span>
          </div>
          <span class="text-sm font-bold tabular-nums ${pnlColor}">${isProfit ? '+' : ''}${this.fmtCurrency(p.unrealized_pnl)}</span>
        </div>
        <div class="flex items-center gap-4 text-xs tabular-nums mb-2">
          <span class="text-gray-400">Entry ${p.entry_price.toFixed(3)}</span>
          <span class="text-gray-300 font-medium">Now ${p.current_price.toFixed(3)}</span>
          <span class="${pnlColor}">${p.unrealized_pnl_pips >= 0 ? '+' : ''}${p.unrealized_pnl_pips.toFixed(1)} pips</span>
        </div>
        ${progressHtml}
        <div class="text-[10px] text-gray-600">${this.fmtHoldTime(p.opened_at)}</div>
      </div>`;
  },

  /** 指標描画 */
  renderIndicators() {
    const grid = document.getElementById('indicator-grid');
    if (!grid) return;

    if (!this.indicators) {
      grid.innerHTML = '<p class="text-gray-500 text-sm col-span-4">指標データなし</p>';
      return;
    }

    const ind = this.indicators;
    grid.innerHTML = `
      ${this.gaugeIndicator('RSI', ind.rsi, 0, 100, [{from:0,to:30,c:'green'},{from:30,to:70,c:'neutral'},{from:70,to:100,c:'red'}])}
      ${this.gaugeIndicator('ADX', ind.adx, 0, 60, [{from:0,to:20,c:'red'},{from:20,to:40,c:'neutral'},{from:40,to:60,c:'green'}])}
      ${this.valueIndicator('MACD', ind.macd, 'Signal', ind.macd_signal, ind.macd !== null && ind.macd > 0 ? 'green' : 'red')}
      ${this.valueIndicator('MACD Hist', ind.macd_hist, null, null, ind.macd_hist !== null && ind.macd_hist > 0 ? 'green' : 'red')}
      ${this.dualIndicator('+DI / -DI', ind.plus_di, ind.minus_di, '+DI', '-DI', 'green', 'red')}
      ${this.bollingerIndicator(ind.bb_upper, ind.bb_middle, ind.bb_lower)}
      ${this.valueIndicator('ATR', ind.atr, null, null, 'neutral', (v) => v.toFixed(4))}
      ${this.dualIndicator('EMA', ind.ema_fast, ind.ema_slow, 'Fast', 'Slow',
        ind.ema_fast !== null && ind.ema_slow !== null && ind.ema_fast > ind.ema_slow ? 'green' : 'red', 'neutral')}
    `;
  },

  gaugeIndicator(label, value, min, max, zones) {
    const pct = value !== null ? Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100)) : 0;
    const zone = value !== null ? zones.find((z) => value >= z.from && value < z.to) : null;
    const zc = zone ? zone.c : 'neutral';
    const colors = { green: 'text-green-400', red: 'text-red-400', neutral: 'text-gray-300' };
    const barColors = { green: 'bg-green-500', red: 'bg-red-500', neutral: 'bg-gray-500' };
    return `
      <div class="bg-gray-800/50 rounded-lg p-2.5">
        <div class="flex items-center justify-between mb-1">
          <span class="text-[10px] text-gray-500 uppercase">${label}</span>
          <span class="text-sm font-bold tabular-nums ${colors[zc]}">${value !== null ? value.toFixed(1) : '-'}</span>
        </div>
        <div class="w-full h-1 bg-gray-700 rounded-full overflow-hidden">
          <div class="h-full rounded-full transition-all duration-300 ${barColors[zc]}" style="width:${pct}%"></div>
        </div>
      </div>`;
  },

  valueIndicator(label, value, subLabel, subValue, color, fmt) {
    fmt = fmt || ((v) => v.toFixed(4));
    const colors = { green: 'text-green-400', red: 'text-red-400', neutral: 'text-gray-300' };
    const subHtml = subLabel ? `<p class="text-[10px] text-gray-500 tabular-nums">${subLabel}: ${subValue !== null && subValue !== undefined ? fmt(subValue) : '-'}</p>` : '';
    return `
      <div class="bg-gray-800/50 rounded-lg p-2.5">
        <span class="text-[10px] text-gray-500 uppercase">${label}</span>
        <p class="text-sm font-bold tabular-nums ${colors[color]}">${value !== null ? fmt(value) : '-'}</p>
        ${subHtml}
      </div>`;
  },

  dualIndicator(label, v1, v2, l1, l2, c1, c2) {
    const colors = { green: 'text-green-400', red: 'text-red-400', neutral: 'text-gray-300' };
    return `
      <div class="bg-gray-800/50 rounded-lg p-2.5">
        <span class="text-[10px] text-gray-500 uppercase">${label}</span>
        <div class="flex items-baseline gap-2 mt-0.5">
          <span class="text-xs font-bold tabular-nums ${colors[c1]}">${l1} ${v1 !== null ? v1.toFixed(1) : '-'}</span>
          <span class="text-xs tabular-nums ${colors[c2]}">${l2} ${v2 !== null ? v2.toFixed(1) : '-'}</span>
        </div>
      </div>`;
  },

  bollingerIndicator(upper, middle, lower) {
    const bbw = upper !== null && lower !== null && middle !== null && middle !== 0
      ? ((upper - lower) / middle * 100).toFixed(3) : null;
    return `
      <div class="bg-gray-800/50 rounded-lg p-2.5">
        <div class="flex items-center justify-between">
          <span class="text-[10px] text-gray-500 uppercase">BB</span>
          ${bbw ? `<span class="text-[10px] text-gray-500">BBW: ${bbw}%</span>` : ''}
        </div>
        <div class="text-xs tabular-nums mt-0.5 space-y-0">
          <div class="flex justify-between"><span class="text-gray-500">U</span><span class="text-gray-400">${upper !== null ? upper.toFixed(3) : '-'}</span></div>
          <div class="flex justify-between"><span class="text-gray-500">M</span><span class="text-gray-300 font-medium">${middle !== null ? middle.toFixed(3) : '-'}</span></div>
          <div class="flex justify-between"><span class="text-gray-500">L</span><span class="text-gray-400">${lower !== null ? lower.toFixed(3) : '-'}</span></div>
        </div>
      </div>`;
  },

  /** トレード履歴 */
  renderTradeHistory() {
    const chipsEl = document.getElementById('trade-summary-chips');
    const tableEl = document.getElementById('trade-history-table');
    if (!chipsEl || !tableEl) return;

    // サマリーチップ
    if (this.tradeSummary) {
      const s = this.tradeSummary;
      const wrColor = s.win_rate >= 50 ? 'text-green-400' : 'text-red-400';
      const pfColor = s.profit_factor >= 1 ? 'text-green-400' : 'text-red-400';
      const netColor = s.net_profit >= 0 ? 'text-green-400' : 'text-red-400';
      chipsEl.innerHTML = `
        <span class="text-gray-500">WR: <span class="${wrColor}">${s.win_rate.toFixed(1)}%</span></span>
        <span class="text-gray-500">PF: <span class="${pfColor}">${s.profit_factor.toFixed(2)}</span></span>
        <span class="text-gray-500">Net: <span class="${netColor}">${this.fmtCurrency(s.net_profit)}</span></span>
        <span class="text-gray-500">${s.total_trades} trades (${s.winning_trades}W/${s.losing_trades}L)</span>
      `;
    }

    // テーブル
    if (this.trades.length === 0) {
      tableEl.innerHTML = '<div class="flex items-center justify-center h-16 text-gray-500 text-sm">トレードなし</div>';
      return;
    }

    const exitReasonConfig = {
      STOP_LOSS: { l: 'SL', c: 'bg-red-900/40 text-red-400 border-red-800/50' },
      TAKE_PROFIT: { l: 'TP', c: 'bg-green-900/40 text-green-400 border-green-800/50' },
      TRAILING_STOP: { l: 'TSL', c: 'bg-cyan-900/40 text-cyan-400 border-cyan-800/50' },
      TIME_EXIT: { l: 'TIME', c: 'bg-gray-700 text-gray-400 border-gray-600' },
      MANUAL: { l: 'MAN', c: 'bg-gray-700 text-gray-400 border-gray-600' },
      SIGNAL_REVERSAL: { l: 'REV', c: 'bg-yellow-900/40 text-yellow-400 border-yellow-800/50' },
      FORCE_CLOSE: { l: 'FORCE', c: 'bg-orange-900/40 text-orange-400 border-orange-800/50' },
      STAGNATION: { l: 'STAG', c: 'bg-purple-900/40 text-purple-400 border-purple-800/50' },
      TP_EARLY: { l: 'TP_E', c: 'bg-emerald-900/40 text-emerald-400 border-emerald-800/50' },
      INSURANCE: { l: 'INS', c: 'bg-blue-900/40 text-blue-400 border-blue-800/50' },
    };

    const rows = this.trades.map((t) => {
      const pnl = t.profit_loss || 0;
      const isProfit = pnl >= 0;
      const dirColor = t.signal_type === 'BUY' ? 'text-green-400' : 'text-red-400';
      const pnlColor = isProfit ? 'text-green-400' : 'text-red-400';
      const reason = t.exit_reason && exitReasonConfig[t.exit_reason];
      const reasonHtml = reason
        ? `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border ${reason.c}">${reason.l}</span>`
        : '';
      const pipsHtml = t.profit_loss_pips !== null
        ? `<span class="text-gray-500 ml-1">(${t.profit_loss_pips.toFixed(1)}p)</span>` : '';

      return `<tr>
        <td class="text-xs text-gray-400 whitespace-nowrap tabular-nums">${this.fmtDateTime(t.closed_at || t.opened_at)}</td>
        <td><span class="text-xs font-bold ${dirColor}">${t.signal_type}</span></td>
        <td class="text-xs text-gray-400 tabular-nums">${t.volume.toFixed(2)}</td>
        <td class="text-xs tabular-nums">${t.entry_price.toFixed(3)}</td>
        <td class="text-xs tabular-nums">${t.exit_price !== null ? t.exit_price.toFixed(3) : '-'}</td>
        <td>${reasonHtml}</td>
        <td class="text-right text-xs font-medium tabular-nums ${pnlColor}">${isProfit ? '+' : ''}${this.fmtCurrency(pnl)}${pipsHtml}</td>
      </tr>`;
    }).join('');

    tableEl.innerHTML = `
      <table class="table">
        <thead class="sticky top-0 bg-gray-800 z-10">
          <tr><th>日時</th><th>方向</th><th>Lot</th><th>Entry</th><th>Exit</th><th>理由</th><th class="text-right">損益</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  },

  // ── ユーティリティ ──

  fmtCurrency(v) {
    const currency = (this.dashboard && this.dashboard.account && this.dashboard.account.currency) || 'JPY';
    const digits = currency === 'JPY' ? 0 : 2;
    return new Intl.NumberFormat('ja-JP', { style: 'currency', currency: currency, maximumFractionDigits: digits }).format(v);
  },
  fmtTime(dateStr) {
    return new Date(dateStr).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
  },
  fmtDateTime(dateStr) {
    return new Date(dateStr).toLocaleDateString('ja-JP', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  },
  fmtHoldTime(openedAt) {
    const diffMs = Date.now() - new Date(openedAt).getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 60) return diffMin + 'm';
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return diffHour + 'h ' + (diffMin % 60) + 'm';
    return Math.floor(diffHour / 24) + 'd ' + (diffHour % 24) + 'h';
  },
  escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
  },
};

// ページ読み込み時に初期化
document.addEventListener('DOMContentLoaded', () => {
  DashboardApp.init();
});
