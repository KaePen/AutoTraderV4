/** チャートモジュール */

const ChartManager = {
  chart: null,
  candleSeries: null,
  volumeSeries: null,
  containerEl: null,
  timeframe: 'M15',
  symbol: 'USDJPY',
  isLoading: false,
  resizeObserver: null,
  // 最新バーキャッシュ（price_update高速更新用）
  _lastBarData: null,
  // 遅延読み込み用状態
  _rawCandles: [],         // APIレスポンス形式の全ローソク足
  _isLoadingMore: false,   // 追加読み込み中フラグ
  _hasMoreData: true,      // 過去データがまだある
  _loadBatchSize: 500,     // 1回の取得本数
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

    this.candleSeries = this.chart.addSeries(LightweightCharts.CandlestickSeries, {
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

    // Volume（チャート下部30%に背景として表示）
    this.volumeSeries = this.chart.addSeries(LightweightCharts.HistogramSeries, {
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

    // スクロール時の遅延読み込み
    this._setupLazyLoading();

    // リサイズ対応
    this.resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (this.chart) this.chart.applyOptions({ width, height });
      }
    });
    this.resizeObserver.observe(this.containerEl);
  },

  /**
   * スクロール時の遅延読み込みを設定
   * 表示範囲の左端がデータ開始付近に達したら自動で過去データを取得
   */
  _setupLazyLoading() {
    if (!this.chart) return;
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(
      (range) => {
        if (!range) return;
        if (this._isLoadingMore || !this._hasMoreData) return;
        if (this.isLoading) return;
        // 左端から10本以内に近づいたら追加読み込み
        if (range.from < 10) {
          this._loadOlderCandles();
        }
      }
    );
  },

  /**
   * 過去のローソク足データを追加読み込み
   * 現在の最古データより前のデータをAPIから取得してマージ
   */
  async _loadOlderCandles() {
    if (this._isLoadingMore || !this._hasMoreData) return;
    if (this._rawCandles.length === 0) return;
    this._isLoadingMore = true;

    // await中にTF/シンボルが変更された場合の検知用
    const symbolBefore = this.symbol;
    const tfBefore = this.timeframe;

    try {
      // 最古のローソク足の時刻をend_timeとして送信
      const endTime = this._rawCandles[0].time;
      const older = await getCandles(
        this.symbol, this.timeframe,
        this._loadBatchSize, endTime
      );

      // TF/シンボルが変更されていたら結果を捨てる
      if (this.symbol !== symbolBefore || this.timeframe !== tfBefore) {
        return;
      }

      if (!older || older.length === 0) {
        this._hasMoreData = false;
        return;
      }

      // 取得本数がバッチサイズ未満ならこれ以上過去データなし
      if (older.length < this._loadBatchSize) {
        this._hasMoreData = false;
      }

      // 重複排除（UNIX秒に正規化して比較）
      const existingTimes = new Set(
        this._rawCandles.map((c) => new Date(c.time).getTime())
      );
      const newCandles = older.filter(
        (c) => !existingTimes.has(new Date(c.time).getTime())
      );

      if (newCandles.length === 0) {
        return;
      }

      this._rawCandles = [...newCandles, ...this._rawCandles];
      this._renderAllData(newCandles.length);
    } catch (_e) {
      // 読み込み失敗時は次回スクロールで再試行
    } finally {
      this._isLoadingMore = false;
    }
  },

  /**
   * _rawCandles から全シリーズデータを再描画
   * prependedCount > 0 の場合、表示範囲を追加分だけシフトして保持
   *
   * @param {number} prependedCount - 先頭に追加されたローソク足数
   */
  _renderAllData(prependedCount = 0) {
    if (!this.candleSeries) return;

    // 現在の表示範囲を保存（追加読み込み時のみ）
    let savedRange = null;
    if (prependedCount > 0) {
      try {
        savedRange = this.chart.timeScale().getVisibleLogicalRange();
      } catch (_e) {
        // 取得失敗時は復元しない
      }
    }

    const chartData = this._rawCandles.map((c) => ({
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
      const volData = this._rawCandles.map((c) => ({
        time: new Date(c.time).getTime() / 1000,
        value: c.volume || 0,
        color: c.close >= c.open ? '#22c55e28' : '#ef444428',
      }));
      this.volumeSeries.setData(volData);
    }

    // 表示範囲を復元（追加分だけシフト）
    if (savedRange && prependedCount > 0) {
      this.chart.timeScale().setVisibleLogicalRange({
        from: savedRange.from + prependedCount,
        to: savedRange.to + prependedCount,
      });
    }

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
        : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-gray-200';
      return `<button data-tf="${tf}" class="px-2 py-0.5 text-[11px] font-medium text-center rounded transition-colors ${cls}">${tf}</button>`;
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

  /** ローソク足取得（初回・TF切替時） */
  async fetchCandles() {
    this.isLoading = true;
    this.showLoading(true);
    // 遅延読み込み状態をリセット
    this._rawCandles = [];
    this._hasMoreData = true;
    this._isLoadingMore = false;
    try {
      const candles = await getCandles(
        this.symbol, this.timeframe, this._loadBatchSize
      );
      this._rawCandles = candles || [];
      // 取得本数がバッチサイズ未満なら過去データなし
      if (this._rawCandles.length < this._loadBatchSize) {
        this._hasMoreData = false;
      }
      this.updateData(this._rawCandles);
    } catch (e) {
      this._rawCandles = [];
      this._hasMoreData = false;
      this.updateData([]);
    } finally {
      this.isLoading = false;
      this.showLoading(false);
    }
  },

  /** データ更新（全件セット、初回・定期更新用） */
  updateData(candles) {
    if (!this.candleSeries) return;
    // _rawCandlesが空の場合は初回読み込みデータとして保存
    if (this._rawCandles.length === 0) {
      this._rawCandles = candles;
    }
    this._renderAllData(0);
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
    } catch (_e) {
      // チャート未準備時は無視
    }
  },

  /** シンボル変更 */
  setSymbol(symbol) {
    if (this.symbol === symbol) return;
    this.symbol = symbol;

    // 旧シンボルのキャッシュをクリア
    this._lastBarData = null;

    // priceFormat を新シンボルに合わせて更新
    const prec = this._getPricePrecision();
    if (this.candleSeries) {
      this.candleSeries.applyOptions({
        priceFormat: {
          type: 'price',
          precision: prec,
          minMove: prec === 3 ? 0.001 : 0.00001,
        },
      });
    }

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
    if (this.chart) {
      this.chart.remove();
      this.chart = null;
      this.candleSeries = null;
      this.volumeSeries = null;
    }
  },
};
