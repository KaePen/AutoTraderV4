/** APIクライアント */

const API_BASE = '/api/v1';

/** API呼び出し共通関数 */
async function fetchApi(endpoint, options) {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  if (!response.ok) {
    throw new Error(`API Error: ${response.status} ${response.statusText}`);
  }

  const json = await response.json();

  if (!json.success) {
    throw new Error(json.error || 'Unknown error');
  }

  return json.data;
}

/** nullを許容するAPI呼び出し */
async function fetchApiNullable(endpoint, options) {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  if (!response.ok) {
    throw new Error(`API Error: ${response.status} ${response.statusText}`);
  }

  const json = await response.json();
  if (!json.success) {
    throw new Error(json.error || 'Unknown error');
  }
  return json.data;
}

/** ヘルスチェック */
function getHealth() {
  return fetchApi('/health');
}

/** ダッシュボード取得 */
function getDashboard() {
  return fetchApi('/dashboard');
}

/** シグナル履歴取得 */
function getSignalHistory(symbol, limit, offset) {
  symbol = symbol || 'USDJPY';
  limit = limit || 50;
  offset = offset || 0;
  return fetchApi(`/signals/history?symbol=${symbol}&limit=${limit}&offset=${offset}`);
}

/** ポジション取得 */
function getPositions(symbol) {
  const query = symbol ? `?symbol=${symbol}` : '';
  return fetchApi(`/positions${query}`);
}

/** トレード履歴取得 */
function getTrades(symbol, limit, offset) {
  const params = new URLSearchParams();
  if (symbol) params.append('symbol', symbol);
  params.append('limit', String(limit || 50));
  params.append('offset', String(offset || 0));
  return fetchApi(`/trades?${params.toString()}`);
}

/** トレードサマリー取得 */
function getTradeSummary(symbol, days) {
  const params = new URLSearchParams();
  if (symbol) params.append('symbol', symbol);
  params.append('days', String(days || 30));
  return fetchApi(`/trades/summary?${params.toString()}`);
}

/** ローソク足取得 */
function getCandles(symbol, timeframe, limit, endTime) {
  limit = limit || 200;
  let url = `/candles/${symbol}/${timeframe}?limit=${limit}`;
  if (endTime) {
    url += `&end_time=${encodeURIComponent(endTime)}`;
  }
  return fetchApi(url);
}

/** 設定取得 */
function getSettings() {
  return fetchApi('/settings');
}

/** 設定更新 */
function updateSettings(settings) {
  return fetchApi('/settings', {
    method: 'PUT',
    body: JSON.stringify(settings),
  });
}

/** トレーディングモード取得 */
function getTradingMode() {
  return fetchApi('/trading/mode');
}

/** MT5接続状態取得 */
function getMT5Status() {
  return fetchApi('/trading/mt5/status');
}

/** MT5接続 */
function connectMT5() {
  return fetchApi('/trading/mt5/connect', { method: 'POST' });
}

/** MT5切断 */
function disconnectMT5() {
  return fetchApi('/trading/mt5/disconnect', { method: 'POST' });
}

/** 自動取引ON/OFF */
function toggleAutoTrade(enable) {
  return fetchApi(
    `/trading/auto-trade?enable=${enable}`, { method: 'POST' }
  );
}

/** 直近tick分析状態取得 */
function getAnalysis(symbol) {
  const q = symbol ? `?symbol=${encodeURIComponent(symbol)}` : '';
  return fetchApi(`/signals/analysis${q}`);
}

/** シンボルごとの自動取引ON/OFF */
function toggleSymbolAutoTrade(symbol, enable) {
  return fetchApi(
    `/trading/symbol-auto-trade?symbol=${encodeURIComponent(symbol)}&enable=${enable}`,
    { method: 'POST' }
  );
}

/** シンボルごとのデモモードON/OFF */
function toggleSymbolDemoMode(symbol, enable) {
  return fetchApi(
    `/trading/symbol-demo-mode?symbol=${encodeURIComponent(symbol)}&enable=${enable}`,
    { method: 'POST' }
  );
}

/** 口座切替 */
function switchAccount(login, password, server) {
  return fetchApi('/trading/mt5/switch-account', {
    method: 'POST',
    body: JSON.stringify({ login, password: password || '', server }),
  });
}

/** 口座プリセット一覧取得 */
function getAccountPresets() {
  return fetchApi('/trading/accounts');
}

/** 口座プリセット登録/更新 */
function addAccountPreset(login, server, name) {
  return fetchApi('/trading/accounts', {
    method: 'POST',
    body: JSON.stringify({ login, server, name: name || '' }),
  });
}

/** シンボルのエンジンを確保（なければ追加） */
function ensureSymbolEngine(symbol) {
  return fetchApi(
    `/trading/symbols/add?symbol=${encodeURIComponent(symbol)}`,
    { method: 'POST' }
  );
}

/** 口座プリセット削除 */
function deleteAccountPreset(login) {
  return fetchApi(`/trading/accounts/${login}`, { method: 'DELETE' });
}

/** プリセットから口座切替（パスワード不要） */
function switchAccountPreset(login, server) {
  return fetchApi('/trading/mt5/switch-account', {
    method: 'POST',
    body: JSON.stringify({ login, password: '', server }),
  });
}

/** ファンダメンタルニュース取得 */
function getFundamentalNews(symbol, limit) {
  symbol = symbol || 'USDJPY';
  limit = limit || 30;
  return fetchApi(`/fundamental/news?symbol=${symbol}&limit=${limit}`);
}

/** 経済カレンダー取得 */
function getFundamentalCalendar(symbol, days) {
  symbol = symbol || 'USDJPY';
  days = days || 2;
  return fetchApi(`/fundamental/calendar?symbol=${symbol}&days=${days}`);
}
