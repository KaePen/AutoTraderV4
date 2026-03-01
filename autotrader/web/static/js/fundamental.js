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

  /** シンボル変更時の軽量更新（カウントダウン再起動なし） */
  changeSymbol(symbol) {
    this.symbol = symbol || 'USDJPY';
    this.fetchNews();
    this.fetchCalendar();
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

  /** カレンダー取得（当日+2営業日先まで） */
  async fetchCalendar() {
    try {
      // 週末を跨ぐ場合を考慮して余裕を持って取得
      const data = await getFundamentalCalendar(this.symbol, 4);
      var raw = (data && data.events) || [];
      // 当日+翌営業日のみにフィルタ
      this.calendarEvents = this._filterTodayAndNextBizDay(raw);
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
    if (d.symbol && d.symbol !== this.symbol) return;
    const exists = this.newsItems.some(function(n) { return n.news_id === d.news_id; });
    if (!exists) {
      this.newsItems.unshift(d);
      if (this.newsItems.length > 100) {
        this.newsItems = this.newsItems.slice(0, 100);
      }
    }
    this.renderNews();
  },

  /** WebSocket: カレンダー更新ハンドラ */
  onCalendarUpdate(msg) {
    if (!msg || !msg.data) return;
    this.fetchCalendar();
  },

  /** ニュース描画 */
  renderNews() {
    const container = document.getElementById('fundamental-news-list');
    const countEl = document.getElementById('fundamental-news-count');
    const updatedEl = document.getElementById('fundamental-news-updated');
    if (!container) return;

    if (countEl) {
      countEl.textContent = this.newsItems.length + ' 件';
    }
    if (updatedEl) {
      var now = new Date();
      updatedEl.textContent = now.getHours().toString().padStart(2, '0') + ':' +
        now.getMinutes().toString().padStart(2, '0');
    }

    if (this.newsItems.length === 0) {
      container.innerHTML = '<div class="flex items-center justify-center h-16 text-gray-500 text-sm">ニュースなし</div>';
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
      container.innerHTML = '<div class="flex items-center justify-center h-16 text-gray-500 text-sm">イベントなし</div>';
      return;
    }

    var html = '<table class="w-full text-xs">';
    html += '<thead><tr class="text-gray-500 text-[10px]">' +
      '<th class="text-left py-1 px-1 w-5"></th>' +
      '<th class="text-left py-1 px-1">Time</th>' +
      '<th class="text-left py-1 px-1">Ccy</th>' +
      '<th class="text-left py-1 px-1">Event</th>' +
      '<th class="text-right py-1 px-1">Actual</th>' +
      '<th class="text-right py-1 px-1">Forecast</th>' +
      '<th class="text-right py-1 px-1">Previous</th>' +
      '<th class="text-right py-1 px-1 w-16">ETA</th>' +
      '</tr></thead><tbody>';

    var prevDateStr = '';
    for (var i = 0; i < this.calendarEvents.length; i++) {
      var ev = this.calendarEvents[i];
      var impactCls = this._impactClass(ev.impact);
      var evDate = new Date(ev.event_time);
      var dateStr = this._formatDateLabelJST(evDate);
      var timeStr = this._formatTimeOnlyJST(evDate);
      var mins = ev.minutes_until || 0;
      var countdownStr = this._formatCountdown(mins);
      var rowCls = ev.is_released ? 'text-gray-500' : 'text-gray-300';
      var actualCls = ev.is_released ? 'text-blue-400 font-semibold' : '';

      // 日付区切り行
      if (dateStr !== prevDateStr) {
        html += '<tr class="bg-gray-800/80"><td colspan="8" class="py-1 px-2 text-[10px] text-gray-400 font-semibold">' + dateStr + '</td></tr>';
        prevDateStr = dateStr;
      }

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
      textEl.textContent = '重要指標まで ' + mins + ' 分';
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
      var cells = document.querySelectorAll('[data-event-time]');
      var now = new Date();
      for (var i = 0; i < cells.length; i++) {
        var eventTime = new Date(cells[i].getAttribute('data-event-time'));
        var diffMs = eventTime - now;
        var mins = diffMs / 60000;
        cells[i].textContent = self._formatCountdown(mins);
      }
      if (self.nextHighImpactMinutes !== null) {
        self.nextHighImpactMinutes -= 1;
        self.renderNextEventWarning();
      }
    }, 60000);
  },

  // ── ヘルパー ──

  /** 当日+2営業日先までのイベントを抽出（JST日付基準） */
  _filterTodayAndNextBizDay(events) {
    // JST基準で当日の開始を計算
    var jstNow = new Date(Date.now() + 9 * 3600000);
    var todayJSTStart =
      Date.UTC(
        jstNow.getUTCFullYear(),
        jstNow.getUTCMonth(),
        jstNow.getUTCDate(),
      ) -
      9 * 3600000; // JST 0:00 をUTCミリ秒に変換

    // 2営業日先を計算（土日スキップ）
    var endDate = new Date(todayJSTStart + 9 * 3600000); // JST基準
    var bizCount = 0;
    while (bizCount < 2) {
      endDate.setUTCDate(endDate.getUTCDate() + 1);
      if (endDate.getUTCDay() !== 0 && endDate.getUTCDay() !== 6) {
        bizCount++;
      }
    }
    // 2営業日先の終わり（JST 23:59:59 = UTC 14:59:59）
    var nextBizEndMs =
      Date.UTC(
        endDate.getUTCFullYear(),
        endDate.getUTCMonth(),
        endDate.getUTCDate(),
        23,
        59,
        59,
        999,
      ) -
      9 * 3600000;

    var filtered = [];
    for (var i = 0; i < events.length; i++) {
      var ev = events[i];
      var evTime = new Date(ev.event_time);
      var evMs = evTime.getTime();
      if (evMs >= todayJSTStart && evMs <= nextBizEndMs) {
        filtered.push(ev);
      }
    }
    return filtered;
  },

  /** 日付ラベル（JST基準 — 日本ユーザー向け） */
  _formatDateLabelJST(d) {
    var days = ['日', '月', '火', '水', '木', '金', '土'];
    // JST基準の日付計算
    var jstNow = new Date(Date.now() + 9 * 3600000);
    var jstTarget = new Date(d.getTime() + 9 * 3600000);
    var todayJST = Date.UTC(
      jstNow.getUTCFullYear(),
      jstNow.getUTCMonth(),
      jstNow.getUTCDate(),
    );
    var targetJST = Date.UTC(
      jstTarget.getUTCFullYear(),
      jstTarget.getUTCMonth(),
      jstTarget.getUTCDate(),
    );
    var diffDays = Math.round((targetJST - todayJST) / 86400000);

    var prefix = '';
    if (diffDays === 0) prefix = '今日';
    else if (diffDays === 1) prefix = '明日';
    else if (diffDays === 2) prefix = '明後日';
    else
      prefix = jstTarget.getUTCMonth() + 1 + '/' + jstTarget.getUTCDate();

    return prefix + '（' + days[jstTarget.getUTCDay()] + '）';
  },

  /** 時刻表示（JST = Asia/Tokyo） */
  _formatTimeOnlyJST(d) {
    if (!d || isNaN(d.getTime())) return '--:--';
    // Intl APIでJST変換（DST考慮不要だが堅牢性のため）
    try {
      return d.toLocaleTimeString('ja-JP', {
        timeZone: 'Asia/Tokyo',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
    } catch (e) {
      // Intl非対応環境フォールバック（UTC+9固定）
      var jst = new Date(d.getTime() + 9 * 3600000);
      return (
        jst.getUTCHours().toString().padStart(2, '0') +
        ':' +
        jst.getUTCMinutes().toString().padStart(2, '0')
      );
    }
  },

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

  /** カウントダウンフォーマット */
  _formatCountdown(mins) {
    if (mins <= 0) return '済';
    if (mins < 60) return Math.round(mins) + '分';
    var h = Math.floor(mins / 60);
    var m = Math.round(mins % 60);
    return h + '時間' + (m > 0 ? m + '分' : '');
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
