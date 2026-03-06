/** ダッシュボードロジック - コンポーネントベース */

// ── ユーティリティ関数 ──

function fmtCurrency(v, currency) {
  currency = currency || 'JPY';
  const digits = currency === 'JPY' ? 0 : 2;
  return new Intl.NumberFormat('ja-JP', { style: 'currency', currency, maximumFractionDigits: digits }).format(v);
}

function fmtTime(dateStr) {
  return new Date(dateStr).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Tokyo' });
}

function fmtDateTime(dateStr) {
  return new Date(dateStr).toLocaleDateString('ja-JP', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Tokyo' });
}

function fmtHoldTime(openedAt, elapsedMin) {
  const diffMin = elapsedMin != null
    ? elapsedMin
    : Math.max(0, Math.floor((Date.now() - new Date(openedAt).getTime()) / 60000));
  if (diffMin < 60) return diffMin + 'm';
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return diffHour + 'h ' + (diffMin % 60) + 'm';
  return Math.floor(diffHour / 24) + 'd ' + (diffHour % 24) + 'h';
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

// ── DataFlowManager (pub/sub + store) ──

class DataFlowManager {
  constructor() {
    this._subs = {};
    this._store = {};
    this._nextId = 0;
  }

  subscribe(channel, handler) {
    if (!this._subs[channel]) this._subs[channel] = [];
    const id = ++this._nextId;
    this._subs[channel].push({ handler, id });
    return () => {
      const arr = this._subs[channel];
      if (arr) this._subs[channel] = arr.filter(s => s.id !== id);
    };
  }

  publish(channel, data) {
    this._store[channel] = data;
    const subs = this._subs[channel];
    if (subs) subs.forEach(s => s.handler(data));
  }

  get(channel) {
    return this._store[channel];
  }
}

// ── Component 基底クラス ──

class Component {
  constructor(rootId, dataFlow) {
    this._rootId = rootId;
    this._dataFlow = dataFlow;
    this._unsubs = [];
  }

  get root() {
    return document.getElementById(this._rootId);
  }

  subscribe(channel, handler) {
    this._unsubs.push(this._dataFlow.subscribe(channel, handler.bind(this)));
  }

  _getCurrency() {
    const d = this._dataFlow.get('dashboard');
    return (d && d.account && d.account.currency) || 'JPY';
  }

  mount() { /* override */ }

  destroy() {
    this._unsubs.forEach(fn => fn());
    this._unsubs = [];
  }
}

// ── MetricsStrip ──

class MetricsStrip extends Component {
  constructor(dataFlow) {
    super('metrics-strip', dataFlow);
    this._initialized = false;
  }

  mount() {
    this.subscribe('dashboard', () => this._updateDashboardCards());
    this.subscribe('positions', () => this._updatePositionCard());
    this.subscribe('trades', () => this._updateStatsCard());
  }

  _getCards() {
    const d = this._dataFlow.get('dashboard');
    if (!d) return null;
    const a = d.account;
    const c = this._getCurrency();
    const wp = d.weekly_pnl || 0;
    const mp = d.monthly_pnl || 0;
    const tp = d.total_pnl || 0;
    const tt = d.total_trades || 0;
    return [
      { id: 'mc-balance', label: '残高', value: fmtCurrency(a.balance, c),
        sub: '有効証拠金: ' + fmtCurrency(a.equity, c), variant: 'neutral' },
      { id: 'mc-daily', label: '本日損益',
        value: (d.daily_pnl >= 0 ? '+' : '') + fmtCurrency(d.daily_pnl, c),
        sub: (d.daily_pnl_pct >= 0 ? '+' : '') + d.daily_pnl_pct.toFixed(2) + '%',
        variant: d.daily_pnl >= 0 ? 'profit' : 'loss' },
      { id: 'mc-weekly', label: '週間/月間',
        value: (wp >= 0 ? '+' : '') + fmtCurrency(wp, c),
        sub: '月間: ' + (mp >= 0 ? '+' : '') + fmtCurrency(mp, c),
        variant: wp >= 0 ? 'profit' : 'loss' },
      { id: 'mc-total', label: '全履歴損益',
        value: (tp >= 0 ? '+' : '') + fmtCurrency(tp, c),
        sub: tt.toLocaleString() + ' トレード',
        variant: tp >= 0 ? 'profit' : 'loss' },
      { id: 'mc-winrate', label: '勝率',
        value: d.win_rate.toFixed(1) + '%',
        sub: '本日 ' + d.today_trades + ' トレード',
        variant: d.win_rate >= 55 ? 'profit' : d.win_rate >= 45 ? 'neutral' : 'loss' },
      { id: 'mc-margin', label: '証拠金維持率',
        value: a.margin_level.toFixed(0) + '%',
        sub: '余剰: ' + fmtCurrency(a.free_margin, c),
        variant: a.margin_level > 300 ? 'profit' : a.margin_level > 150 ? 'neutral' : 'loss' },
    ];
  }

  _metricCardHtml(id, label, value, sub, variant) {
    const valueColors = { profit: 'text-green-400', loss: 'text-red-400', neutral: 'text-gray-100' };
    const borderColors = { profit: 'border-l-green-500/50', loss: 'border-l-red-500/50', neutral: 'border-l-gray-600' };
    return `
      <div class="card border-l-2 ${borderColors[variant]}" data-metric="${id}">
        <p class="text-[10px] text-gray-400 uppercase tracking-wider mb-0.5">${label}</p>
        <p class="text-sm font-bold tabular-nums leading-tight ${valueColors[variant]}" data-val>${value}</p>
        <p class="text-[10px] text-gray-500 mt-0.5 tabular-nums" data-sub>${sub}</p>
      </div>`;
  }

  _positionMetricCardHtml() {
    const positions = this._dataFlow.get('positions') || [];
    const totalCount = positions.length;
    const totalProfit = positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
    const variant = totalCount === 0 ? 'neutral' : (totalProfit >= 0 ? 'profit' : 'loss');
    const borderColors = { profit: 'border-l-green-500/50', loss: 'border-l-red-500/50', neutral: 'border-l-gray-600' };
    const valueColors = { profit: 'text-green-400', loss: 'text-red-400', neutral: 'text-gray-100' };
    const c = this._getCurrency();
    const profitStr = totalCount > 0
      ? `${totalProfit >= 0 ? '+' : ''}${fmtCurrency(totalProfit, c)}`
      : '';
    const symbolCount = new Set(positions.map(p => p.symbol)).size;
    const summaryText = totalCount > 0 ? `${symbolCount}ペア` : '';
    return `
      <div class="card border-l-2 ${borderColors[variant]} relative" data-metric="mc-position">
        <p class="text-[10px] text-gray-400 uppercase tracking-wider mb-0.5">ポジション</p>
        <p class="text-sm font-bold tabular-nums leading-tight ${valueColors[variant]}" data-val>${totalCount} open</p>
        <p class="text-[10px] text-gray-500 mt-0.5 tabular-nums" data-sub>${profitStr}</p>
        <p class="text-[10px] text-gray-500 mt-0.5 tabular-nums cursor-pointer hover:text-gray-300 transition-colors" data-pos-summary>${summaryText}</p>
        <div data-pos-popover class="hidden absolute z-50 left-0 top-full mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-xl p-2 min-w-[180px]"></div>
      </div>`;
  }

  _buildPopoverContent(positions) {
    if (positions.length === 0) return '';
    const c = this._getCurrency();
    const bySymbol = {};
    for (const p of positions) {
      if (!bySymbol[p.symbol]) bySymbol[p.symbol] = { pnl: 0, count: 0 };
      bySymbol[p.symbol].pnl += (p.unrealized_pnl || 0);
      bySymbol[p.symbol].count += 1;
    }
    return Object.entries(bySymbol).map(([sym, data]) => {
      const color = data.pnl > 0 ? 'text-green-400' : data.pnl < 0 ? 'text-red-400' : 'text-gray-400';
      const sign = data.pnl >= 0 ? '+' : '';
      return `<div class="flex items-center justify-between gap-3 py-0.5">
        <span class="text-[11px] font-medium text-gray-300">${sym} <span class="text-gray-500">×${data.count}</span></span>
        <span class="text-[11px] font-bold tabular-nums ${color}">${sign}${fmtCurrency(data.pnl, c)}</span>
      </div>`;
    }).join('');
  }

  /** dashboard チャネル更新 → 口座・損益カードのみ差分更新 */
  _updateDashboardCards() {
    const el = this.root;
    if (!el) return;
    const cards = this._getCards();
    if (!cards) {
      if (!this._initialized) {
        el.innerHTML = '<div class="card"><p class="text-gray-400 text-sm">口座データ取得中...</p></div>';
      }
      return;
    }
    if (!this._initialized) {
      const before = cards.slice(0, 5);
      const after = cards.slice(5);
      el.innerHTML = before.map(c => this._metricCardHtml(c.id, c.label, c.value, c.sub, c.variant)).join('')
        + this._positionMetricCardHtml()
        + after.map(c => this._metricCardHtml(c.id, c.label, c.value, c.sub, c.variant)).join('')
        + this._statsCardHtml();
      this._initialized = true;
      this._initPopover();
      return;
    }
    // 差分更新: textContent のみ（innerHTML 不使用で点滅防止）
    const valueColors = { profit: 'text-green-400', loss: 'text-red-400', neutral: 'text-gray-100' };
    const borderColors = { profit: 'border-l-green-500/50', loss: 'border-l-red-500/50', neutral: 'border-l-gray-600' };
    for (const c of cards) {
      const card = el.querySelector(`[data-metric="${c.id}"]`);
      if (!card) continue;
      const valEl = card.querySelector('[data-val]');
      const subEl = card.querySelector('[data-sub]');
      if (valEl) {
        valEl.textContent = c.value;
        valEl.className = `text-sm font-bold tabular-nums leading-tight ${valueColors[c.variant]}`;
      }
      if (subEl) subEl.textContent = c.sub;
      card.className = card.className
        .replace(/border-l-(green|red|gray)-[^\s]*/g, '')
        .replace(/\s+/g, ' ').trim()
        + ' ' + borderColors[c.variant];
    }
  }

  /** positions チャネル更新 → ポジションカードのみ差分更新 */
  _updatePositionCard() {
    const el = this.root;
    if (!el || !this._initialized) return;
    const card = el.querySelector('[data-metric="mc-position"]');
    if (!card) return;

    const positions = this._dataFlow.get('positions') || [];
    const totalCount = positions.length;
    const totalProfit = positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
    const variant = totalCount === 0 ? 'neutral' : (totalProfit >= 0 ? 'profit' : 'loss');
    const valueColors = { profit: 'text-green-400', loss: 'text-red-400', neutral: 'text-gray-100' };
    const borderColors = { profit: 'border-l-green-500/50', loss: 'border-l-red-500/50', neutral: 'border-l-gray-600' };
    const c = this._getCurrency();

    const valEl = card.querySelector('[data-val]');
    const subEl = card.querySelector('[data-sub]');
    const summaryEl = card.querySelector('[data-pos-summary]');
    const popoverEl = card.querySelector('[data-pos-popover]');
    if (valEl) {
      valEl.textContent = totalCount + ' open';
      valEl.className = `text-sm font-bold tabular-nums leading-tight ${valueColors[variant]}`;
    }
    if (subEl) {
      subEl.textContent = totalCount > 0
        ? `${totalProfit >= 0 ? '+' : ''}${fmtCurrency(totalProfit, c)}`
        : '';
    }
    if (summaryEl) {
      const symbolCount = new Set(positions.map(p => p.symbol)).size;
      summaryEl.textContent = totalCount > 0 ? `${symbolCount}ペア` : '';
    }
    // ポップオーバーが開いている場合は内容も更新
    if (popoverEl && !popoverEl.classList.contains('hidden')) {
      popoverEl.innerHTML = this._buildPopoverContent(positions);
    }
    card.className = card.className
      .replace(/border-l-(green|red|gray)-[^\s]*/g, '')
      .replace(/\s+/g, ' ').trim()
      + ' ' + borderColors[variant];
  }

  _statsCardHtml() {
    const td = this._dataFlow.get('trades');
    const s = td && td.summary;
    const c = this._getCurrency();
    const pf = s ? s.profit_factor.toFixed(2) : '--';
    const pfVal = s ? s.profit_factor : 0;
    const variant = pfVal >= 1.5 ? 'profit' : pfVal >= 1.0 ? 'neutral' : 'loss';
    const valueColors = { profit: 'text-green-400', loss: 'text-red-400', neutral: 'text-gray-100' };
    const borderColors = { profit: 'border-l-green-500/50', loss: 'border-l-red-500/50', neutral: 'border-l-gray-600' };
    const sub = this._statsSubText(s, c);
    return `
      <div class="card border-l-2 ${borderColors[variant]}" data-metric="mc-stats">
        <p class="text-[10px] text-gray-400 uppercase tracking-wider mb-0.5">PF (統計)</p>
        <p class="text-sm font-bold tabular-nums leading-tight ${valueColors[variant]}" data-val>${pf}</p>
        <p class="text-[10px] text-gray-500 mt-0.5 tabular-nums truncate" data-sub>${sub}</p>
      </div>`;
  }

  _statsSubText(s, c) {
    if (!s) return '--';
    const np = `${s.net_profit >= 0 ? '+' : ''}${fmtCurrency(s.net_profit, c)}`;
    const aw = fmtCurrency(s.average_win, c);
    const al = fmtCurrency(s.average_loss, c);
    const dd = fmtCurrency(s.max_drawdown, c);
    return `${np} | ▲${aw} ▼${al} | DD${dd}`;
  }

  /** trades チャネル更新 → 統計カードのみ差分更新 */
  _updateStatsCard() {
    const el = this.root;
    if (!el || !this._initialized) return;
    const card = el.querySelector('[data-metric="mc-stats"]');
    if (!card) return;
    const td = this._dataFlow.get('trades');
    const s = td && td.summary;
    if (!s) return;
    const c = this._getCurrency();
    const valEl = card.querySelector('[data-val]');
    const subEl = card.querySelector('[data-sub]');
    const pfVal = s.profit_factor;
    const variant = pfVal >= 1.5 ? 'profit' : pfVal >= 1.0 ? 'neutral' : 'loss';
    const valueColors = { profit: 'text-green-400', loss: 'text-red-400', neutral: 'text-gray-100' };
    const borderColors = { profit: 'border-l-green-500/50', loss: 'border-l-red-500/50', neutral: 'border-l-gray-600' };
    if (valEl) {
      valEl.textContent = pfVal.toFixed(2);
      valEl.className = `text-sm font-bold tabular-nums leading-tight ${valueColors[variant]}`;
    }
    if (subEl) subEl.textContent = this._statsSubText(s, c);
    card.className = card.className
      .replace(/border-l-(green|red|gray)-[^\s]*/g, '')
      .replace(/\s+/g, ' ').trim()
      + ' ' + borderColors[variant];
  }

  /** ポップオーバーのクリックイベントを設定 */
  _initPopover() {
    const el = this.root;
    if (!el) return;
    const card = el.querySelector('[data-metric="mc-position"]');
    if (!card) return;
    const summaryEl = card.querySelector('[data-pos-summary]');
    const popoverEl = card.querySelector('[data-pos-popover]');
    if (!summaryEl || !popoverEl) return;

    summaryEl.addEventListener('click', (e) => {
      e.stopPropagation();
      const positions = this._dataFlow.get('positions') || [];
      if (positions.length === 0) return;
      popoverEl.innerHTML = this._buildPopoverContent(positions);
      popoverEl.classList.toggle('hidden');
    });

    // ポップオーバー外クリックで閉じる
    document.addEventListener('click', (e) => {
      if (!card.contains(e.target)) {
        popoverEl.classList.add('hidden');
      }
    });
  }
}

// ── PositionPanel ──

class PositionPanel extends Component {
  constructor(dataFlow) {
    super('position-list', dataFlow);
    this._expandedPositions = new Set();
    this._posTimeCache = {};
    this._posTimeInterval = null;
    this._closeVolumes = {};
  }

  mount() {
    this.subscribe('positions', () => this._render());
    this._posTimeInterval = setInterval(() => this._tickPositionTimers(), 60000);
  }

  destroy() {
    super.destroy();
    if (this._posTimeInterval) {
      clearInterval(this._posTimeInterval);
      this._posTimeInterval = null;
    }
  }

  _render() {
    const listEl = this.root;
    const countEl = document.getElementById('position-count');
    if (!listEl) return;

    const positions = this._dataFlow.get('positions') || [];
    this._updatePosTimeCache(positions);

    if (countEl) countEl.textContent = positions.length > 0 ? positions.length + ' open' : 'no open';

    if (positions.length === 0) {
      listEl.innerHTML = '<div class="flex items-center justify-center h-16 text-gray-500 text-sm">ポジションなし</div>';
      return;
    }

    // 決済済みポジションの展開状態をクリーンアップ
    const activeTickets = new Set(positions.map(p => p.ticket));
    for (const t of this._expandedPositions) {
      if (!activeTickets.has(t)) this._expandedPositions.delete(t);
    }

    // 差分更新: ticketセットが同じならカード内部だけ更新
    const existingCards = listEl.querySelectorAll('[data-ticket]');
    const existingTickets = [...existingCards].map(el => Number(el.dataset.ticket));
    const newTickets = positions.map(p => p.ticket);
    const sameStructure =
      existingTickets.length === newTickets.length &&
      existingTickets.every((t, i) => t === newTickets[i]);

    if (sameStructure) {
      positions.forEach((p, i) => {
        const card = existingCards[i];
        if (!card) return;
        const inner = this._positionCardInner(p, i);
        card.setAttribute(
          'onclick',
          `DashboardApp.togglePositionDetail('pos-detail-${i}', 'pos-arrow-${i}', ${p.ticket})`
        );
        const posInner = card.querySelector('[data-pos-inner]');
        if (posInner) {
          posInner.innerHTML = inner;
        } else {
          card.innerHTML = inner;
        }
      });
    } else {
      listEl.innerHTML =
        '<div class="space-y-2">' +
        positions.map((p, i) => this._positionCard(p, i)).join('') +
        '</div>';
    }
  }

  _positionCard(p, idx) {
    const isBuy = p.signal_type === 'BUY';
    const borderColor = isBuy ? 'border-l-green-500' : 'border-l-red-500';
    const inner = this._positionCardInner(p, idx);
    const isExpanded = this._expandedPositions.has(p.ticket);
    const closeCls = isExpanded ? '' : 'hidden';
    return `
      <div class="border-l-2 ${borderColor} bg-gray-800/60 rounded-r-lg px-3 py-2 cursor-pointer select-none"
           data-ticket="${p.ticket}"
           onclick="DashboardApp.togglePositionDetail('pos-detail-${idx}', 'pos-arrow-${idx}', ${p.ticket})">
        <div data-pos-inner>${inner}</div>
        <div data-close-ui="${p.ticket}" class="${closeCls}">${this._closePositionHtml(p)}</div>
      </div>`;
  }

  _positionCardInner(p, idx) {
    const c = this._getCurrency();
    const isProfit = p.unrealized_pnl >= 0;
    const isBuy = p.signal_type === 'BUY';
    const dirBg = isBuy ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400';
    const pnlColor = isProfit ? 'text-green-400' : 'text-red-400';
    const pnlBg = isProfit ? 'bg-green-500/10' : 'bg-red-500/10';
    const pnlSign = isProfit ? '+' : '';
    const digits = p.entry_price > 20 ? 3 : 5;

    let priceRowHtml = '';
    let progressHtml = '';
    if (p.stop_loss != null && p.take_profit != null) {
      const sl = p.stop_loss, tp = p.take_profit;
      const entry = p.entry_price, now = p.current_price;
      const halfRange = Math.max(
        Math.abs(entry - sl), Math.abs(entry - tp),
        Math.abs(entry - now), 0.0001
      );
      const range = halfRange * 2;
      if (range > 0) {
        const toPct = (v) => Math.max(0, Math.min(100, 50 + (v - entry) / halfRange * 50));
        const entryPct = 50;
        const nowPct = toPct(now);
        const slPct = toPct(sl);
        const tpPct = toPct(tp);
        const nowBg = pnlColor.replace('text-', 'bg-');
        const leftZoneBg = isBuy ? 'bg-red-500/35' : 'bg-green-500/25';
        const rightZoneBg = isBuy ? 'bg-green-500/25' : 'bg-red-500/35';

        priceRowHtml = `
          <div class="grid grid-cols-4 text-center gap-1 mb-2">
            <div><div class="text-[9px] text-gray-600 mb-0.5">SL</div><div class="text-[10px] tabular-nums text-red-400">${sl.toFixed(digits)}</div></div>
            <div><div class="text-[9px] text-gray-600 mb-0.5">Entry</div><div class="text-[10px] tabular-nums text-gray-400">${entry.toFixed(digits)}</div></div>
            <div><div class="text-[9px] text-gray-600 mb-0.5">Now</div><div class="text-[10px] tabular-nums font-semibold ${pnlColor}">${now.toFixed(digits)}</div></div>
            <div><div class="text-[9px] text-gray-600 mb-0.5">TP</div><div class="text-[10px] tabular-nums text-green-400">${tp.toFixed(digits)}</div></div>
          </div>`;

        const slLeft = slPct <= 0 ? '0' : (slPct >= 100 ? 'calc(100% - 2px)' : `${slPct}%`);
        const tpLeft = tpPct <= 0 ? '0' : (tpPct >= 100 ? 'calc(100% - 2px)' : `${tpPct}%`);

        progressHtml = `
          <div class="mb-2">
            <div class="w-full h-3 relative">
              <div class="absolute inset-x-0 top-0.5 bottom-0.5 bg-gray-700 rounded-full overflow-hidden">
                <div class="absolute inset-y-0 left-0 ${leftZoneBg}" style="width:${entryPct}%"></div>
                <div class="absolute inset-y-0 right-0 ${rightZoneBg}" style="width:${100 - entryPct}%"></div>
                <div class="absolute inset-y-0 w-px bg-gray-300 opacity-50" style="left:${entryPct}%"></div>
              </div>
              <div class="absolute inset-y-0 w-0.5 bg-red-500" style="left:${slLeft}"></div>
              <div class="absolute inset-y-0 w-0.5 bg-green-500" style="left:${tpLeft}"></div>
              <div class="absolute -top-0.5 -bottom-0.5 w-1 ${nowBg} rounded-sm" style="left:${nowPct}%;transform:translateX(-50%)"></div>
              <div class="absolute w-2.5 h-2.5 ${nowBg} rounded-full border border-gray-900/70" style="left:${nowPct}%;top:50%;transform:translate(-50%,-50%)"></div>
            </div>
          </div>`;
      }
    } else {
      priceRowHtml = `
        <div class="flex items-center gap-2 text-[11px] tabular-nums mb-2">
          <span class="text-gray-500">Entry</span>
          <span class="text-gray-400">${p.entry_price.toFixed(digits)}</span>
          <span class="text-gray-600">&rarr;</span>
          <span class="font-semibold ${pnlColor}">${p.current_price.toFixed(digits)}</span>
        </div>`;
    }

    const detailId = `pos-detail-${idx}`;
    const arrowId = `pos-arrow-${idx}`;
    const isExpanded = this._expandedPositions.has(p.ticket);
    const detailCls = isExpanded ? '' : 'hidden';
    const arrowChar = isExpanded ? '&#x25BE;' : '&#x25B8;';

    return `
        <div class="flex items-center justify-between mb-1.5">
          <div class="flex items-center gap-1.5">
            <span class="text-[11px] font-bold px-1.5 py-0.5 rounded ${dirBg}">${p.signal_type}</span>
            <span class="text-sm font-semibold text-gray-200">${p.symbol}</span>
            <span class="text-xs font-mono tabular-nums ${pnlColor}">${p.current_price.toFixed(digits)}</span>
            <span class="text-xs text-gray-400">${p.volume.toFixed(2)}lot</span>
            <span class="text-xs text-gray-600">&middot;</span>
            <span class="text-xs text-gray-400" data-elapsed-ticket="${p.ticket}">${this._fmtElapsedTime(p)}</span>
            <span class="text-xs inline-block" data-remaining-ticket="${p.ticket}">${this._fmtRemainingTimeInner(p)}</span>
          </div>
          <div class="flex items-center gap-2">
            <div class="flex items-baseline gap-1 ${pnlBg} px-2 py-0.5 rounded-md">
              <span class="text-sm font-bold tabular-nums ${pnlColor}">${pnlSign}${fmtCurrency(p.unrealized_pnl, c)}</span>
              <span class="text-[10px] tabular-nums ${pnlColor} opacity-70">${pnlSign}${p.unrealized_pnl_pips.toFixed(1)}p</span>
            </div>
            <span id="${arrowId}" class="text-base text-gray-400">${arrowChar}</span>
          </div>
        </div>
        ${progressHtml}
        <div id="${detailId}" class="${detailCls}">
          ${priceRowHtml}
          ${p.trade_id ? `<div class="mt-1 text-[9px] text-gray-700 tabular-nums truncate">ID: ${p.trade_id}</div>` : ''}
        </div>`;
  }

  _closePositionHtml(p) {
    const vol = p.volume;
    const pct25 = Math.max(0.01, Math.round(vol * 0.25 * 100) / 100);
    const pct50 = Math.max(0.01, Math.round(vol * 0.50 * 100) / 100);
    const pct75 = Math.max(0.01, Math.round(vol * 0.75 * 100) / 100);
    const selCls = 'px-2 py-1 text-[10px] font-semibold rounded transition-colors cursor-pointer';
    const actCls = 'shrink-0 px-2 py-1 text-[10px] font-semibold rounded transition-colors';
    return `
      <div class="mt-2 pt-2 border-t border-gray-700/50" onclick="event.stopPropagation()">
        <div class="flex items-center gap-1.5">
          <button class="${selCls} bg-gray-600 hover:bg-amber-700/80 text-gray-200"
            onclick="DashboardApp._selectClosePct(${p.ticket}, ${pct25}, this)">25%</button>
          <button class="${selCls} bg-gray-600 hover:bg-amber-700/80 text-gray-200"
            onclick="DashboardApp._selectClosePct(${p.ticket}, ${pct50}, this)">50%</button>
          <button class="${selCls} bg-gray-600 hover:bg-amber-700/80 text-gray-200"
            onclick="DashboardApp._selectClosePct(${p.ticket}, ${pct75}, this)">75%</button>
          <div class="w-px h-4 bg-gray-600 shrink-0"></div>
          <button class="${actCls} bg-amber-600 hover:bg-amber-500 text-white opacity-50 pointer-events-none"
            data-close-exec="${p.ticket}"
            onclick="DashboardApp.closePosition(${p.ticket}, DashboardApp._closeVolumes[${p.ticket}])">部分決済</button>
          <button class="${actCls} bg-red-600 hover:bg-red-500 text-white"
            onclick="DashboardApp.closePosition(${p.ticket}, null)">全決済</button>
        </div>
      </div>`;
  }

  togglePositionDetail(detailId, arrowId, ticket) {
    const detail = document.getElementById(detailId);
    const arrow = document.getElementById(arrowId);
    if (!detail) return;
    const isHidden = detail.classList.toggle('hidden');
    if (arrow) arrow.textContent = isHidden ? '▸' : '▾';
    if (ticket != null) {
      const closeUi = document.querySelector(`[data-close-ui="${ticket}"]`);
      if (closeUi) {
        if (isHidden) closeUi.classList.add('hidden');
        else closeUi.classList.remove('hidden');
      }
      if (isHidden) this._expandedPositions.delete(ticket);
      else this._expandedPositions.add(ticket);
    }
  }

  _selectClosePct(ticket, volume, btn) {
    this._closeVolumes[ticket] = volume;
    const ui = btn.closest('[data-close-ui]');
    if (ui) {
      ui.querySelectorAll('button:not([data-close-exec])').forEach(b => {
        if (b.textContent.includes('%')) {
          b.className = b.className
            .replace(/bg-amber-700\/80/g, 'bg-gray-600')
            .replace(/text-amber-100/g, 'text-gray-200');
        }
      });
    }
    btn.className = btn.className
      .replace(/bg-gray-600/g, 'bg-amber-700/80')
      .replace(/text-gray-200/g, 'text-amber-100');
    const execBtn = document.querySelector(`[data-close-exec="${ticket}"]`);
    if (execBtn) {
      execBtn.classList.remove('opacity-50', 'pointer-events-none');
    }
  }

  async closePosition(ticket, volume) {
    const isPartial = volume != null;
    const closeUi = document.querySelector(`[data-close-ui="${ticket}"]`);
    const buttons = closeUi ? closeUi.querySelectorAll('button') : [];
    buttons.forEach(b => { b.disabled = true; b.classList.add('opacity-50'); });

    try {
      const params = new URLSearchParams();
      if (volume != null) params.set('volume', volume.toFixed(2));
      const url = `/api/v1/trading/positions/${ticket}/close?${params}`;
      const res = await fetch(url, { method: 'POST' });
      const json = await res.json();
      if (json.success) {
        const d = json.data;
        const msg = isPartial
          ? `部分決済完了: ${d.closed_volume}lot (残${d.remaining_volume}lot)`
          : `全決済完了: ${d.closed_volume}lot @ ${d.exit_price}`;
        this._showCloseToast(msg, 'success');
      } else {
        this._showCloseToast(`決済失敗: ${json.error || '不明なエラー'}`, 'error');
      }
    } catch (e) {
      this._showCloseToast(`通信エラー: ${e.message}`, 'error');
    } finally {
      buttons.forEach(b => { b.disabled = false; b.classList.remove('opacity-50'); });
    }
  }

  _showCloseToast(msg, type) {
    const existing = document.getElementById('close-toast');
    if (existing) existing.remove();
    const bg = type === 'success' ? 'bg-green-600' : 'bg-red-600';
    const toast = document.createElement('div');
    toast.id = 'close-toast';
    toast.className = `fixed bottom-4 right-4 ${bg} text-white text-sm px-4 py-2 rounded-lg shadow-lg z-50 transition-opacity`;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  // ── 時間計算 ──

  _updatePosTimeCache(positions) {
    const activeTickets = new Set();
    for (const p of positions) {
      const t = Number(p.ticket);
      activeTickets.add(t);
      const existing = this._posTimeCache[t];
      const openedAtMs = (existing && existing.openedAtMs)
        ? existing.openedAtMs
        : (p.opened_at ? new Date(p.opened_at).getTime() : null);
      const maxHoldMin = p.max_hold_minutes != null
        ? p.max_hold_minutes
        : (existing ? existing.maxHoldMin : null);
      this._posTimeCache[t] = { openedAtMs, maxHoldMin };
    }
    for (const ticket of Object.keys(this._posTimeCache)) {
      if (!activeTickets.has(Number(ticket))) delete this._posTimeCache[ticket];
    }
  }

  _calcElapsedMin(ticket) {
    const cache = this._posTimeCache[ticket];
    if (!cache || !cache.openedAtMs) return 0;
    return Math.max(0, Math.floor((Date.now() - cache.openedAtMs) / 60000));
  }

  _fmtElapsedTime(p) {
    if (p.opened_at || this._posTimeCache[p.ticket]) {
      return fmtHoldTime(null, this._calcElapsedMin(p.ticket));
    }
    if (p.elapsed_minutes != null) return fmtHoldTime(null, p.elapsed_minutes);
    return '0m';
  }

  _calcRemainingHtml(ticket) {
    const cache = this._posTimeCache[ticket];
    if (!cache || cache.maxHoldMin == null) return '';
    const elapsed = this._calcElapsedMin(ticket);
    const rem = Math.max(0, cache.maxHoldMin - elapsed);
    const ratio = cache.maxHoldMin > 0 ? rem / cache.maxHoldMin : 1;
    const cls = ratio <= 0.2 ? 'text-orange-400' : 'text-gray-500';
    let label;
    if (rem < 60) {
      label = `残${rem}m`;
    } else {
      const h = Math.floor(rem / 60);
      const m = rem % 60;
      label = m > 0 ? `残${h}h${m}m` : `残${h}h`;
    }
    return `<span class="${cls}">/ ${label}</span>`;
  }

  _fmtRemainingTimeInner(p) {
    return this._calcRemainingHtml(Number(p.ticket));
  }

  _tickPositionTimers() {
    const elapsedEls = document.querySelectorAll('[data-elapsed-ticket]');
    for (const el of elapsedEls) {
      const ticket = Number(el.dataset.elapsedTicket);
      const min = this._calcElapsedMin(ticket);
      el.textContent = fmtHoldTime(null, min);
    }
    const remainEls = document.querySelectorAll('[data-remaining-ticket]');
    for (const el of remainEls) {
      const ticket = Number(el.dataset.remainingTicket);
      el.innerHTML = this._calcRemainingHtml(ticket);
    }
  }
}

// ── TradeHistory ──

class TradeHistory extends Component {
  constructor(dataFlow) {
    super('trade-history-table', dataFlow);
    this.tradeFilterSymbol = null;
    this.tradeFilterDays = null;
    this.tradeHistoryExpanded = true;
  }

  mount() {
    this.subscribe('trades', () => this._render());
    // トレード履歴トグル
    const toggle = document.getElementById('trade-history-toggle');
    if (toggle) {
      toggle.addEventListener('click', () => {
        this.tradeHistoryExpanded = !this.tradeHistoryExpanded;
        const table = this.root;
        const chevron = document.getElementById('trade-history-chevron');
        if (table) table.classList.toggle('hidden', !this.tradeHistoryExpanded);
        if (chevron) {
          chevron.style.transform = this.tradeHistoryExpanded ? '' : 'rotate(-90deg)';
        }
      });
    }
  }

  _getFilteredTrades() {
    const data = this._dataFlow.get('trades');
    let filtered = (data && data.trades) || [];
    if (this.tradeFilterSymbol) {
      filtered = filtered.filter(t => t.symbol === this.tradeFilterSymbol);
    }
    if (this.tradeFilterDays) {
      const cutoff = Date.now() - this.tradeFilterDays * 86400000;
      filtered = filtered.filter(t => {
        const ts = new Date(t.closed_at || t.opened_at).getTime();
        return ts >= cutoff;
      });
    }
    return filtered;
  }

  _renderTradeFilters() {
    const container = document.getElementById('trade-filter-bar');
    if (!container) return;
    const data = this._dataFlow.get('trades');
    const trades = (data && data.trades) || [];
    const symbols = [...new Set(trades.map(t => t.symbol).filter(Boolean))].sort();

    const periods = [
      { label: '1D', days: 1 },
      { label: '7D', days: 7 },
      { label: '30D', days: 30 },
      { label: 'All', days: null },
    ];
    const periodBtns = periods.map(p => {
      const active = this.tradeFilterDays === p.days;
      const cls = active
        ? 'bg-blue-600 text-white'
        : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200';
      return `<button data-days="${p.days}" class="px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${cls}">${p.label}</button>`;
    }).join('');

    const symOptions = symbols.map(s =>
      `<option value="${s}" ${s === this.tradeFilterSymbol ? 'selected' : ''}>${s}</option>`
    ).join('');

    container.innerHTML = `
      <div class="flex items-center gap-2 flex-wrap">
        <div class="flex gap-1">${periodBtns}</div>
        <select id="trade-filter-symbol" class="bg-gray-700 text-gray-300 text-[11px] rounded px-2 py-0.5 border border-gray-600 focus:border-blue-500 focus:outline-none">
          <option value="">全通貨</option>
          ${symOptions}
        </select>
      </div>`;

    container.querySelectorAll('button[data-days]').forEach(btn => {
      btn.addEventListener('click', () => {
        const v = btn.dataset.days;
        this.tradeFilterDays = v === 'null' ? null : Number(v);
        this._render();
      });
    });
    const sel = document.getElementById('trade-filter-symbol');
    if (sel) {
      sel.addEventListener('change', () => {
        this.tradeFilterSymbol = sel.value || null;
        this._render();
      });
    }
  }

  _render() {
    const tableEl = this.root;
    if (!tableEl) return;
    const c = this._getCurrency();

    this._renderTradeFilters();
    const filtered = this._getFilteredTrades();

    if (filtered.length === 0) {
      tableEl.innerHTML = '<div class="flex items-center justify-center h-16 text-gray-500 text-sm">トレードなし</div>';
      return;
    }

    const exitReasonConfig = {
      SL_HIT: { l: 'SL', c: 'bg-red-900/40 text-red-400 border-red-800/50' },
      TP_HIT: { l: 'TP', c: 'bg-green-900/40 text-green-400 border-green-800/50' },
      TP_1R: { l: 'TP1R', c: 'bg-green-900/40 text-green-300 border-green-800/50' },
      TP_2R: { l: 'TP2R', c: 'bg-green-900/40 text-green-200 border-green-800/50' },
      BE_HIT: { l: 'BE', c: 'bg-blue-900/40 text-blue-400 border-blue-800/50' },
      TRAIL_HIT: { l: 'TSL', c: 'bg-cyan-900/40 text-cyan-400 border-cyan-800/50' },
      TIME: { l: 'TIME', c: 'bg-gray-700 text-gray-400 border-gray-600' },
      MANUAL: { l: 'MAN', c: 'bg-gray-700 text-gray-400 border-gray-600' },
      SIGNAL_REV: { l: 'REV', c: 'bg-yellow-900/40 text-yellow-400 border-yellow-800/50' },
      STAGNATION: { l: 'STAG', c: 'bg-purple-900/40 text-purple-400 border-purple-800/50' },
      TP_EARLY: { l: 'TP_E', c: 'bg-emerald-900/40 text-emerald-400 border-emerald-800/50' },
      FORCE_CLOSE: { l: 'FORCE', c: 'bg-orange-900/40 text-orange-400 border-orange-800/50' },
      EXTERNAL_CLOSE: { l: 'EXT', c: 'bg-gray-700 text-gray-300 border-gray-500' },
    };

    const rows = filtered.map(t => {
      const isOpen = t.is_open === true;
      const pnl = t.profit_loss || 0;
      const isProfit = pnl >= 0;
      const dirColor = t.signal_type === 'BUY' ? 'text-green-400' : 'text-red-400';

      let reasonHtml;
      if (isOpen) {
        reasonHtml = '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border bg-blue-900/40 text-blue-300 border-blue-700/50">OPEN</span>';
      } else {
        let effectiveReason = t.exit_reason;
        if (!effectiveReason || effectiveReason === 'EXTERNAL_CLOSE') {
          const ep = t.exit_price;
          if (ep != null) {
            const tol = ep * 0.0001;
            if (t.stop_loss != null && Math.abs(ep - t.stop_loss) <= tol) effectiveReason = 'SL_HIT';
            else if (t.take_profit != null && Math.abs(ep - t.take_profit) <= tol) effectiveReason = 'TP_HIT';
          }
        }
        const reason = effectiveReason && exitReasonConfig[effectiveReason];
        reasonHtml = reason
          ? `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border ${reason.c}">${reason.l}</span>`
          : '';
      }

      const pipsHtml = !isOpen && t.profit_loss_pips !== null
        ? `<span class="text-gray-500 ml-1">(${t.profit_loss_pips.toFixed(1)}p)</span>`
        : '';
      const pnlText = isOpen
        ? '-'
        : `${isProfit ? '+' : ''}${fmtCurrency(pnl, c)}${pipsHtml}`;

      const rowBg = isOpen ? 'rgba(30,58,138,0.10)' : isProfit ? 'rgba(20,83,45,0.07)' : 'rgba(127,29,29,0.07)';
      const borderClr = isOpen ? 'rgba(96,165,250,0.5)' : isProfit ? 'rgba(34,197,94,0.45)' : 'rgba(239,68,68,0.45)';
      const rowTextClr = isOpen ? 'text-gray-400' : isProfit ? 'text-green-400' : 'text-red-400';
      return `<tr style="background:${rowBg}">
        <td style="box-shadow:inset 3px 0 0 ${borderClr}" class="text-xs ${rowTextClr} whitespace-nowrap tabular-nums">${t.opened_at ? fmtDateTime(t.opened_at) : '-'}</td>
        <td class="text-xs ${rowTextClr} whitespace-nowrap tabular-nums">${t.closed_at ? fmtDateTime(t.closed_at) : '-'}</td>
        <td class="text-xs text-gray-300 whitespace-nowrap">${t.symbol || ''}</td>
        <td><span class="text-xs font-bold ${dirColor}">${t.signal_type}</span></td>
        <td class="text-xs ${rowTextClr} tabular-nums">${t.volume.toFixed(2)}</td>
        <td class="text-xs ${rowTextClr} tabular-nums">${t.entry_price.toFixed(3)}</td>
        <td class="text-xs ${rowTextClr} tabular-nums">${t.exit_price !== null ? t.exit_price.toFixed(3) : '-'}</td>
        <td>${reasonHtml}</td>
        <td class="text-right text-xs font-bold tabular-nums ${rowTextClr}">${pnlText}</td>
      </tr>`;
    }).join('');

    tableEl.innerHTML = `
      <table class="table">
        <thead class="sticky top-0 bg-gray-800 z-10">
          <tr><th>Entry日時</th><th>Exit日時</th><th>通貨</th><th>方向</th><th>Lot</th><th>Entry</th><th>Exit</th><th>ステータス</th><th class="text-right">損益</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  }
}

// ── AnalysisPanel ──

class AnalysisPanel extends Component {
  constructor(dataFlow) {
    super('analysis-panel', dataFlow);
  }

  mount() {
    this.subscribe('analysis', () => this._render());
    this.subscribe('tradingMode', () => this._render());
  }

  _render() {
    const panel = this.root;
    if (!panel) return;

    const a = this._dataFlow.get('analysis');
    const m = this._dataFlow.get('tradingMode');
    const isLive = m && (m.mode === 'live' || m.mode === 'demo');
    if (!isLive) {
      panel.classList.add('hidden');
      return;
    }
    panel.classList.remove('hidden');

    // 重要指標発表警告
    const apEventBanner = document.getElementById('ap-next-event');
    const apEventText = document.getElementById('ap-next-event-text');
    if (apEventBanner && apEventText) {
      const mins = (typeof FundamentalWidget !== 'undefined') ? FundamentalWidget.nextHighImpactMinutes : null;
      if (mins !== null && mins <= 60 && mins > 0) {
        apEventText.textContent = '重要指標まで ' + Math.round(mins) + ' 分';
        apEventBanner.classList.remove('hidden');
      } else {
        apEventBanner.classList.add('hidden');
      }
    }

    const noData = !a || (!a.engine_running && (!a.tf_scores || Object.keys(a.tf_scores).length === 0));
    if (noData) {
      const msg = (a && a.rationale) ? a.rationale : '分析データなし';
      const dirBadge = document.getElementById('ap-direction-badge');
      if (dirBadge) {
        dirBadge.textContent = '--';
        dirBadge.className = 'inline-flex items-center px-2 py-0.5 rounded text-xs font-bold bg-gray-700 text-gray-400';
      }
      const scoreText = document.getElementById('ap-score-text');
      if (scoreText) scoreText.textContent = '--';
      const scoreBar = document.getElementById('ap-score-bar');
      if (scoreBar) scoreBar.style.width = '0%';
      const htfEl = document.getElementById('ap-htf');
      if (htfEl) { htfEl.textContent = '--'; htfEl.className = 'font-bold tabular-nums text-gray-500'; }
      const trendEl = document.getElementById('ap-trend');
      if (trendEl) { trendEl.textContent = '--'; trendEl.className = 'font-bold tabular-nums text-gray-500'; }
      const penaltyEl = document.getElementById('ap-penalty');
      if (penaltyEl) { penaltyEl.textContent = '--'; penaltyEl.className = 'font-bold tabular-nums text-gray-500'; }
      const tfEl = document.getElementById('ap-tf-scores');
      if (tfEl) tfEl.innerHTML = '<p class="text-gray-500 text-xs text-center py-4">' + msg + '</p>';
      return;
    }

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

    // スコアバー
    const score = a.consensus_score || 0;
    const threshold = a.entry_threshold || 1;
    const dir = a.direction;
    const buyScore = a.buy_score || 0;
    const sellScore = a.sell_score || 0;
    const scoreText = document.getElementById('ap-score-text');
    const scoreBar = document.getElementById('ap-score-bar');
    const thSell = document.getElementById('ap-threshold-sell');
    const thBuy = document.getElementById('ap-threshold-buy');
    if (scoreText) {
      if (dir === 'HOLD' && (buyScore > 0 || sellScore > 0)) {
        scoreText.textContent = 'B:' + buyScore.toFixed(1) + ' S:' + sellScore.toFixed(1) + ' / ' + threshold.toFixed(1);
      } else {
        scoreText.textContent = score.toFixed(2) + ' / ' + threshold.toFixed(1);
      }
    }
    if (scoreBar) {
      const maxHalf = threshold * 1.5;
      let barDir = dir;
      let barScore = score;
      if (dir === 'HOLD' && (buyScore > 0 || sellScore > 0)) {
        barDir = buyScore >= sellScore ? 'BUY' : 'SELL';
        barScore = Math.max(buyScore, sellScore);
      }
      const halfPct = Math.min(50, (barScore / maxHalf) * 50);
      if (barDir === 'BUY') {
        scoreBar.style.left = '50%';
        scoreBar.style.width = halfPct + '%';
      } else if (barDir === 'SELL') {
        scoreBar.style.left = (50 - halfPct) + '%';
        scoreBar.style.width = halfPct + '%';
      } else {
        scoreBar.style.left = '50%';
        scoreBar.style.width = '0%';
      }
      let barColor;
      if (dir === 'BUY') {
        barColor = score >= threshold ? 'bg-green-500' : score >= threshold * 0.7 ? 'bg-green-700' : 'bg-gray-500';
      } else if (dir === 'SELL') {
        barColor = score >= threshold ? 'bg-red-500' : score >= threshold * 0.7 ? 'bg-red-700' : 'bg-gray-500';
      } else {
        barColor = 'bg-gray-500';
      }
      const roundedCls = barDir === 'BUY' ? 'rounded-r-full' : barDir === 'SELL' ? 'rounded-l-full' : 'rounded-full';
      scoreBar.className = 'absolute top-0 h-full ' + roundedCls + ' transition-all duration-500 ' + barColor;
    }
    if (thSell || thBuy) {
      const maxHalf = threshold * 1.5;
      const thHalfPct = Math.min(49, (threshold / maxHalf) * 50);
      if (thSell) thSell.style.left = (50 - thHalfPct) + '%';
      if (thBuy) thBuy.style.left = (50 + thHalfPct) + '%';
    }

    // サブ指標
    const htfEl = document.getElementById('ap-htf');
    const trendEl = document.getElementById('ap-trend');
    const penaltyEl = document.getElementById('ap-penalty');
    if (htfEl) {
      htfEl.textContent = (a.htf_alignment * 100).toFixed(0) + '%';
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

    // 時間足スコア
    const tfEl = document.getElementById('ap-tf-scores');
    if (tfEl && a.tf_scores) {
      const tfOrder = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'H8', 'D1'];
      const tfs = tfOrder.map(tf => [tf, a.tf_scores[tf] ?? null]);
      const bd = a.tf_breakdowns || {};
      const aligned = a.aligned_tfs || [];
      const tfDirsRaw = a.tf_directions || {};
      const tfDirs = {};
      for (const [tf] of tfs) {
        tfDirs[tf] = tfDirsRaw[tf] || 'HOLD';
      }

      let buyCount = 0, sellCount = 0, holdCount = 0;
      for (const [tf, sc] of tfs) {
        if (sc === null) continue;
        if (tfDirs[tf] === 'BUY') buyCount++;
        else if (tfDirs[tf] === 'SELL') sellCount++;
        else holdCount++;
      }
      const total = buyCount + sellCount + holdCount;
      const buyPct = total > 0 ? (buyCount / total * 100) : 0;
      const sellPct = total > 0 ? (sellCount / total * 100) : 0;
      const holdPct = total > 0 ? (holdCount / total * 100) : 0;

      const pb = a.penalty_breakdown || {};
      const penaltyLabel = {
        high_spread: 'SPR', off_hours: 'HRS',
        low_volatility: 'VOL↓', high_volatility: 'VOL↑',
        recent_loss: 'LOSS', mtf_conflict: 'MTF', weak_trend: 'TRD',
      };
      const penaltyItems = Object.keys(penaltyLabel).map(k => [k, pb[k] || 0]);
      const penaltyTotal = a.penalty_total || 0;
      const penaltyBarW = Math.min(100, Math.round(penaltyTotal / 0.5 * 100));
      const penaltyBorderCls = penaltyTotal > 0 ? 'border-red-700/50 bg-red-900/10' : 'border-gray-700 bg-gray-800/60';
      const penaltyScColor = penaltyTotal > 0 ? 'text-red-400' : 'text-gray-500';
      const penaltyCardHtml = `<div class="rounded border ${penaltyBorderCls} px-2 py-1.5">
          <div class="flex items-center justify-between mb-0.5">
            <span class="text-[10px] text-gray-400 uppercase font-bold">PEN</span>
            <span class="text-xs font-bold tabular-nums ${penaltyScColor}">-${penaltyTotal.toFixed(2)}</span>
          </div>
          <div class="w-full bg-gray-700/50 rounded-full h-1 mb-1">
            <div class="bg-red-500/60 h-1 rounded-full" style="width:${penaltyBarW}%"></div>
          </div>
          <div class="hidden xl:block space-y-0.5">
            ${penaltyItems.map(([k, v]) => {
              const lbl = penaltyLabel[k];
              const c = v > 0 ? 'text-red-400' : 'text-gray-600';
              const w = Math.min(100, Math.round(v / 0.25 * 100));
              const valText = v > 0 ? `-${v.toFixed(2)}` : '0';
              return `<div class="flex items-center gap-1">
                <span class="text-[8px] text-gray-500 w-7 text-right flex-shrink-0">${lbl}</span>
                <div class="flex-1 h-1 bg-gray-700/40 rounded-full overflow-hidden">
                  <div class="bg-red-500/40 h-full rounded-full" style="width:${w}%"></div>
                </div>
                <span class="text-[9px] tabular-nums ${c} w-8 text-right flex-shrink-0">${valText}</span>
              </div>`;
            }).join('')}
          </div>
        </div>`;

      const summaryHtml = `
        <div class="mb-2">
          <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] mb-1">
            <span class="text-gray-500">Vote:</span>
            <span class="text-green-400 font-bold">${buyCount} BUY</span>
            <span class="text-gray-500">|</span>
            <span class="text-red-400 font-bold">${sellCount} SELL</span>
            <span class="text-gray-500">|</span>
            <span class="text-gray-400">${holdCount} HOLD</span>
            <span class="text-gray-500 ml-auto">${a.regime || '--'}</span>
          </div>
          <div class="w-full h-1.5 rounded-full overflow-hidden flex">
            <div class="bg-green-500/70 h-full" style="width:${buyPct}%"></div>
            <div class="bg-gray-600/50 h-full" style="width:${holdPct}%"></div>
            <div class="bg-red-500/70 h-full" style="width:${sellPct}%"></div>
          </div>
        </div>`;

      const indLabel = {
        trend: 'TRD', adx: 'ADX', rsi: 'RSI',
        macd_slope: 'MACD', divergence: 'DIV',
        ema_cross: 'EMA', stochastic: 'STO', htf: 'HTF',
      };

      const cardsHtml = tfs.map(([tf, sc]) => {
        if (sc === null) {
          const naIndKeys = ['trend', 'adx', 'rsi', 'macd_slope', 'divergence', 'ema_cross', 'stochastic', 'htf'];
          const naDetailHtml = naIndKeys.map(k => {
            const lbl = indLabel[k] || k.slice(0, 4).toUpperCase();
            return `<div class="flex items-center gap-1">
              <span class="text-[8px] text-gray-500 w-7 text-right flex-shrink-0">${lbl}</span>
              <div class="flex-1 h-1 bg-gray-600/30 rounded-full overflow-hidden"></div>
              <span class="text-[9px] tabular-nums text-gray-500 w-6 text-right flex-shrink-0">--</span>
            </div>`;
          }).join('');
          return `<div class="rounded border border-gray-600 bg-gray-700/30 px-2 py-1.5">
            <div class="flex items-center justify-between mb-0.5">
              <div class="flex items-center">
                <span class="text-gray-500 text-[10px] mr-1">&#9644;</span>
                <span class="text-[10px] text-gray-400 uppercase font-bold">${tf}</span>
              </div>
              <span class="text-xs font-bold tabular-nums text-gray-500">N/A</span>
            </div>
            <div class="w-full bg-gray-600/40 rounded-full h-1 mb-1"></div>
            <div class="hidden xl:block space-y-0.5">${naDetailHtml}</div>
          </div>`;
        }
        const isAligned = aligned.includes(tf);
        const tfDir = tfDirs[tf];
        const dirIcon = tfDir === 'BUY' ? '&#9650;' : tfDir === 'SELL' ? '&#9660;' : '&#9644;';
        const dirColor = tfDir === 'BUY' ? 'text-green-400' : tfDir === 'SELL' ? 'text-red-400' : 'text-gray-500';
        const borderCls = isAligned
          ? (tfDir === 'SELL' ? 'border-red-600/60 bg-red-900/10' : 'border-green-600/60 bg-green-900/10')
          : 'border-gray-700 bg-gray-800/60';
        const alignBadge = isAligned
          ? `<span class="text-[8px] ${tfDir === 'SELL' ? 'text-red-400' : 'text-green-400'} font-bold ml-1">&#10003;</span>`
          : '';
        const scColor = sc > 0.5 ? 'text-green-400' : sc > 0.2 ? 'text-yellow-400' : 'text-gray-500';
        const barW = Math.min(100, sc * 100);
        const barColor = tfDir === 'BUY' ? 'bg-green-500/60' : tfDir === 'SELL' ? 'bg-red-500/60' : 'bg-gray-500/40';

        const detail = bd[tf];
        let detailHtml = '';
        if (detail) {
          const indKeys = ['trend', 'adx', 'rsi', 'macd_slope', 'divergence', 'ema_cross', 'stochastic', 'htf'];
          const items = indKeys.map(k => [k, detail[k] || 0]);
          const maxAbs = Math.max(...items.map(([, v]) => Math.abs(v)), 0.1);
          detailHtml = items.map(([k, v]) => {
            const c2 = v > 0 ? 'text-green-400' : v < 0 ? 'text-red-400' : 'text-gray-600';
            const bg = v > 0 ? 'bg-green-500/40' : 'bg-red-500/40';
            const w = Math.round(Math.abs(v) / maxAbs * 100);
            const lbl = indLabel[k] || k.slice(0, 4).toUpperCase();
            return `<div class="flex items-center gap-1">
              <span class="text-[8px] text-gray-500 w-7 text-right flex-shrink-0">${lbl}</span>
              <div class="flex-1 h-1 bg-gray-700/40 rounded-full overflow-hidden">
                <div class="${bg} h-full rounded-full" style="width:${w}%"></div>
              </div>
              <span class="text-[9px] tabular-nums ${c2} w-6 text-right flex-shrink-0">${v >= 0 ? '+' : ''}${v.toFixed(1)}</span>
            </div>`;
          }).join('');
        }

        return `<div class="rounded border ${borderCls} px-2 py-1.5">
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
          ${detailHtml ? `<div class="hidden xl:block space-y-0.5">${detailHtml}</div>` : ''}
        </div>`;
      }).join('');

      tfEl.innerHTML = `<div class="w-full">${summaryHtml}</div>` + `<div class="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-9 gap-1.5 mt-2">${cardsHtml}${penaltyCardHtml}</div>`;
    }
  }
}

