# MT5ゲートウェイ + WebUI統合 実装計画

## Context

AutoTraderV4はバックテスト完了済み（PF 3.17, WR 66.8%）だが、
MT5リアルトレード接続が未実装。WebUIの市場データはモック状態。
本計画はMT5との「出入り口」を作成し、WebUIからリアルタイムデータ取得と
トレード実行を可能にする。

**実行環境**: WSL（Linux） → Windows MT5ターミナル間のブリッジが必要。

## アーキテクチャ

```
WebUI (FastAPI + React)
    ↕ REST/WebSocket
LiveTradingEngine (asyncioメインループ)
    ↕ 意思決定層（既存: TradeBot, PM, Sizer）
MT5 Adapter Layer
    ↕ MT5Transport ABC
┌──────────┐  ┌──────────────┐
│ Direct   │  │ Bridge       │
│ Transport│  │ Transport    │
│(Win直接) │  │(WSL→Winソケット)│
└──────────┘  └──────┬───────┘
                     ↕ JSON-RPC over TCP
              BridgeServer (Windows側)
                     ↕ MetaTrader5パッケージ
              MT5 Terminal
```

## 既存の再利用ポイント

| 既存コンポーネント | パス | 再利用方法 |
|---|---|---|
| TradeExecutor ABC | `core/interfaces/trade_executor.py` | MT5TradeExecutorの基底 |
| DataProvider ABC | `core/interfaces/data_provider.py` | MT5DataProviderの基底 |
| Position/Trade/AccountInfo | `core/entities.py` | ticket, SymbolInfo等MT5対応済み |
| UnifiedTradeBot | `decision/unified/trade_bot.py` | シグナル生成そのまま再利用 |
| PositionManager | `decision/unified/position_manager.py` | 出口管理そのまま再利用 |
| PositionSizer | `decision/unified/position_sizer.py` | ロット計算そのまま再利用 |
| LiveTradingState | `web/services/trading_state_service.py` | プレースホルダーを実装に置換 |
| WebSocket handlers | `web/websocket/handlers.py` | broadcast_*関数を再利用 |
| database adapter | `adapters/database/` | パターン参照（DI, contextmanager） |

## Phase 1: MT5アダプタ基盤（6ファイル新規作成）

### 1.1 `src/autotrader/adapters/mt5/__init__.py`
- 公開APIエクスポート

### 1.2 `src/autotrader/adapters/mt5/constants.py`
- Timeframeマッピング（M1→1, H1→16385等）
- 注文タイプ（BUY=0, SELL=1）
- アクション（DEAL=1, SLTP=6）
- リターンコード（DONE=10009等）
- MT5パッケージ不要でブリッジ経由動作可能にするためハードコード

### 1.3 `src/autotrader/adapters/mt5/exceptions.py`
- MT5Error（基底）, MT5ConnectionError, MT5ExecutionError,
  MT5DataError, MT5BridgeError

### 1.4 `src/autotrader/adapters/mt5/config.py`
- `MT5Config(frozen dataclass)`:
  login, password, server, terminal_path,
  transport("direct"|"bridge"), bridge_host/port,
  timeout, magic_number, deviation, symbol,
  retry_count/delay, health_check_interval

### 1.5 `src/autotrader/adapters/mt5/converters.py`
- `mt5_account_to_entity(dict) -> AccountInfo`
- `mt5_symbol_to_entity(dict) -> SymbolInfo`
- `mt5_position_to_entity(dict) -> Position`
- `mt5_rates_to_dataframe(list[dict]) -> pd.DataFrame`
- `signal_to_mt5_request(Signal, volume, tick, config) -> dict`
- 全てイミュータブル（新規オブジェクト返却）

### 1.6 `src/autotrader/adapters/mt5/connection.py`
- `MT5Transport(ABC)`: initialize, login, shutdown,
  account_info, symbol_info, symbol_info_tick,
  copy_rates_from_pos, copy_rates_range,
  order_send, positions_get, history_deals_get
- `DirectTransport(MT5Transport)`: MetaTrader5パッケージ直接呼出
- `BridgeTransport(MT5Transport)`: JSON-RPC over TCPソケット
- `MT5ConnectionManager`: connect/disconnect/health_check/ensure_connected
  - リトライ付き接続、contextmanagerセッション

## Phase 2: DataProvider + TradeExecutor（3ファイル）

### 2.1 `src/autotrader/core/interfaces/trade_executor.py`（既存修正）
- `close_partial(position, volume, reason) -> ExecutionResult` 追加

### 2.2 `src/autotrader/adapters/mt5/data_provider.py`
- `MT5DataProvider(DataProvider)`:
  - get_candles, get_latest_candle, get_spread （ABC実装）
  - get_tick, get_account_info, get_symbol_info （追加メソッド）

