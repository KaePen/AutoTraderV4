# WebUI本格実装計画

## Context

AutoTraderV4のWebUIは既にFastAPI+React+TypeScriptの骨格が存在するが、以下の深刻な問題がある：

1. **P0**: バックテストルーターが存在しないフィールド参照でクラッシュ
2. **P1**: スキーマ全体が削除済みの旧`StrategyConfig`を参照（`UnifiedBotConfig`に未対応）
3. **P2**: 全サービスがスタブ（ハードコード値のみ）、データパイプライン未接続

**目標**: リアルトレード対応ダッシュボード（バックテストはおまけ）、機能+デザイン両立

---

## Phase 0: P0バグ修正（バックテスト起動可能化）

### 修正ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/web/routers/backtest.py` | `request.preset`/`consensus`/`min_alignment`参照を削除、`BacktestRequest`に実在フィールドのみ残す |
| `src/autotrader/web/routers/settings.py` | プリセットエンドポイント削除、`StrategyConfig`依存除去 |
| `src/autotrader/web/services/settings_service.py` | `StrategyConfig`インポート・`apply_preset`削除 |
| `frontend/src/components/BacktestPage.tsx` | `PRESETS`配列削除、`preset`送信削除、`console.log`除去 |

### 検証
- `POST /api/v1/backtest/run {"start_year":2023,"end_year":2024}` がクラッシュしない
- `GET /api/v1/settings` が有効なJSONを返す

---

## Phase 1: スキーマ現行化（UnifiedBotConfig対応）

### 修正ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/web/schemas/responses.py` | `StrategyConfigResponse` → `TradingConfigResponse`（`UnifiedBotConfig`フィールド反映）。`presets`フィールド削除 |
| `src/autotrader/web/schemas/requests.py` | `StrategyConfigUpdate` → `TradingConfigUpdate`。`BacktestRequest`に`UnifiedBotConfig`オーバーライド追加 |
| `src/autotrader/web/services/settings_service.py` | `UnifiedBotConfig`からデフォルト値読出し。`PositionManagerConfig`も公開 |
| `src/autotrader/web/routers/settings.py` | 新スキーマ使用。プリセット→削除 |
| `src/autotrader/web/routers/backtest.py` | `BacktestRequest`拡張（`range_day_bbw_threshold`, `use_dynamic_lot`, `base_risk_pct`等をOptionalで追加）。`_run_backtest_sync`で`UnifiedBotConfig`構築 |

### 公開するUnifiedBotConfigパラメータ
```
# エントリーフィルター
range_day_bbw_threshold, range_day_score_premium
weak_hours_enabled, weak_hours_score_premium
tokyo_night_swing_enabled, tokyo_night_swing_premium
tokyo_range_p0_enabled

# 資金管理
use_dynamic_lot, base_risk_pct, max_lot_per_trade
max_total_exposure_lot, equity_floor_pct, slippage_buffer_pips

# ポジション管理
enable_position_manager, stagnation_min_mfe_r
range_day_early_be_r, insurance_trigger_r
```

### 検証
- `GET /api/v1/settings` が`TradingConfigResponse`を返す（`range_day_bbw_threshold: 0.20`等）
- `POST /api/v1/backtest/run`がパラメータオーバーライドを受け付ける

---

## Phase 2: データレイヤー接続

### 修正ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/web/services/market_service.py` | 全スタブ実装。`TradeRepository`/`SignalRepository`からDB読出し。キャンドルはCSVから読出し |
| `src/autotrader/web/services/signal_service.py` | `SignalRepository`接続。シグナル履歴返却 |
| `src/autotrader/web/main.py` | `lifespan`でDB初期化（`Base.metadata.create_all`） |
| `src/autotrader/web/dependencies.py` | DB初期化ロジック整理 |

### 新規ファイル

| ファイル | 内容 |
|---------|------|
| `src/autotrader/web/services/candle_service.py` | CSV読込専用サービス。Polarsで高速読込、LRUキャッシュ |
| `src/autotrader/web/services/backtest_history_service.py` | バックテスト履歴管理。CSVログのDB取込、履歴一覧 |

### 検証
- `GET /api/v1/candles/USDJPY/M15?limit=100` が実データ返却
- バックテスト実行後、`GET /api/v1/trades` がDB上のトレード返却

---

## Phase 3: バックテストパイプライン統合

