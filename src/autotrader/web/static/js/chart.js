/** チャートモジュール */

const ChartManager = {
  chart: null,
  rsiChart: null,
  candleSeries: null,
  volumeSeries: null,
  rsiSeries: null,
  containerEl: null,
  rsiContainerEl: null,
  timeframe: 'M15',
  symbol: 'USDJPY',
  signals: [],
  isLoading: false,
  resizeObserver: null,
  rsiResizeObserver: null,
  // 自動更新
  _refreshTimer: null,
  _refreshMs: 1000, // デフォルト1秒
  // 指標シリーズ（オーバーレイ）
  _indicatorSeries: {
    ema12: null,
    ema26: null,
    bbUpper: null,
    bbMiddle: null,
    bbLower: null,
  },
  // 指標表示ON/OFF状態
  _indVisible: {
    ema: true,
    bb: true,
    rsi: true,
  },

  /** 初期化 */
  init(containerId, symbol) {
    this.containerEl = document.getElementById(containerId);
    if (!this.containerEl) return;
    this.symbol = symbol || 'USDJPY';

    // localStorageから設定を復元
    const savedTf = localStorage.getItem('chart_timeframe');
    if (savedTf) this.timeframe = savedTf;
    const savedMs = localStorage.getItem('chart_refresh_ms');
    if (savedMs !== null) this._refreshMs = parseInt(savedMs, 10);

    this.createChart();
    this.fetchCandles();
    this.renderTimeframeButtons();
    this.renderRefreshSelector();
    this.renderIndicatorToggles();
    this._scheduleRefresh();
  },

  /** チャート作成 */
  createChart() {
    if (!this.containerEl) return;

    this.chart = LightweightCharts.createChart(this.containerEl, {
      layout: {
        background: { type: 'solid', color: '#1f2937' },
        textColor: '#9ca3af',
      },
      grid: {
        vertLines: { color: '#374151' },
        horzLines: { color: '#374151' },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: '#374151' },
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    this.candleSeries = this.chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
      priceFormat: {
        type: 'price',
        precision: 3,
        minMove: 0.001,
      },
    });

    // EMA(12)
    this._indicatorSeries.ema12 = this.chart.addLineSeries({
      color: '#60a5fa',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    // EMA(26)
    this._indicatorSeries.ema26 = this.chart.addLineSeries({
      color: '#f97316',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    // BB Upper
    this._indicatorSeries.bbUpper = this.chart.addLineSeries({
      color: '#a78bfa',
      lineWidth: 1,
      lineStyle: 2, // Dashed
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    // BB Middle
    this._indicatorSeries.bbMiddle = this.chart.addLineSeries({
      color: '#6b7280',
      lineWidth: 1,
      lineStyle: 1, // Dotted
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    // BB Lower
    this._indicatorSeries.bbLower = this.chart.addLineSeries({
      color: '#a78bfa',
      lineWidth: 1,
      lineStyle: 2, // Dashed
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    // Volume（チャート下部30%に背景として表示）
    this.volumeSeries = this.chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });
    this.chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.7, bottom: 0 },
      borderVisible: false,
      visible: false,
    });

    // クロスヘア移動でOHLC情報バーを更新
    this.chart.subscribeCrosshairMove((param) => {
      this._onCrosshairMove(param);
    });

    // RSIサブチャート
    this._createRsiChart();

    // リサイズ対応
    this.resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (this.chart) this.chart.applyOptions({ width, height });
      }
    });
    this.resizeObserver.observe(this.containerEl);
  },

  /** RSIサブチャート作成 */
  _createRsiChart() {
    this.rsiContainerEl = document.getElementById('rsi-container');
    if (!this.rsiContainerEl) return;

    this.rsiChart = LightweightCharts.createChart(this.rsiContainerEl, {
      layout: {
        background: { type: 'solid', color: '#1f2937' },
        textColor: '#9ca3af',
      },
      grid: {
        vertLines: { color: '#374151' },
        horzLines: { color: '#374151' },
      },
      crosshair: { mode: 1 },
      rightPriceScale: {
        borderColor: '#374151',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: '#374151',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // RSI ライン
    this.rsiSeries = this.rsiChart.addLineSeries({
      color: '#facc15',
      lineWidth: 1.5,
      priceLineVisible: false,
      lastValueVisible: true,
    });

    // 70ライン
    this._rsiLine70 = this.rsiChart.addLineSeries({
      color: '#ef4444',
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    // 30ライン
    this._rsiLine30 = this.rsiChart.addLineSeries({
      color: '#22c55e',
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    // RSIリサイズ対応
    this.rsiResizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (this.rsiChart) this.rsiChart.applyOptions({ width, height });
      }
    });
    this.rsiResizeObserver.observe(this.rsiContainerEl);

    // タイムスケール同期（メイン→RSI一方向）
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(
      (range) => {
        if (range !== null && this.rsiChart) {
          this.rsiChart.timeScale().setVisibleLogicalRange(
            range
          );
        }
      }
    );
  },

  /** タイムフレームボタン描画 */
  renderTimeframeButtons() {
    const container = document.getElementById('chart-timeframe-buttons');
    if (!container) return;

    const timeframes = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
    container.innerHTML = timeframes.map((tf) => {
      const active = tf === this.timeframe;
      const cls = active
        ? 'bg-blue-600 text-white'
        : 'bg-gray-700 text-gray-300 hover:bg-gray-600';
      return `<button data-tf="${tf}" class="px-2 py-1 text-xs rounded transition-colors ${cls}">${tf}</button>`;
    }).join('');

    container.querySelectorAll('button[data-tf]').forEach((btn) => {
      btn.addEventListener('click', () => {
        this.timeframe = btn.dataset.tf;
        localStorage.setItem('chart_timeframe', this.timeframe);
        this.renderTimeframeButtons();
        this.fetchCandles();
        this._scheduleRefresh();
      });
    });
  },

  /** 自動更新セレクター描画 */
  renderRefreshSelector() {
    const container = document.getElementById('chart-refresh-selector');
    if (!container) return;

    const options = [
      { label: 'OFF', ms: 0 },
      { label: '1s', ms: 1000 },
      { label: '5s', ms: 5000 },
      { label: '10s', ms: 10000 },
      { label: '30s', ms: 30000 },
    ];

    container.innerHTML = options.map((o) => {
      const active = o.ms === this._refreshMs;
      const cls = active
        ? 'bg-cyan-700 text-cyan-200'
        : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200';
      return `<button data-ms="${o.ms}" class="px-2 py-1 text-xs rounded transition-colors ${cls}">${o.label}</button>`;
    }).join('');

    container.querySelectorAll('button[data-ms]').forEach((btn) => {
      btn.addEventListener('click', () => {
        this._refreshMs = parseInt(btn.dataset.ms, 10);
        localStorage.setItem('chart_refresh_ms', this._refreshMs);
        this.renderRefreshSelector();
        this._scheduleRefresh();
      });
    });
  },

  /** 指標切り替えボタン描画 */
  renderIndicatorToggles() {
    const container = document.getElementById('chart-indicator-toggles');
    if (!container) return;

    const toggles = [
      { key: 'ema', label: 'EMA', color: '#60a5fa' },
      { key: 'bb', label: 'BB', color: '#a78bfa' },
      { key: 'rsi', label: 'RSI', color: '#facc15' },
    ];

    container.innerHTML = toggles.map(({ key, label, color }) => {
      const active = this._indVisible[key];
      const cls = active
        ? 'ring-1 ring-white/30 opacity-100'
        : 'opacity-40';
      return `<button data-ind="${key}"
        class="px-2 py-1 text-xs rounded transition-all ${cls}"
        style="background:${color}22; color:${color}; border:1px solid ${color}55"
      >${label}</button>`;
    }).join('');

    container.querySelectorAll('button[data-ind]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.ind;
        this._indVisible[key] = !this._indVisible[key];
        this._applyIndicatorVisibility();
        this.renderIndicatorToggles();
      });
    });
  },

  /** 指標の表示/非表示を適用 */
  _applyIndicatorVisibility() {
    const showEma = this._indVisible.ema;
    const showBb = this._indVisible.bb;
    const showRsi = this._indVisible.rsi;

    if (this._indicatorSeries.ema12) {
      this._indicatorSeries.ema12.applyOptions({ visible: showEma });
    }
    if (this._indicatorSeries.ema26) {
      this._indicatorSeries.ema26.applyOptions({ visible: showEma });
    }
    if (this._indicatorSeries.bbUpper) {
      this._indicatorSeries.bbUpper.applyOptions({ visible: showBb });
    }
    if (this._indicatorSeries.bbMiddle) {
      this._indicatorSeries.bbMiddle.applyOptions({ visible: showBb });
    }
    if (this._indicatorSeries.bbLower) {
      this._indicatorSeries.bbLower.applyOptions({ visible: showBb });
    }

    // RSIサブチャートの表示/非表示
    if (this.rsiContainerEl) {
      this.rsiContainerEl.parentElement.classList.toggle(
        'hidden', !showRsi
      );
    }
  },

  /** クロスヘア移動時にOHLC情報バーを更新 */
  _onCrosshairMove(param) {
    const bar = document.getElementById('chart-ohlc-bar');
    if (!bar) return;

    if (!param || !param.time || !param.seriesData) {
      bar.classList.add('invisible');
      return;
    }

    const candleData = param.seriesData.get(this.candleSeries);
    const volData = this.volumeSeries
      ? param.seriesData.get(this.volumeSeries)
      : null;

    if (!candleData) {
      bar.classList.add('invisible');
      return;
    }

    bar.classList.remove('invisible');

    const { open, high, low, close } = candleData;
    const change = close - open;
    const changePct = open !== 0 ? (change / open) * 100 : 0;
    const isUp = close >= open;
    const prec = this._getPricePrecision();
    const fmt = (v) => v.toFixed(prec);

    document.getElementById('chart-ohlc-o').textContent = fmt(open);
    document.getElementById('chart-ohlc-h').textContent = fmt(high);
    document.getElementById('chart-ohlc-l').textContent = fmt(low);

    const closeEl = document.getElementById('chart-ohlc-c');
    if (closeEl) {
      closeEl.textContent = fmt(close);
      closeEl.className = isUp ? 'text-green-400 ml-0.5' : 'text-red-400 ml-0.5';
    }

    const chgEl = document.getElementById('chart-ohlc-chg');
    if (chgEl) {
      const sign = isUp ? '+' : '';
      chgEl.textContent = `${sign}${change.toFixed(prec)} (${sign}${changePct.toFixed(2)}%)`;
      chgEl.className = 'font-medium ' + (isUp ? 'text-green-400' : 'text-red-400');
    }

    const volEl = document.getElementById('chart-ohlc-vol');
    if (volEl) {
      volEl.textContent = volData ? Math.round(volData.value).toLocaleString() : '-';
    }
  },

  /** シンボルに応じた価格の小数点桁数 */
  _getPricePrecision() {
    return this.symbol && this.symbol.includes('JPY') ? 3 : 5;
  },

  /** 自動更新スケジュール */
  _scheduleRefresh() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
    if (this._refreshMs > 0) {
      this._refreshTimer = setInterval(
        () => this.fetchLatestCandle(),
        this._refreshMs
      );
    }
  },

  /** 最新ローソク足だけ軽量更新（差分更新） */
  async fetchLatestCandle() {
    if (this.isLoading || !this.candleSeries) return;
    try {
      const data = await getCandles(this.symbol, this.timeframe, 2);
      if (!data || data.length === 0) return;

      const latest = data[data.length - 1];
      const latestTime = Math.floor(
        new Date(latest.time).getTime() / 1000
      );

      try {
        this.candleSeries.update({
          time: latestTime,
          open: latest.open,
          high: latest.high,
          low: latest.low,
          close: latest.close,
        });
        if (this.volumeSeries) {
          this.volumeSeries.update({
            time: latestTime,
            value: latest.volume || 0,
            color: latest.close >= latest.open ? '#22c55e28' : '#ef444428',
          });
        }
      } catch (_updateErr) {
        await this.fetchCandles();
      }
    } catch (e) {
      // ネットワークエラー等は無視
    }
  },

  /** ローソク足取得（全件） */
  async fetchCandles() {
    this.isLoading = true;
    this.showLoading(true);
    try {
      const [candleData, indData] = await Promise.allSettled([
        getCandles(this.symbol, this.timeframe, 500),
        getIndicatorSeries(this.symbol, this.timeframe, 500),
      ]);
      this.updateData(
        candleData.status === 'fulfilled' ? candleData.value : []
      );
      if (indData.status === 'fulfilled') {
        this._updateIndicators(indData.value);
      }
    } catch (e) {
      this.updateData([]);
    } finally {
      this.isLoading = false;
      this.showLoading(false);
    }
  },

  /** データ更新（全件セット） */
  updateData(candles) {
    if (!this.candleSeries) return;

    const chartData = candles.map((c) => ({
      time: new Date(c.time).getTime() / 1000,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    this.candleSeries.setData(chartData);

    // ボリューム
    if (this.volumeSeries) {
      const volData = candles.map((c) => ({
        time: new Date(c.time).getTime() / 1000,
        value: c.volume || 0,
        color: c.close >= c.open ? '#22c55e28' : '#ef444428',
      }));
      this.volumeSeries.setData(volData);
    }

    // シグナルマーカー
    if (this.signals && this.signals.length > 0) {
      const markers = this.signals
        .filter((s) => s.timeframe === this.timeframe && s.signal_type !== 'HOLD')
        .map((s) => ({
          time: new Date(s.created_at).getTime() / 1000,
          position: s.signal_type === 'BUY' ? 'belowBar' : 'aboveBar',
          color: s.signal_type === 'BUY' ? '#22c55e' : '#ef4444',
          shape: s.signal_type === 'BUY' ? 'arrowUp' : 'arrowDown',
          text: s.signal_type + ' ' + (s.confidence * 100).toFixed(0) + '%',
        }));
      this.candleSeries.setMarkers(markers);
    }
  },

  /** 指標時系列を更新 */
  _updateIndicators(data) {
    if (!data) return;

    const toPoints = (arr) =>
      (arr || []).map((p) => ({ time: p.time, value: p.value }));

    if (this._indicatorSeries.ema12) {
      this._indicatorSeries.ema12.setData(toPoints(data.ema12));
    }
    if (this._indicatorSeries.ema26) {
      this._indicatorSeries.ema26.setData(toPoints(data.ema26));
    }
    if (this._indicatorSeries.bbUpper) {
      this._indicatorSeries.bbUpper.setData(toPoints(data.bb_upper));
    }
    if (this._indicatorSeries.bbMiddle) {
      this._indicatorSeries.bbMiddle.setData(toPoints(data.bb_middle));
    }
    if (this._indicatorSeries.bbLower) {
      this._indicatorSeries.bbLower.setData(toPoints(data.bb_lower));
    }

    // RSIサブチャート
    if (this.rsiSeries && data.rsi && data.rsi.length > 0) {
      const rsiPoints = toPoints(data.rsi);
      this.rsiSeries.setData(rsiPoints);

      // 70/30 ライン（RSIデータと同じ時間範囲）
      const line70 = rsiPoints.map((p) => ({ time: p.time, value: 70 }));
      const line30 = rsiPoints.map((p) => ({ time: p.time, value: 30 }));
      if (this._rsiLine70) this._rsiLine70.setData(line70);
      if (this._rsiLine30) this._rsiLine30.setData(line30);
    }

    this._applyIndicatorVisibility();
  },

  /** シグナル設定 */
  setSignals(signals) {
    this.signals = signals;
  },

  /** シンボル変更 */
  setSymbol(symbol) {
    this.symbol = symbol;
    this.fetchCandles();
    this._scheduleRefresh();
  },

  /** ローディング表示 */
  showLoading(show) {
    const loader = document.getElementById('chart-loader');
    if (loader) {
      loader.classList.toggle('hidden', !show);
    }
  },

  /** 破棄 */
  destroy() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }
    if (this.rsiResizeObserver) {
      this.rsiResizeObserver.disconnect();
    }
    if (this.chart) {
      this.chart.remove();
      this.chart = null;
      this.candleSeries = null;
      this.volumeSeries = null;
    }
    if (this.rsiChart) {
      this.rsiChart.remove();
      this.rsiChart = null;
      this.rsiSeries = null;
    }
  },
};
