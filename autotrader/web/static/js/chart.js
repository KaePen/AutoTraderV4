/** チャートモジュール */

const ChartManager = {
  chart: null,
  candleSeries: null,
  volumeSeries: null,
  containerEl: null,
  timeframe: 'M15',
  symbol: 'USDJPY',
  _trades: [],
  _tradeLines: [],        // トレードライン LineSeries のリスト
  _openTradeLineRefs: [], // オープン中ポジションのラインシリーズ（tick更新用）
  _seriesMarkers: null,   // createSeriesMarkers プリミティブ（v5）

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

    // マーカー再描画
    this._applyMarkers();
    // トレードライン再描画
    this._applyTradeLines();
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

  /**
   * DBのUTC日時文字列をチャート時刻（MT5 GMT+2基準のUNIX秒）に変換
   * MT5ブローカーはGMT+2でローソク足を記録するため、
   * チャートのバー時刻と一致させるには UTC+2h が必要
   *
   * @param {string} dateStr - ISO 8601 日時文字列（UTC）
   * @returns {number} チャート時刻（整数 UNIX秒）
   */
  _toChartTime(dateStr) {
    return Math.floor(new Date(dateStr).getTime() / 1000) + 2 * 3600;
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

  /** トレード設定（エントリー/エグジットマーカー更新） */
  setTrades(trades) {
    this._trades = trades || [];
    this._applyMarkers();
    this._applyTradeLines();
  },

  /**
   * トレードマーカーをチャートに描画
   * v5: createSeriesMarkers プリミティブで管理
   * エントリー/エグジット価格位置に正確に表示
   */
  _applyMarkers() {
    if (!this.candleSeries) return;
    const markers = [];

    // トレードマーカー（atPriceMiddle + price で正確な価格位置に表示）
    if (this._trades && this._trades.length > 0) {
      for (const t of this._trades) {
        if (t.symbol !== this.symbol) continue;

        const isBuy = t.signal_type === 'BUY';
        const entryTime = this._toChartTime(t.opened_at);

        // エントリーマーカー
        // 買い: ▲（arrowUp）緑 / 売り: ●（circle）赤
        if (entryTime > 0 && t.entry_price != null) {
          markers.push({
            time: entryTime,
            position: 'atPriceMiddle',
            price: t.entry_price,
            color: isBuy ? '#22c55e' : '#ef4444',
            shape: isBuy ? 'arrowUp' : 'circle',
          });
        }

        // エグジットマーカー（クローズ済みのみ）
        // 買い: ▼（arrowDown）損益色 / 売り: ■（square）損益色
        if (!t.is_open && t.closed_at && t.exit_price != null) {
          const exitTime = this._toChartTime(t.closed_at);
          const isProfit = (t.profit_loss || 0) >= 0;
          markers.push({
            time: exitTime,
            position: 'atPriceMiddle',
            price: t.exit_price,
            color: isProfit ? '#4ade80' : '#f87171',
            shape: isBuy ? 'arrowDown' : 'square',
          });
        }
      }
    }

    // time 昇順ソート（setMarkers の要件）
    markers.sort((a, b) => a.time - b.time);

    // v5: createSeriesMarkers プリミティブで管理
    if (!this._seriesMarkers) {
      this._seriesMarkers = LightweightCharts.createSeriesMarkers(
        this.candleSeries, markers
      );
    } else {
      this._seriesMarkers.setMarkers(markers);
    }
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

      const entryTime = this._toChartTime(t.opened_at);
      if (entryTime <= 0) continue;

      let exitTime, exitPrice;

      if (t.is_open) {
        // オープン中：最新バーの時刻・価格まで延ばす
        if (!this._lastBarData) continue;
        exitTime = this._lastBarData.time;
        exitPrice = this._lastBarData.close;
      } else {
        if (!t.closed_at || t.exit_price == null) continue;
        exitTime = this._toChartTime(t.closed_at);
        exitPrice = t.exit_price;
      }

      // 同一バー内は破線描画不可（チャート破損を防ぐ）
      // time はバー開始時刻（整数）に丸めて使う
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

      const series = this.chart.addSeries(LightweightCharts.LineSeries, {
        color: lineColor,
        lineWidth: 1,
        lineStyle: 2, // Dashed（破線）
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });

      // バー開始時刻（整数）を使うことでチャート時刻と一致させ崩れを防ぐ
      series.setData([
        { time: entryBar, value: t.entry_price },
        { time: exitBar, value: exitPrice },
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
    if (this.symbol === symbol) return;
    this.symbol = symbol;

    // 旧シンボルのキャッシュをクリア
    this._lastBarData = null;
    this._trades = [];

    // 旧トレードラインを即座に削除
    if (this.chart) {
      for (const series of this._tradeLines) {
        try { this.chart.removeSeries(series); } catch (_e) {}
      }
    }
    this._tradeLines = [];
    this._openTradeLineRefs = [];

    // 旧マーカーをクリア
    if (this._seriesMarkers) {
      this._seriesMarkers.setMarkers([]);
    }

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
      // トレードライン・マーカーは chart.remove() で一括破棄される
      this._tradeLines = [];
      this._openTradeLineRefs = [];
      this._seriesMarkers = null;
      this.chart.remove();
      this.chart = null;
      this.candleSeries = null;
      this.volumeSeries = null;
    }
  },
};