### 修正ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/web/routers/backtest.py` | `WebUIAdapter`活用。結果をDBに永続化。WebSocketリスナー接続 |
| `src/autotrader/backtest/adapters/webui.py` | 新`BacktestRequest`対応。`UnifiedBotConfig`オーバーライド対応 |
| `src/autotrader/backtest/websocket_listener.py` | イベントマッピング検証・修正 |

### 新規エンドポイント
- `GET /api/v1/backtest/history` - 過去の実行一覧
- `GET /api/v1/backtest/{id}/trades` - 特定実行のトレード一覧

### 検証
- バックテスト実行中にWebSocketでリアルタイム進捗受信
- 完了後、結果がSQLiteに永続化
- `GET /api/v1/backtest/history` で履歴閲覧可能

---

## Phase 4: フロントエンド型定義・API更新

### 修正ファイル

| ファイル | 変更内容 |
|---------|---------|
| `frontend/src/types/index.ts` | `StrategyConfig` → `TradingConfig`。バックテストWebSocketイベント型追加。`ConsolidatedSignal`型追加 |
| `frontend/src/api/client.ts` | 新スキーマ対応。`getPresets`/`applyPreset`削除。`getBacktestHistory`/`getBacktestTrades`追加。null応答ハンドリング修正 |
| `frontend/src/components/SettingsModal.tsx` | 旧パラメータ→`TradingConfig`表示。エントリーフィルター/資金管理/PM別セクション |
| `frontend/src/components/BacktestPage.tsx` | パラメータオーバーライドフォーム追加。共有WebSocketClient使用に統一 |
| `frontend/src/App.tsx` | ハードコード`SYMBOL`削除。シンボルセレクター追加 |

### 検証
- 設定モーダルが`UnifiedBotConfig`パラメータを正しく表示
- TypeScriptコンパイルエラーなし

---

## Phase 5: ダッシュボードUI/UXデザイン刷新

### ダッシュボードレイアウト
```
+----------------------------------------------------------+
| [Logo] AutoTrader V4    [Symbol▼] [Status●] [Settings⚙]  |
+----------------------------------------------------------+
| Balance    | Equity     | Daily P&L  | Win Rate | Trades  |
| ¥1,005,000 | ¥1,007,500 | +¥5,000    | 66.8%    | 3/5     |
+----------------------------------------------------------+
|                                    |  Signal Panel        |
|  Price Chart (2/3 width)           |  ┌─────────────────┐ |
|  ┌────────────────────────────┐    |  │ BUY  Score: 6.2  │ |
|  │  USDJPY M15 Candlestick   │    |  │ RANGE/DAY_TRADE  │ |
|  │  + BB bands + EMA overlay  │    |  │ TF: H1,H4,D1    │ |
|  │  + Entry/Exit markers      │    |  │ Penalty: 0.3     │ |
|  ├────────────────────────────┤    |  └─────────────────┘ |
|  │  RSI / MACD sub-chart      │    |  Position Table      |
|  └────────────────────────────┘    |  ┌─────────────────┐ |
|                                    |  │ #1 BUY +12pips  │ |
|                                    |  │ R: 0.8  SL/TP   │ |
|                                    |  └─────────────────┘ |
+----------------------------------------------------------+
| Trade History (collapsible)                                |
| ID | Dir | Entry | Exit | Pips | P&L | Regime | Reason   |
+----------------------------------------------------------+
```

### 修正ファイル

| ファイル | 変更内容 |
|---------|---------|
| `frontend/src/components/Layout.tsx` | サイドバーナビ（Dashboard/Backtest/History/Settings）。ダークテーマ。接続ステータス表示 |
| `frontend/src/components/AccountInfo.tsx` | メトリクスストリップ化。カラーコード、アニメーション遷移 |
| `frontend/src/components/Chart.tsx` | トレードマーカー、インジケータオーバーレイ、RSI/MACDサブチャート |
| `frontend/src/components/SignalPanel.tsx` | `ConsolidatedSignal`詳細表示。スコアバー、レジーム/モードバッジ、ペナルティ内訳 |
| `frontend/src/components/PositionTable.tsx` | リアルタイムP&L、SL/TP進捗バー、R倍率、保有時間 |
| `frontend/src/components/TradeHistory.tsx` | サマリーメトリクス行、カラーコード、決済理由バッジ、フィルター/ページネーション |
| `frontend/src/components/IndicatorPanel.tsx` | コンパクトゲージ（RSI/Stoch）。BBW/ADXバー表示 |
| `frontend/src/App.tsx` | レイアウト再構成。タブベースナビ |
| `frontend/tailwind.config.js` | トレーディングカラー拡張、カスタムアニメーション |
| `frontend/src/styles/globals.css` | ダークテーマ強化、グラスモーフィズム、P&L色アニメーション |