// ── TradingControl ──

class TradingControl extends Component {
  constructor(dataFlow, callbacks) {
    super('symbol-dropdown-wrapper', dataFlow);
    this._onSelectSymbol = callbacks.onSelectSymbol;
    this._onFetchTradingMode = callbacks.onFetchTradingMode;
    this._onFetchAnalysis = callbacks.onFetchAnalysis;
    this.tcBusy = false;
  }

  mount() {
    this.subscribe('tradingMode', () => {
      if (!this.tcBusy) this._render();
    });
    this.subscribe('symbol', () => this.renderSymbolDropdown(true));
    this.subscribe('dashboard', () => this._updateAccountName());

    const settingsMt5Btn = document.getElementById('settings-mt5-btn');
    const trigger = document.getElementById('symbol-dropdown-trigger');
    if (settingsMt5Btn) {
      settingsMt5Btn.addEventListener('click', () => this.handleMT5Toggle());
    }
    if (trigger) {
      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const list = document.getElementById('symbol-dropdown-list');
        if (list) list.classList.toggle('hidden');
      });
    }
    // ドロップダウン外クリックで閉じる
    document.addEventListener('click', (e) => {
      const wrapper = this.root;
      const list = document.getElementById('symbol-dropdown-list');
      if (wrapper && list && !wrapper.contains(e.target)) {
        list.classList.add('hidden');
      }
    });
  }

  async handleMT5Toggle() {
    if (this.tcBusy) return;
    const m = this._dataFlow.get('tradingMode');
    const isConnected = m && m.connected;
    if (isConnected) {
      if (!confirm('MT5から切断しますか？')) return;
    }
    this.tcBusy = true;
    this._render();
    try {
      if (isConnected) await disconnectMT5();
      else await connectMT5();
      await this._onFetchTradingMode();
    } catch (e) {
      console.error('MT5操作エラー:', e);
    } finally {
      this.tcBusy = false;
      this._render();
    }
  }

  async handleDropdownDemoToggle(symbol) {
    if (this.tcBusy) return;
    const m = this._dataFlow.get('tradingMode');
    const symbolDemoStates = (m && m.symbol_demo_mode) || {};
    const currentOn = Object.prototype.hasOwnProperty.call(symbolDemoStates, symbol)
      ? symbolDemoStates[symbol] : false;
    const nextOn = !currentOn;

    // 楽観的UI更新: API応答を待たず即座に反映
    const optimistic = { ...m, symbol_demo_mode: { ...symbolDemoStates, [symbol]: nextOn } };
    this._dataFlow.publish('tradingMode', optimistic);
    this.renderSymbolDropdown(true);

    this.tcBusy = true;
    try {
      const result = await toggleSymbolDemoMode(symbol, nextOn);
      if (result) this._dataFlow.publish('tradingMode', result);
    } catch (e) {
      // 失敗時: ロールバック
      this._dataFlow.publish('tradingMode', m);
      console.error(symbol + ' デモモード切替エラー:', e);
    } finally {
      this.tcBusy = false;
      this.renderSymbolDropdown(true);
      this._onFetchAnalysis();
    }
  }

  async handleDropdownAutoToggle(symbol) {
    if (this.tcBusy) return;
    const m = this._dataFlow.get('tradingMode');
    const symbolAutoStates = (m && m.symbol_auto_trade) || {};
    const symbolDemoStates = (m && m.symbol_demo_mode) || {};
    const currentOn = Object.prototype.hasOwnProperty.call(symbolAutoStates, symbol)
      ? symbolAutoStates[symbol] : false;
    const isDemoOn = Object.prototype.hasOwnProperty.call(symbolDemoStates, symbol)
      ? symbolDemoStates[symbol] : false;
    const nextOn = !currentOn;
    if (nextOn && !isDemoOn) {
      if (!confirm(`${symbol} の自動トレード（リアルモード）を開始しますか？\n実際の売買が実行されます。`)) return;
    }

    // 楽観的UI更新: API応答を待たず即座に反映
    const optimistic = { ...m, symbol_auto_trade: { ...symbolAutoStates, [symbol]: nextOn } };
    this._dataFlow.publish('tradingMode', optimistic);
    this.renderSymbolDropdown(true);

    this.tcBusy = true;
    try {
      const result = await toggleSymbolAutoTrade(symbol, nextOn);
      if (result) this._dataFlow.publish('tradingMode', result);
    } catch (e) {
      // 失敗時: ロールバック
      this._dataFlow.publish('tradingMode', m);
      console.error(symbol + ' 自動トレード切替エラー:', e);
    } finally {
      this.tcBusy = false;
      this.renderSymbolDropdown(true);
    }
  }

  async handleGroupDemoToggle(pairs) {
    if (this.tcBusy) return;
    const m = this._dataFlow.get('tradingMode');
    const demoStates = (m && m.symbol_demo_mode) || {};
    const allOn = pairs.every(p => demoStates[p]);
    const nextOn = !allOn;

    // 楽観的UI更新: 全ペア一括で状態を反映
    const newDemoStates = { ...demoStates };
    pairs.forEach(p => { newDemoStates[p] = nextOn; });
    this._dataFlow.publish('tradingMode', { ...m, symbol_demo_mode: newDemoStates });
    this.renderSymbolDropdown(true);

    this.tcBusy = true;
    try {
      // 順次実行で中間状態の干渉を防止
      for (const p of pairs) {
        await toggleSymbolDemoMode(p, nextOn);
      }
    } catch (e) {
      this._dataFlow.publish('tradingMode', m);
      console.error('グループDEMO切替エラー:', e);
    } finally {
      this.tcBusy = false;
      // 最終状態をサーバーから取得して確定
      this._onFetchTradingMode();
      this._onFetchAnalysis();
    }
  }

  async handleGroupAutoToggle(pairs) {
    if (this.tcBusy) return;
    const m = this._dataFlow.get('tradingMode');
    const autoStates = (m && m.symbol_auto_trade) || {};
    const demoStates = (m && m.symbol_demo_mode) || {};
    const allOn = pairs.every(p => autoStates[p]);
    const nextOn = !allOn;

    // リアルモードで全ONにする場合は確認
    if (nextOn) {
      const realPairs = pairs.filter(p => !demoStates[p]);
      if (realPairs.length > 0) {
        if (!confirm(`${realPairs.join(', ')} の自動トレード（リアルモード）を開始しますか？\n実際の売買が実行されます。`)) return;
      }
    }

    // 楽観的UI更新: 全ペア一括で状態を反映
    const newAutoStates = { ...autoStates };
    pairs.forEach(p => { newAutoStates[p] = nextOn; });
    this._dataFlow.publish('tradingMode', { ...m, symbol_auto_trade: newAutoStates });
    this.renderSymbolDropdown(true);

    this.tcBusy = true;
    try {
      for (const p of pairs) {
        await toggleSymbolAutoTrade(p, nextOn);
      }
    } catch (e) {
      this._dataFlow.publish('tradingMode', m);
      console.error('グループ自動トレード切替エラー:', e);
    } finally {
      this.tcBusy = false;
      // 最終状態をサーバーから取得して確定
      this._onFetchTradingMode();
    }
  }

  _render() {
    const mt5Badge = document.getElementById('tc-mt5-badge');
    const mt5Btn = document.getElementById('settings-mt5-btn');
    if (!mt5Badge) return;

    const m = this._dataFlow.get('tradingMode');
    const isLive = m && m.mode === 'live';
    const isConnected = m && m.connected;

    if (isConnected) {
      mt5Badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-green-900/30 text-green-400 border border-green-800/50';
      mt5Badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>MT5';
    } else if (isLive) {
      mt5Badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-900/30 text-yellow-400 border border-yellow-800/50';
      mt5Badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-yellow-500"></span>MT5';
    } else {
      mt5Badge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-gray-700/80 text-gray-400 border border-gray-700/50';
      mt5Badge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-gray-500"></span>MT5';
    }

    const btnBase = 'px-4 py-2 rounded text-sm font-semibold transition-all';
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

    this.renderSymbolDropdown();
    this._updateAccountName();
  }

  _isDropdownOpen() {
    const list = document.getElementById('symbol-dropdown-list');
    return list && !list.classList.contains('hidden');
  }

  renderSymbolDropdown(force) {
    const symbolGroups = [
      { label: 'USD ペア', pairs: ['EURUSD', 'GBPUSD', 'AUDUSD', 'NZDUSD', 'USDCHF', 'USDCAD'] },
      { label: 'JPY ペア', pairs: ['USDJPY', 'EURJPY', 'GBPJPY', 'AUDJPY', 'CADJPY', 'CHFJPY'] },
    ];
    const m = this._dataFlow.get('tradingMode');
    const symbol = this._dataFlow.get('symbol');
    const isConnected = m && m.connected;
    const symbolAutoStates = (m && m.symbol_auto_trade) || {};
    const symbolDemoStates = (m && m.symbol_demo_mode) || {};

    const getPairMode = (pair) => {
      const autoOn = Object.prototype.hasOwnProperty.call(symbolAutoStates, pair) ? symbolAutoStates[pair] : false;
      const demoOn = Object.prototype.hasOwnProperty.call(symbolDemoStates, pair) ? symbolDemoStates[pair] : false;
      if (!isConnected) return { label: '未接続', dotCls: 'bg-gray-600', textCls: 'text-gray-500', pulse: false };
      if (autoOn && !demoOn) return { label: 'リアル', dotCls: 'bg-green-500', textCls: 'text-green-400', pulse: true };
      if (autoOn && demoOn) return { label: 'デモ', dotCls: 'bg-orange-400', textCls: 'text-orange-400', pulse: true };
      return { label: '待機', dotCls: 'bg-gray-500', textCls: 'text-gray-500', pulse: false };
    };

    // トリガーボタン更新
    const curMode = getPairMode(symbol);
    const trigDot = document.getElementById('symbol-trigger-dot');
    const trigLabel = document.getElementById('symbol-trigger-label');
    const trigMode = document.getElementById('symbol-trigger-mode');
    if (trigDot) trigDot.className = `w-1.5 h-1.5 rounded-full flex-shrink-0 ${curMode.dotCls}${curMode.pulse ? ' animate-pulse' : ''}`;
    if (trigLabel) trigLabel.textContent = symbol;
    if (trigMode) {
      trigMode.textContent = curMode.label;
      trigMode.className = `font-normal inline-block w-[2.5rem] ${curMode.textCls}`;
    }

    const list = document.getElementById('symbol-dropdown-list');
    if (!list) return;
    if (this._isDropdownOpen() && !force) return;

    const self = this;
    const renderPairItem = (pair) => {
      const mode = getPairMode(pair);
      const isSelected = pair === symbol;
      const selectedCls = isSelected ? 'bg-gray-700/60' : '';
      const pulseAttr = mode.pulse ? ' animate-pulse' : '';
      const autoOn = Object.prototype.hasOwnProperty.call(symbolAutoStates, pair) ? symbolAutoStates[pair] : false;
      const demoOn = Object.prototype.hasOwnProperty.call(symbolDemoStates, pair) ? symbolDemoStates[pair] : false;

      let toggleHtml = '';
      if (isConnected) {
        const busy = self.tcBusy;
        const disabledAttr = busy ? ' disabled' : '';
        const disabledCls = busy ? ' opacity-50 cursor-not-allowed' : '';
        const demoCls = demoOn
          ? 'bg-orange-500/25 text-orange-400 border border-orange-600/50 hover:bg-orange-500/40'
          : 'bg-gray-700/80 text-gray-500 border border-gray-600/50 hover:bg-gray-600/80 hover:text-gray-300';
        const autoCls = autoOn
          ? (demoOn ? 'bg-orange-600 text-white hover:bg-orange-700' : 'bg-red-600 text-white hover:bg-red-700')
          : 'bg-green-600/90 text-white hover:bg-green-700';
        const autoLabel = autoOn ? 'ON' : 'OFF';
        toggleHtml = `<div class="flex items-center gap-1 flex-shrink-0 ml-auto">
          <button data-action="demo" data-symbol="${pair}"${disabledAttr}
                  class="px-1.5 py-0.5 rounded text-[10px] font-bold transition-all min-w-[3rem] text-center ${demoCls}${disabledCls}" title="デモモード">DEMO</button>
          <button data-action="auto" data-symbol="${pair}"${disabledAttr}
                  class="px-1.5 py-0.5 rounded text-[10px] font-bold transition-all min-w-[3rem] text-center ${autoCls}${disabledCls}" title="自動トレード">${autoLabel}</button>
        </div>`;
      }

      const checkSvg = isSelected
        ? '<svg class="w-3 h-3 text-blue-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>'
        : '';

      return `<div class="dd-pair-row flex items-center gap-2 px-3 py-2 hover:bg-gray-700/50 cursor-pointer transition-colors select-none ${selectedCls}" data-pair="${pair}">
        <span class="w-2 h-2 rounded-full flex-shrink-0 ${mode.dotCls}${pulseAttr}"></span>
        <span class="font-semibold text-xs text-gray-200 tabular-nums">${pair}</span>
        <span class="text-[10px] ${mode.textCls}">${mode.label}</span>
        ${checkSvg}
        ${toggleHtml}
      </div>`;
    };

    list.innerHTML = symbolGroups.map((group, groupIdx) => {
      const divider = groupIdx > 0 ? '<div class="border-t border-gray-700/60 my-1"></div>' : '';
      // グループ内の状態集計
      let groupToggleHtml = '';
      if (isConnected) {
        const busy = self.tcBusy;
        const disabledAttr = busy ? ' disabled' : '';
        const disabledCls = busy ? ' opacity-50 cursor-not-allowed' : '';
        const allDemo = group.pairs.every(p => symbolDemoStates[p]);
        const allAuto = group.pairs.every(p => symbolAutoStates[p]);
        const demoCls = allDemo
          ? 'bg-orange-500/25 text-orange-400 border border-orange-600/50 hover:bg-orange-500/40'
          : 'bg-gray-700/80 text-gray-500 border border-gray-600/50 hover:bg-gray-600/80 hover:text-gray-300';
        const autoCls = allAuto
          ? 'bg-red-600 text-white hover:bg-red-700'
          : 'bg-green-600/90 text-white hover:bg-green-700';
        const autoLabel = allAuto ? 'ALL ON' : 'ALL OFF';
        groupToggleHtml = `<div class="flex items-center gap-1 ml-auto">
          <button data-group-action="demo" data-group-pairs="${group.pairs.join(',')}"${disabledAttr}
                  class="px-1.5 py-0.5 rounded text-[10px] font-bold transition-all min-w-[3rem] text-center ${demoCls}${disabledCls}">DEMO</button>
          <button data-group-action="auto" data-group-pairs="${group.pairs.join(',')}"${disabledAttr}
                  class="px-1.5 py-0.5 rounded text-[10px] font-bold transition-all min-w-[3rem] text-center ${autoCls}${disabledCls}">${autoLabel}</button>
        </div>`;
      }
      const header = `<div class="px-3 pt-1.5 pb-0.5 flex items-center gap-1">
        <span class="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">${group.label}</span>
        ${groupToggleHtml}
      </div>`;
      return divider + header + group.pairs.map(renderPairItem).join('');
    }).join('');

    list.onclick = (e) => {
      // グループ一括ボタン
      const groupBtn = e.target.closest('[data-group-action]');
      if (groupBtn) {
        e.stopPropagation();
        const action = groupBtn.dataset.groupAction;
        const pairs = groupBtn.dataset.groupPairs.split(',');
        if (action === 'demo') self.handleGroupDemoToggle(pairs);
        else if (action === 'auto') self.handleGroupAutoToggle(pairs);
        return;
      }
      // 個別ボタン
      const btn = e.target.closest('[data-action]');
      if (btn) {
        e.stopPropagation();
        const action = btn.dataset.action;
        const sym = btn.dataset.symbol;
        if (action === 'demo') self.handleDropdownDemoToggle(sym);
        else if (action === 'auto') self.handleDropdownAutoToggle(sym);
        return;
      }
      const row = e.target.closest('.dd-pair-row');
      if (row) self._onSelectSymbol(row.dataset.pair);
    };
  }

  _updateAccountName() {
    const el = document.getElementById('header-account-name');
    if (!el) return;
    const m = this._dataFlow.get('tradingMode');
    const d = this._dataFlow.get('dashboard');
    const isConnected = m && m.connected;
    const acct = isConnected && d && d.account ? d.account : null;
    if (!acct) { el.classList.add('hidden'); return; }
    const nick = (typeof SettingsManager !== 'undefined') ? SettingsManager.getNickname(acct.login) : '';
    el.textContent = nick || '#' + acct.login;
    el.classList.remove('hidden');
  }
}

