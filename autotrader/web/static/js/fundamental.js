/** ファンダメンタルウィジェット（ニュース + 経済カレンダー） */

const FundamentalWidget = {
  symbol: 'USDJPY',
  newsItems: [],
  calendarEvents: [],
  nextHighImpactMinutes: null,
  _countdownInterval: null,

  /** 初期化 */
  init(symbol) {
    this.symbol = symbol || 'USDJPY';
    this.fetchNews();
    this.fetchCalendar();
    this.startCountdown();
  },

  /** ニュース取得 */
  async fetchNews() {
    try {
      const data = await getFundamentalNews(this.symbol);
      this.newsItems = (data && data.items) || [];
    } catch (e) {
      this.newsItems = [];
    }
    this.renderNews();
  },

  /** カレンダー取得 */
  async fetchCalendar() {
    try {
      const data = await getFundamentalCalendar(this.symbol);
      this.calendarEvents = (data && data.events) || [];
      this.nextHighImpactMinutes = data ? data.next_high_impact_minutes : null;
    } catch (e) {
      this.calendarEvents = [];
      this.nextHighImpactMinutes = null;
    }
    this.renderCalendar();
    this.renderNextEventWarning();
  },

  /** WebSocket: ニュース更新ハンドラ */
  onNewsUpdate(msg) {
    if (!msg || !msg.data) return;
    const d = msg.data;
    // シンボルフィルタ（配信されたシンボルと一致する場合のみ）
    if (d.symbol && d.symbol !== this.symbol) return;
    // 先頭に追加（重複排除）
    const exists = this.newsItems.some(function(n) { return n.news_id === d.news_id; });
    if (!exists) {
      this.newsItems.unshift(d);
      // 上限100件
      if (this.newsItems.length > 100) {
        this.newsItems = this.newsItems.slice(0, 100);
      }
    }
    this.renderNews();
  },

  /** WebSocket: カレンダー更新ハンドラ */
  onCalendarUpdate(msg) {
    if (!msg || !msg.data) return;
    // フルリフレッシュ
    this.fetchCalendar();
  },

  /** ニュース描画 */
  renderNews() {
    const container = document.getElementById('fundamental-news-list');
    const countEl = document.getElementById('fundamental-news-count');
    const updatedEl = document.getElementById('fundamental-news-updated');
    if (!container) return;

    if (countEl) {
      countEl.textContent = this.newsItems.length + ' items';
    }
    if (updatedEl) {
      var now = new Date();
      updatedEl.textContent = now.getHours().toString().padStart(2, '0') + ':' +
        now.getMinutes().toString().padStart(2, '0');
    }

    if (this.newsItems.length === 0) {
      container.innerHTML = '<div class="flex items-center justify-center h-16 text-gray-500 text-sm">No news available</div>';
      return;
    }

    var html = '';
    for (var i = 0; i < this.newsItems.length; i++) {
      var n = this.newsItems[i];
      var sentCls = this._sentimentClass(n.sentiment_score);
      var timeStr = this._formatTime(n.published_at);
      var currTags = '';
      var currencies = n.currencies || [];
      for (var j = 0; j < currencies.length; j++) {
        currTags += '<span class="px-1 py-0.5 rounded text-[9px] font-semibold bg-gray-700 text-gray-400">' + currencies[j] + '</span>';
      }

      html += '<div class="flex gap-2 p-2 rounded bg-gray-800/50 hover:bg-gray-800 transition-colors">' +
        '<div class="w-1 rounded-full flex-shrink-0 ' + sentCls + '"></div>' +
        '<div class="flex-1 min-w-0">' +
          '<p class="text-xs text-gray-200 leading-tight line-clamp-2">' + this._escapeHtml(n.title) + '</p>' +
          '<div class="flex items-center gap-1.5 mt-1">' +
            '<span class="text-[10px] text-gray-500">' + this._escapeHtml(n.source_name) + '</span>' +
            currTags +
            '<span class="text-[10px] text-gray-600 ml-auto tabular-nums">' + timeStr + '</span>' +
          '</div>' +
        '</div>' +
      '</div>';
    }
    container.innerHTML = html;
  },

  /** カレンダー描画 */
  renderCalendar() {
    var container = document.getElementById('fundamental-calendar-list');
    var updatedEl = document.getElementById('fundamental-calendar-updated');
    if (!container) return;

    if (updatedEl) {
      var now = new Date();
      updatedEl.textContent = now.getHours().toString().padStart(2, '0') + ':' +
        now.getMinutes().toString().padStart(2, '0');
    }

    if (this.calendarEvents.length === 0) {
      container.innerHTML = '<div class="flex items-center justify-center h-16 text-gray-500 text-sm">No events</div>';
      return;
    }

    var html = '<table class="w-full text-xs">';
    html += '<thead><tr class="text-gray-500 text-[10px]">' +
      '<th class="text-left py-1 px-1 w-5"></th>' +
      '<th class="text-left py-1 px-1">Time</th>' +
      '<th class="text-left py-1 px-1">Ccy</th>' +
      '<th class="text-left py-1 px-1">Event</th>' +
      '<th class="text-right py-1 px-1">Act</th>' +
      '<th class="text-right py-1 px-1">Fct</th>' +
      '<th class="text-right py-1 px-1">Prev</th>' +
      '<th class="text-right py-1 px-1 w-16">Until</th>' +
      '</tr></thead><tbody>';

    for (var i = 0; i < this.calendarEvents.length; i++) {
      var ev = this.calendarEvents[i];
      var impactCls = this._impactClass(ev.impact);
      var timeStr = this._formatEventTime(ev.event_time);
      var mins = ev.minutes_until || 0;
      var countdownStr = this._formatCountdown(mins);
      var rowCls = ev.is_released ? 'text-gray-500' : 'text-gray-300';
      var actualCls = ev.is_released ? 'text-blue-400 font-semibold' : '';

      html += '<tr class="' + rowCls + ' border-t border-gray-800/50 hover:bg-gray-800/30">' +
        '<td class="py-1.5 px-1"><span class="' + impactCls + '"></span></td>' +
        '<td class="py-1.5 px-1 tabular-nums whitespace-nowrap">' + timeStr + '</td>' +
        '<td class="py-1.5 px-1 font-semibold">' + this._escapeHtml(ev.currency) + '</td>' +
        '<td class="py-1.5 px-1 truncate max-w-[120px]" title="' + this._escapeHtml(ev.event_name) + '">' + this._escapeHtml(ev.event_name) + '</td>' +
        '<td class="py-1.5 px-1 text-right tabular-nums ' + actualCls + '">' + this._fmtValue(ev.actual) + '</td>' +
        '<td class="py-1.5 px-1 text-right tabular-nums">' + this._fmtValue(ev.forecast) + '</td>' +
        '<td class="py-1.5 px-1 text-right tabular-nums">' + this._fmtValue(ev.previous) + '</td>' +
        '<td class="py-1.5 px-1 text-right tabular-nums whitespace-nowrap" data-event-time="' + ev.event_time + '">' + countdownStr + '</td>' +
        '</tr>';
    }
    html += '</tbody></table>';
    container.innerHTML = html;
  },

  /** 次のHIGHイベント警告バナー */
  renderNextEventWarning() {
    var banner = document.getElementById('fundamental-next-event');
    var textEl = document.getElementById('fundamental-next-event-text');
    if (!banner || !textEl) return;

    if (this.nextHighImpactMinutes !== null && this.nextHighImpactMinutes <= 60 && this.nextHighImpactMinutes > 0) {
      var mins = Math.round(this.nextHighImpactMinutes);
      textEl.textContent = 'HIGH impact event in ' + mins + ' min';
      banner.classList.remove('hidden');
    } else {
      banner.classList.add('hidden');
    }
  },

  /** カウントダウン更新タイマー開始 */
  startCountdown() {
    if (this._countdownInterval) {
      clearInterval(this._countdownInterval);
    }
    var self = this;
    this._countdownInterval = setInterval(function() {
      // カレンダーのカウントダウンセルを更新
      var cells = document.querySelectorAll('[data-event-time]');
      var now = new Date();
      for (var i = 0; i < cells.length; i++) {
        var eventTime = new Date(cells[i].getAttribute('data-event-time'));
        var diffMs = eventTime - now;
        var mins = diffMs / 60000;
        cells[i].textContent = self._formatCountdown(mins);
      }
      // 警告バナーも更新
      if (self.nextHighImpactMinutes !== null) {
        self.nextHighImpactMinutes -= 1;
        self.renderNextEventWarning();
      }
    }, 60000);
  },

  // ── ヘルパー ──

  /** センチメントスコアに応じた色クラス */
  _sentimentClass(score) {
    if (score === null || score === undefined) return 'bg-gray-600';
    if (score > 0.2) return 'bg-green-500';
    if (score < -0.2) return 'bg-red-500';
    return 'bg-gray-600';
  },

  /** インパクトに応じた色クラス */
  _impactClass(impact) {
    if (impact === 'high') return 'inline-block w-2 h-2 rounded-full bg-red-500';
    if (impact === 'medium') return 'inline-block w-2 h-2 rounded-full bg-yellow-500';
    return 'inline-block w-1.5 h-1.5 rounded-full bg-gray-600';
  },

  /** 時刻フォーマット（HH:MM） */
  _formatTime(isoStr) {
    if (!isoStr) return '--:--';
    var d = new Date(isoStr);
    if (isNaN(d.getTime())) return '--:--';
    return d.getHours().toString().padStart(2, '0') + ':' +
      d.getMinutes().toString().padStart(2, '0');
  },

  /** イベント時刻フォーマット（MM/DD HH:MM） */
  _formatEventTime(isoStr) {
    if (!isoStr) return '--';
    var d = new Date(isoStr);
    if (isNaN(d.getTime())) return '--';
    return (d.getMonth() + 1).toString().padStart(2, '0') + '/' +
      d.getDate().toString().padStart(2, '0') + ' ' +
      d.getHours().toString().padStart(2, '0') + ':' +
      d.getMinutes().toString().padStart(2, '0');
  },

  /** カウントダウンフォーマット */
  _formatCountdown(mins) {
    if (mins <= 0) return 'done';
    if (mins < 60) return Math.round(mins) + 'm';
    var h = Math.floor(mins / 60);
    var m = Math.round(mins % 60);
    return h + 'h' + (m > 0 ? m + 'm' : '');
  },

  /** 数値フォーマット（null対応） */
  _fmtValue(val) {
    if (val === null || val === undefined) return '-';
    return String(val);
  },

  /** HTMLエスケープ */
  _escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }
};