### 新規ファイル

| ファイル | 内容 |
|---------|------|
| `frontend/src/components/EquityCurve.tsx` | エクイティカーブ（lightweight-charts）。DD表示、月次リターンヒートマップ |

---

## Phase 6: WebSocketリアルタイム統合

### 修正ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/web/websocket/handlers.py` | `broadcast_consolidated_signal`追加。バックテストイベント転送検証 |
| `frontend/src/api/websocket.ts` | 指数バックオフ改善。接続状態公開 |
| `frontend/src/hooks/useWebSocket.ts` | 接続状態追跡（connected/connecting/disconnected/error）|
| `frontend/src/hooks/useSignals.ts` | `ConsolidatedSignal`対応 |

### 新規ファイル

| ファイル | 内容 |
|---------|------|
| `frontend/src/hooks/useBacktestWebSocket.ts` | バックテスト専用WebSocketフック。進捗/トレード/メトリクスのtyped state |

---

## Phase 7: バックテストページ強化

### 修正ファイル

| ファイル | 変更内容 |
|---------|---------|
| `frontend/src/components/BacktestPage.tsx` | 全面書き換え：設定パネル→実行ダッシュボード→結果表示 |
| `frontend/src/components/BacktestChart.tsx` | DDオーバーレイ、トレードマーカー、ズーム/パン |
| `src/autotrader/web/routers/backtest.py` | 履歴API追加（Phase3で作成したものの最終調整） |

### 新規ファイル

| ファイル | 内容 |
|---------|------|
| `frontend/src/components/BacktestConfigForm.tsx` | パラメータフォーム（セクション分け、バリデーション、localStorage保存） |
| `frontend/src/components/BacktestResults.tsx` | 結果表示（KPIカード、年次/月次テーブル、ヒートマップ、トレード一覧） |
| `frontend/src/components/BacktestHistory.tsx` | 過去実行一覧、比較機能 |

---

## Phase 8: MT5対応準備

### 新規ファイル

| ファイル | 内容 |
|---------|------|
| `src/autotrader/web/services/trading_state_service.py` | 抽象トレーディング状態インターフェース。`BacktestTradingState`実装 + `LiveTradingState`プレースホルダ |

### 修正ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/web/services/market_service.py` | `TradingStateService`経由でデータ取得に切替 |
| `frontend/src/App.tsx` | モード表示（Backtest Mode / Live Mode） |

---

## 全体サマリー

| Phase | 内容 | 複雑度 | 修正 | 新規 |
|-------|------|--------|------|------|
| 0 | P0バグ修正 | S | 4 | 0 |
| 1 | スキーマ現行化 | M | 5 | 0 |
| 2 | データレイヤー接続 | L | 4 | 2 |
| 3 | バックテストパイプライン | M | 3 | 0 |
| 4 | フロントエンド型更新 | M | 5 | 0 |
| 5 | ダッシュボードUI刷新 | L | 10 | 1 |
| 6 | WebSocket統合 | M | 4 | 1 |
| 7 | バックテストページ強化 | M | 3 | 3 |
| 8 | MT5準備 | S | 2 | 1 |
| **計** | | | **40** | **8** |

## 検証方法

各Phase完了後:
1. `uvicorn autotrader.web.main:app --reload` でサーバー起動
2. `cd src/autotrader/web/frontend && npm run dev` でフロントエンド起動
3. ブラウザで `http://localhost:5173` を開く
4. 各Phase固有の検証項目を実行

最終検証:
1. ダッシュボードが正しいレイアウトで表示される
2. バックテストを実行し、リアルタイム進捗がWebSocketで受信される
3. 完了後の結果がDB永続化され、履歴から閲覧可能
4. 設定画面が`UnifiedBotConfig`の実パラメータを表示
5. TypeScriptコンパイルエラーなし、Pythonテスト全パス