// ── DashboardApp (薄いオーケストレータ) ──

const DashboardApp = {
  dataFlow: null,
  metricsStrip: null,
  positionPanel: null,
  tradeHistory: null,
  analysisPanel: null,
  tradingControl: null,
  // WebSocket駆動フラグ
  wsActive: false,
  // trade_idキャッシュ
  _tradeIdCache: {},

  // 後方互換ゲッター（settings.js等の外部参照用）
  get dashboard() { return this.dataFlow ? this.dataFlow.get('dashboard') : null; },
  get tradingMode() { return this.dataFlow ? this.dataFlow.get('tradingMode') : null; },
  get symbol() { return this.dataFlow ? this.dataFlow.get('symbol') : 'USDJPY'; },

  init() {
    const df = new DataFlowManager();
    this.dataFlow = df;

    // 保存済みシンボル復元
    const savedSymbol = localStorage.getItem('chart_symbol') || 'USDJPY';
    df.publish('symbol', savedSymbol);

    // コンポーネント生成
    this.metricsStrip = new MetricsStrip(df);
    this.positionPanel = new PositionPanel(df);
    this.tradeHistory = new TradeHistory(df);
    this.analysisPanel = new AnalysisPanel(df);
    this.tradingControl = new TradingControl(df, {
      onSelectSymbol: (s) => this.selectSymbol(s),
      onFetchTradingMode: () => this.fetchTradingMode(),
      onFetchAnalysis: () => this.fetchAnalysis(),
    });

    // コンポーネントマウント
    this.metricsStrip.mount();
    this.positionPanel.mount();
    this.tradeHistory.mount();
    this.analysisPanel.mount();
    this.tradingControl.mount();

    // グローバルブリッジ（インラインonclick互換）
    this._closeVolumes = this.positionPanel._closeVolumes;

    // UI初期化
    const sel = document.getElementById('symbol-selector');
    if (sel) sel.value = savedSymbol;
    const chartTitle = document.getElementById('chart-title');
    if (chartTitle) chartTitle.textContent = savedSymbol + ' チャート';

    // チャート初期化
    ChartManager.init('chart-container', savedSymbol);

    // エンジン確保
    ensureSymbolEngine(savedSymbol).catch(() => {});

    // データ取得
    this.fetchAll();
    this.fetchTradingMode();
    this.fetchAnalysis();

    // ファンダメンタルウィジェット
    if (typeof FundamentalWidget !== 'undefined') {
      FundamentalWidget.init(savedSymbol);
    }

    // チャートは30秒毎にフル再取得
    this.pollInterval = setInterval(() => this.fetchAll(), 30000);

    // WS切断中フォールバック（10秒毎）
    this._fallbackInterval = setInterval(() => {
      if (!this.wsActive) {
        this.fetchAnalysis();
        this.fetchPositionsAndTrades();
        this.fetchTradingMode();
      }
    }, 10000);

    // WebSocket
    this.dashWs = createWebSocketClient('/ws/dashboard');
    this.dashWs.on('price_update', (msg) => {
      this.wsActive = true;
      const { symbol, bid, time_ms } = msg.data;
      if (bid > 0 && symbol === df.get('symbol')) {
        ChartManager.updateLastBar(bid, time_ms);
      }
    });
    this.dashWs.on('tick_update', (msg) => {
      this.wsActive = true;
      this._applyTickUpdate(msg.data);
    });
    this.dashWs.on('position_update', () => {
      this.wsActive = true;
      this.fetchPositionsAndTrades();
    });
    this.dashWs.on('news_update', (msg) => {
      if (typeof FundamentalWidget !== 'undefined') FundamentalWidget.onNewsUpdate(msg);
    });
    this.dashWs.on('calendar_update', (msg) => {
      if (typeof FundamentalWidget !== 'undefined') FundamentalWidget.onCalendarUpdate(msg);
    });
    this.dashWs.onStateChange((state) => {
      if (state === 'disconnected' || state === 'error') this.wsActive = false;
    });
    this.dashWs.connect();
  },

  // ── tick_update 一括適用 ──

  _applyTickUpdate(data) {
    const df = this.dataFlow;
    const symbol = df.get('symbol');

    // 分析パネル
    if (data.analysis) {
      if (!data.analysis.symbol || data.analysis.symbol === symbol) {
        df.publish('analysis', data.analysis);
      }
    }

    // メトリクス（口座情報のみ → dashboardチャネル）
    if (data.account) {
      const d = df.get('dashboard');
      if (d) {
        df.publish('dashboard', {
          ...d,
          account: { ...d.account, ...data.account },
        });
      }
    }

    // ポジション（シンボル単位マージ: 各エンジンは自シンボル分のみ送信）
    if (data.positions !== undefined) {
      const tickSymbol = (data.analysis && data.analysis.symbol) || '';
      for (const p of data.positions) {
        p.trade_id = this._tradeIdCache[p.ticket] || '';
      }
      const prevPositions = df.get('positions') || [];
      // 送信元シンボルのポジションだけ差し替え、他シンボルは保持
      const otherPositions = tickSymbol
        ? prevPositions.filter(p => p.symbol !== tickSymbol)
        : [];
      const merged = [...otherPositions, ...data.positions]
        .sort((a, b) => a.ticket - b.ticket);
      const prevTickets = new Set(prevPositions.map(p => p.ticket));
      const mergedTickets = new Set(merged.map(p => p.ticket));
      df.publish('positions', merged);
      // ポジション増減時はREST再取得でtrade_idをDB同期
      const added = merged.some(p => !prevTickets.has(p.ticket));
      const removed = [...prevTickets].some(t => !mergedTickets.has(t));
      if (added || removed) {
        this.fetchPositionsAndTrades();
      }
    }
  },

  // ── データ取得 ──

  _syncTradeIdCache() {
    const positions = this.dataFlow.get('positions') || [];
    const active = new Set();
    for (const p of positions) {
      if (p.trade_id) this._tradeIdCache[p.ticket] = p.trade_id;
      active.add(p.ticket);
    }
    for (const ticket of Object.keys(this._tradeIdCache)) {
      if (!active.has(Number(ticket))) delete this._tradeIdCache[ticket];
    }
  },

  async fetchPositionsAndTrades() {
    const [dash, pos, tr, summary] = await Promise.allSettled([
      getDashboard(),
      getPositions(null),
      getTrades(null, 100),
      getTradeSummary(null, 30),
    ]);
    const df = this.dataFlow;
    if (dash.status === 'fulfilled') df.publish('dashboard', dash.value);
    if (pos.status === 'fulfilled') {
      const sorted = [...pos.value].sort((a, b) => a.ticket - b.ticket);
      df.publish('positions', sorted);
      this._syncTradeIdCache();
    }
    if (tr.status === 'fulfilled' || summary.status === 'fulfilled') {
      df.publish('trades', {
        trades: tr.status === 'fulfilled' ? tr.value : ((df.get('trades') || {}).trades || []),
        summary: summary.status === 'fulfilled' ? summary.value : ((df.get('trades') || {}).summary || null),
      });
    }
  },

  async fetchAll() {
    const [dash, pos, tr, summary] = await Promise.allSettled([
      getDashboard(),
      getPositions(null),
      getTrades(null, 100),
      getTradeSummary(null, 30),
    ]);
    const df = this.dataFlow;
    if (dash.status === 'fulfilled') df.publish('dashboard', dash.value);
    if (pos.status === 'fulfilled') {
      const sorted = [...pos.value].sort((a, b) => a.ticket - b.ticket);
      df.publish('positions', sorted);
      this._syncTradeIdCache();
    }
    if (tr.status === 'fulfilled' || summary.status === 'fulfilled') {
      df.publish('trades', {
        trades: tr.status === 'fulfilled' ? tr.value : ((df.get('trades') || {}).trades || []),
        summary: summary.status === 'fulfilled' ? summary.value : ((df.get('trades') || {}).summary || null),
      });
    }
  },

  async fetchAnalysis() {
    const symbol = this.dataFlow.get('symbol');
    try {
      const analysis = await getAnalysis(symbol);
      this.dataFlow.publish('analysis', analysis);
    } catch (e) {
      this.dataFlow.publish('analysis', null);
    }
  },

  async fetchTradingMode() {
    try {
      const mode = await getTradingMode();
      this.dataFlow.publish('tradingMode', mode);
    } catch (e) {
      this.dataFlow.publish('tradingMode', null);
    }
  },

  async selectSymbol(symbol) {
    const df = this.dataFlow;
    df.publish('symbol', symbol);
    localStorage.setItem('chart_symbol', symbol);
    const sel = document.getElementById('symbol-selector');
    if (sel) sel.value = symbol;
    const chartTitle = document.getElementById('chart-title');
    if (chartTitle) chartTitle.textContent = symbol + ' チャート';
    ChartManager.setSymbol(symbol);
    const list = document.getElementById('symbol-dropdown-list');
    if (list) list.classList.add('hidden');
    if (typeof FundamentalWidget !== 'undefined') {
      FundamentalWidget.changeSymbol(symbol);
    }
    try { await ensureSymbolEngine(symbol); } catch (e) { /* 続行 */ }
    df.publish('analysis', null);
    this.fetchAnalysis();
    this.fetchAll();
  },

  // ── グローバルブリッジ（インラインonclick互換） ──

  togglePositionDetail(...args) {
    this.positionPanel.togglePositionDetail(...args);
  },
  closePosition(...args) {
    return this.positionPanel.closePosition(...args);
  },
  _selectClosePct(...args) {
    this.positionPanel._selectClosePct(...args);
  },
  updateHeaderAccountName() {
    if (this.tradingControl) this.tradingControl._updateAccountName();
  },
};

// ページ読み込み時に初期化
document.addEventListener('DOMContentLoaded', () => {
  DashboardApp.init();
});
