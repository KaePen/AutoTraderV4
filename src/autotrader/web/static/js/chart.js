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
  _trades: [],
  _tradeLines: [],        // トレードライン LineSeries のリスト
  _openTradeLineRefs: [], // オープン中ポジションのラインシリーズ（tick更新用）

  isLoading: false,
  resizeObserver: null,
  rsiResizeObserver: null,
  // 最新バーキャッシュ（price_update高速更新用）
  _lastBarData: null,
  // 指標シリーズ（オーバーレイ）
  _indicatorSeries: {
    ema12: null,
    ema26: null,
    ema50: null,
    ema200: null,
    bbUpper: null,
    bbMiddle: null,
    bbLower: null,
    vwap: null,
  },
  // 指標表示ON/OFF状態
  _indVisible: {
    ema: true,
    bb: true,
    rsi: true,
    ema50: false,
    ema200: false,
    vwap: false,
  },

  /** 初期化 */
  init(containerId, symbol) {
    this.containerEl = document.getElementById(containerId);
    if (!this.containerEl) return;
    this.symbol = symbol || 'USDJPY';

    // localStorageから設定を復元
    const savedTf = localStorage.getItem('chart_timeframe');
    if (savedTf) this.timeframe = savedTf;
    this.createChart();
    this.fetchCandles();
    this.renderTimeframeButtons();
    this.renderIndicatorToggles();
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
        tickMarkFormatter: (time, type) => this._jstTickMarkFormatter(time, type),
      },
      localization: {
        timeFormatter: (time) => {
          const d = new Date(time * 1000 + 7 * 3600 * 1000);
          const Y = d.getUTCFullYear();
          const M = String(d.getUTCMonth() + 1).padStart(2, '0');
          const D = String(d.getUTCDate()).padStart(2, '0');
          const h = String(d.getUTCHours()).padStart(2, '0');
          const mi = String(d.getUTCMinutes()).padStart(2, '0');
          return `${Y}/${M}/${D} ${h}:${mi} JST`;
        },
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

    // EMA(50)
    this._indicatorSeries.ema50 = this.chart.addLineSeries({
      color: '#22d3ee',
      lineWidth: 1.5,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      visible: false,
    });

    // EMA(200)
    this._indicatorSeries.ema200 = this.chart.addLineSeries({
      color: '#e2e8f0',
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      visible: false,
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

    // VWAP
    this._indicatorSeries.vwap = this.chart.addLineSeries({
      color: '#4ade80',
      lineWidth: 1.5,
      lineStyle: 0,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      visible: false,
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
        tickMarkFormatter: (time, type) => this._jstTickMarkFormatter(time, type),
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

    const timeframes = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'H8', 'D1'];
    container.innerHTML = timeframes.map((tf) => {
      const active = tf === this.timeframe;
      const cls = active
        ? 'bg-blue-600 text-white'
        : 'bg-gray-700 text-gray-300 hover:bg-gray-600';
      return `<button data-tf="${tf}" class="w-9 py-1 text-xs text-center rounded transition-colors ${cls}">${tf}</button>`;
    }).join('');

    container.querySelectorAll('button[data-tf]').forEach((btn) => {
      btn.addEventListener('click', () => {
        this.timeframe = btn.dataset.tf;
        localStorage.setItem('chart_timeframe', this.timeframe);
        this.renderTimeframeButtons();
        this.fetchCandles();
      });
    });
  },

  /** 指標切り替えボタン描画 */
  renderIndicatorToggles() {
    const container = document.getElementById('chart-indicator-toggles');
    if (!container) return;

    const toggles = [
      { key: 'ema', label: 'EMA', color: '#60a5fa' },
      { key: 'ema50', label: 'E50', color: '#22d3ee' },
      { key: 'ema200', label: 'E200', color: '#e2e8f0' },
      { key: 'bb', label: 'BB', color: '#a78bfa' },
      { key: 'vwap', label: 'VWAP', color: '#4ade80' },
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
    const showEma50 = this._indVisible.ema50;
    const showEma200 = this._indVisible.ema200;
    const showVwap = this._indVisible.vwap;

    if (this._indicatorSeries.ema12) {
      this._indicatorSeries.ema12.applyOptions({ visible: showEma });
    }
    if (this._indicatorSeries.ema26) {
      this._indicatorSeries.ema26.applyOptions({ visible: showEma });
    }
    if (this._indicatorSeries.ema50) {
      this._indicatorSeries.ema50.applyOptions({ visible: showEma50 });
    }
    if (this._indicatorSeries.ema200) {
      this._indicatorSeries.ema200.applyOptions({ visible: showEma200 });
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
    if (this._indicatorSeries.vwap) {
      this._indicatorSeries.vwap.applyOptions({ visible: showVwap });
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

  /**
   * 時間軸目盛りをJST（UTC+9）で返すフォーマッター
   * LightweightCharts v4 の tickMarkFormatter コールバック
   *
   * @param {number} time - UNIX秒タイムスタンプ
   * @param {number} type - TickMarkType (0=Year,1=Month,2=Day,3=Time,4=TimeWithSec)
   * @returns {string} フォーマット済み文字列
   */
  _jstTickMarkFormatter(time, type) {
    // MT5ブローカーはGMT+2でタイムスタンプを記録するため -2h 補正
    // 正しいJST = GMT+2データ -2h +9h(JST) = +7h
    const d = new Date(time * 1000 + 7 * 3600 * 1000);
    const Y = d.getUTCFullYear();
    const M = String(d.getUTCMonth() + 1).padStart(2, '0');
    const D = String(d.getUTCDate()).padStart(2, '0');
    const h = String(d.getUTCHours()).padStart(2, '0');
    const mi = String(d.getUTCMinutes()).padStart(2, '0');
    switch (type) {
      case 0: return String(Y);
      case 1: return `${Y}/${M}`;
      case 2: return `${M}/${D}`;
      case 3: return `${h}:${mi}`;
      case 4: return `${h}:${mi}`;
      default: return `${h}:${mi}`;
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

    // 最新バーをキャッシュ（price_update高速更新用）
    if (chartData.length > 0) {
      this._lastBarData = { ...chartData[chartData.length - 1] };
    }

    // ボリューム
    if (this.volumeSeries) {
      const volData = candles.map((c) => ({
        time: new Date(c.time).getTime() / 1000,
        value: c.volume || 0,
        color: c.close >= c.open ? '#22c55e28' : '#ef444428',
      }));
      this.volumeSeries.setData(volData);
    }

    // マーカー再描画（シグナル＋トレード）
    this._applyMarkers();
    // トレードライン再描画（ローソク足データ更新後に必ず呼ぶ）
    this._applyTradeLines();
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
    if (this._indicatorSeries.ema50) {
      this._indicatorSeries.ema50.setData(toPoints(data.ema50));
    }
    if (this._indicatorSeries.ema200) {
      this._indicatorSeries.ema200.setData(toPoints(data.ema200));
    }
    if (this._indicatorSeries.vwap) {
      this._indicatorSeries.vwap.setData(toPoints(data.vwap));
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
    this._applyMarkers();
  },

  /** トレード設定（エントリー/エグジットマーカー更新） */
  setTrades(trades) {
    this._trades = trades || [];
    this._applyMarkers();
    this._applyTradeLines();
  },

  /**
   * シグナルマーカーとトレードマーカーを統合してチャートに描画
   * LightweightCharts の setMarkers は time 昇順が必須
   */
  _applyMarkers() {
    if (!this.candleSeries) return;
    const prec = this._getPricePrecision();
    const markers = [];

    // シグナルマーカー
    if (this.signals && this.signals.length > 0) {
      for (const s of this.signals) {
        if (s.timeframe !== this.timeframe) continue;
        if (s.signal_type === 'HOLD') continue;
        markers.push({
          time: new Date(s.created_at).getTime() / 1000,
          position: s.signal_type === 'BUY' ? 'belowBar' : 'aboveBar',
          color: s.signal_type === 'BUY' ? '#60a5fa' : '#f87171',
          shape: s.signal_type === 'BUY' ? 'arrowUp' : 'arrowDown',
          text: s.signal_type + ' ' + (s.confidence * 100).toFixed(0) + '%',
        });
      }
    }

    // トレードマーカー（エントリー・エグジット）
    if (this._trades && this._trades.length > 0) {
      for (const t of this._trades) {
        if (t.symbol !== this.symbol) continue;

        // エントリーマーカー
        const entryTime = new Date(t.opened_at).getTime() / 1000;
        if (entryTime > 0) {
          const isBuy = t.signal_type === 'BUY';
          markers.push({
            time: entryTime,
            position: isBuy ? 'belowBar' : 'aboveBar',
            color: isBuy ? '#22c55e' : '#ef4444',
            shape: isBuy ? 'arrowUp' : 'arrowDown',
          });
        }

        // エグジットマーカー（クローズ済みのみ）
        if (!t.is_open && t.closed_at && t.exit_price != null) {
          const exitTime = new Date(t.closed_at).getTime() / 1000;
          const isProfit = (t.profit_loss || 0) >= 0;
          markers.push({
            time: exitTime,
            position: t.signal_type === 'BUY' ? 'aboveBar' : 'belowBar',
            color: isProfit ? '#4ade80' : '#f87171',
            shape: 'circle',
          });
        }
      }
    }

    // time 昇順ソート（setMarkers の要件）
    markers.sort((a, b) => a.time - b.time);
    this.candleSeries.setMarkers(markers);
  },

  /**
   * エントリー→エグジット（またはエントリー→現在価格）の破線を描画
   * LineSeries を各トレードごとに生成し、_tradeLines に保持する
   */
  _applyTradeLines() {
    if (!this.chart) return;

    // 既存ラインを全削除
    for (const series of this._tradeLines) {
      try { this.chart.removeSeries(series); } catch (_e) {}
    }
    this._tradeLines = [];
    this._openTradeLineRefs = [];

    if (!this._trades || this._trades.length === 0) return;

    for (const t of this._trades) {
      if (t.symbol !== this.symbol) continue;

      const entryTime = new Date(t.opened_at).getTime() / 1000;
      if (entryTime <= 0) continue;

      let exitTime, exitPrice;

      if (t.is_open) {
        // オープン中：最新バーの時刻・価格まで延ばす
        if (!this._lastBarData) continue;
        exitTime = this._lastBarData.time;
        exitPrice = this._lastBarData.close;
      } else {
        if (!t.closed_at || t.exit_price == null) continue;
        exitTime = new Date(t.closed_at).getTime() / 1000;
        exitPrice = t.exit_price;
      }

      // 同一バー内は破線描画不可（チャート破損を防ぐ）
      const TF_SECONDS = {
        M1: 60, M5: 300, M15: 900, M30: 1800,
        H1: 3600, H4: 14400, D1: 86400,
      };
      const tfSec = TF_SECONDS[this.timeframe] || 900;
      const entryBar = Math.floor(entryTime / tfSec) * tfSec;
      const exitBar = Math.floor(exitTime / tfSec) * tfSec;
      if (entryBar >= exitBar) continue;

      const isBuy = t.signal_type === 'BUY';
      let lineColor;
      if (t.is_open) {
        lineColor = isBuy ? '#22c55eaa' : '#ef4444aa';
      } else {
        const isProfit = (t.profit_loss || 0) >= 0;
        lineColor = isProfit ? '#4ade80aa' : '#f87171aa';
      }

      const series = this.chart.addLineSeries({
        color: lineColor,
        lineWidth: 1,
        lineStyle: 2, // Dashed（破線）
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });

      series.setData([
        { time: entryTime, value: t.entry_price },
        { time: exitTime, value: exitPrice },
      ]);

      this._tradeLines.push(series);
      if (t.is_open) {
        this._openTradeLineRefs.push(series);
      }
    }
  },

  /**
   * MT5のtick price_updateで最新バーのcloseをリアルタイム更新
   * ローソク足APIを呼ばずに高速でチャートを更新する。
   *
   * @param {number} bid - MT5のbid価格
   */
  updateLastBar(bid) {
    if (!this.candleSeries || !this._lastBarData || bid <= 0) return;
    const updated = {
      ...this._lastBarData,
      close: bid,
      high: Math.max(this._lastBarData.high, bid),
      low: Math.min(this._lastBarData.low, bid),
    };
    try {
      this.candleSeries.update(updated);
      this._lastBarData = updated;
      // オープン中ポジションの線を現在価格まで延ばす
      for (const series of this._openTradeLineRefs) {
        try { series.update({ time: updated.time, value: bid }); } catch (_e2) {}
      }
    } catch (_e) {
      // チャート未準備時は無視
    }
  },

  /** シンボル変更 */
  setSymbol(symbol) {
    this.symbol = symbol;
    this.fetchCandles();
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
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }
    if (this.rsiResizeObserver) {
      this.rsiResizeObserver.disconnect();
    }
    if (this.chart) {
      // トレードラインは chart.remove() で一括破棄される
      this._tradeLines = [];
      this._openTradeLineRefs = [];
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