### 2.3 `src/autotrader/adapters/mt5/trade_executor.py`
- `MT5TradeExecutor(TradeExecutor)`:
  - open_position, close_position, modify_position,
    get_open_positions （ABC実装）
  - close_partial （追加メソッド）
  - magic_numberフィルタでAutoTraderV4のポジションのみ操作

## Phase 3: ライブエンジン + ブリッジ（5ファイル）

### 3.1 `src/autotrader/live/__init__.py`

### 3.2 `src/autotrader/live/engine.py`
- `LiveTradingEngine`:
  - start/stop（async）
  - _main_loop: check_interval_sec間隔（デフォルト60秒）
  - _tick: 口座情報→ローソク足→シグナル生成→エントリー判定→ポジション管理
  - _execute_entry: PositionSizer→Signal生成→MT5発注→PM登録→DB保存
  - _manage_positions: PM.evaluate()→MT5 SL更新/部分決済/全決済
  - _load_historical_data: 起動時に過去データをTradeBotに供給
  - WebSocketコールバック（on_signal, on_position, on_account）

### 3.3 `src/autotrader/live/config.py`
- `LiveTradingConfig`:
  symbol, check_interval_sec, candle_lookback,
  bot_config(UnifiedBotConfig), mt5_config(MT5Config),
  enable_auto_trade, require_confirmation

### 3.4 `src/autotrader/adapters/mt5/bridge/server.py`（Windows側で実行）
- TCPソケットサーバ（asyncio）
- JSON-RPCプロトコル: {"method": "account_info", "params": {}, "id": 1}
- MetaTrader5パッケージを直接import
- 全MT5Transport ABCメソッドに対応するRPCハンドラ
- スタンドアロン実行可能（`python -m autotrader.adapters.mt5.bridge.server`）

### 3.5 `src/autotrader/adapters/mt5/bridge/protocol.py`
- JSON-RPCリクエスト/レスポンス定義
- メッセージフレーミング（長さプレフィックス+JSON）
- エラーコード定義

## Phase 4: WebUI統合（4ファイル修正/新規）

### 4.1 `src/autotrader/web/services/trading_state_service.py`（既存修正）
- `LiveTradingState`をMT5実データで実装:
  - get_dashboard → MT5 account_info + DBトレード
  - get_positions → MT5 positions_get
  - get_trades → DB TradeRecord
  - get_trade_summary → DB集計

### 4.2 `src/autotrader/web/routers/trading.py`（新規）
- `GET /api/v1/trading/mode` → 現在のモード
- `GET /api/v1/trading/mt5/status` → MT5接続状態
- `POST /api/v1/trading/mt5/connect` → MT5接続開始
- `POST /api/v1/trading/mt5/disconnect` → MT5切断
- `POST /api/v1/trading/auto-trade` → 自動取引ON/OFF

### 4.3 `src/autotrader/web/main.py`（既存修正）
- lifespan内でライブモード判定+エンジン起動
- tradingルーター追加
- app.stateにengine参照保持

### 4.4 `src/autotrader/web/schemas/responses.py`（既存修正）
- `MT5StatusResponse`: connected, account, symbol_info
- `TradingModeResponse`: mode, label, connected

## 実装順序

```
Phase 1 (基盤)
  constants.py → exceptions.py → config.py → converters.py → connection.py

Phase 2 (ABC実装)
  trade_executor.py修正 → data_provider.py → trade_executor.py(MT5)

Phase 3 (エンジン+ブリッジ)
  protocol.py → server.py → config.py → engine.py

Phase 4 (WebUI)
  responses.py修正 → trading_state_service.py修正 → trading.py新規 → main.py修正
```

## 検証方法

### 単体テスト
- `tests/unit/adapters/mt5/test_converters.py` - 変換関数テスト
- `tests/unit/adapters/mt5/test_connection.py` - MockTransportでの接続テスト
- `tests/unit/live/test_engine.py` - MockDataProvider/TradeExecutorでのエンジンテスト

### 結合テスト
1. Windows側でブリッジサーバ起動:
   `python -m autotrader.adapters.mt5.bridge.server`
2. WSL側からブリッジ接続テスト:
   `python -c "from autotrader.adapters.mt5.connection import ...; ..."`
3. WebUI起動してMT5接続:
   `python -m autotrader.web.main`
   → ブラウザで `/api/v1/trading/mt5/status` 確認
4. デモ口座でシグナル生成→注文実行の一連フロー確認

### E2Eフロー
WebUI起動 → MT5接続 → ダッシュボードに実口座残高表示 →
シグナル発生 → 自動注文 → ポジション一覧にリアルタイム表示 →
SL/TP変更 → 決済 → トレード履歴に反映
