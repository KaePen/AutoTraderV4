# AutoTraderV4 WebUI実装計画

## 概要

リアルタイムチャート分析・トレード状況を表示するWebダッシュボードを実装する。

## 技術スタック

- **バックエンド**: FastAPI 0.108.0 + Uvicorn 0.25.0（既存依存）
- **リアルタイム通信**: WebSocket
- **フロントエンド**: React 18 + Vite
- **チャート**: TradingView Lightweight Charts
- **スタイル**: Tailwind CSS
- **通知**: Web Notifications API

## ディレクトリ構造

```
src/autotrader/
└── web/                          # 新規作成
    ├── __init__.py
    ├── main.py                   # FastAPIアプリケーション
    ├── config.py                 # Web設定
    ├── dependencies.py           # DI設定
    ├── routers/
    │   ├── __init__.py
    │   ├── dashboard.py          # ダッシュボードAPI
    │   ├── signals.py            # シグナルAPI
    │   ├── positions.py          # ポジションAPI
    │   ├── trades.py             # トレード履歴API
    │   ├── indicators.py         # 指標API
    │   ├── candles.py            # ローソク足API
    │   └── settings.py           # 設定変更API
    ├── schemas/
    │   ├── __init__.py
    │   ├── responses.py          # レスポンススキーマ
    │   └── requests.py           # リクエストスキーマ
    ├── services/
    │   ├── __init__.py
    │   ├── market_service.py     # 市場データサービス
    │   ├── signal_service.py     # シグナルサービス
    │   └── settings_service.py   # 設定サービス
    ├── websocket/
    │   ├── __init__.py
    │   ├── manager.py            # 接続管理
    │   └── handlers.py           # イベントハンドラー
    └── frontend/                 # Reactアプリ
        ├── package.json
        ├── vite.config.ts
        ├── tsconfig.json
        ├── index.html
        ├── src/
        │   ├── main.tsx
        │   ├── App.tsx
        │   ├── api/
        │   │   ├── client.ts     # APIクライアント
        │   │   └── websocket.ts  # WebSocket管理
        │   ├── components/
        │   │   ├── Layout.tsx
        │   │   ├── Chart.tsx
        │   │   ├── SignalPanel.tsx
        │   │   ├── PositionTable.tsx
        │   │   ├── TradeHistory.tsx
        │   │   ├── IndicatorPanel.tsx
        │   │   ├── AccountInfo.tsx
        │   │   ├── SettingsModal.tsx
        │   │   └── NotificationBell.tsx
        │   ├── hooks/
        │   │   ├── useWebSocket.ts
        │   │   ├── useSignals.ts
        │   │   └── useNotification.ts
        │   ├── types/
        │   │   └── index.ts
        │   └── styles/
        │       └── globals.css
        └── public/
```

## 主要機能

### 1. ダッシュボード
- 口座情報（残高、エクイティ、証拠金）
- 本日のP&Lサマリー
- アクティブシグナル数

### 2. リアルタイムチャート
- TradingView Lightweight Chartsによるローソク足表示
- 時間足切り替え（M1, M5, M15, H1, H4, D1）
- シグナルマーカー表示（BUY/SELL矢印）

### 3. シグナル情報
- 現在のシグナル（方向、確度、根拠）
- 確度レベル（HIGH/MEDIUM/LOW）色分け表示
- 指標スナップショット（RSI, MACD, ADX等）

### 4. ポジション管理
- オープンポジション一覧
- 未実現損益リアルタイム更新
- SL/TP表示

### 5. トレード履歴
- 直近トレード一覧
- 勝敗統計

### 6. MTF分析
- 複数時間足の統合分析表示
- 各時間足のシグナル強度

### 7. アラート通知
- シグナル発生時のブラウザ通知
- 通知設定（確度閾値、有効/無効）
- 通知履歴表示

### 8. 設定変更UI
- 戦略パラメータ変更
  - min_signals, adx_threshold, confidence_threshold
  - SL/TP倍率
- リスク管理設定
  - 最大日次損失率
  - 最大ポジション数
- 通知設定

## REST APIエンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/v1/health` | ヘルスチェック |
| GET | `/api/v1/dashboard` | ダッシュボード全体 |
| GET | `/api/v1/signals/current` | 現在のシグナル |
| GET | `/api/v1/signals/history` | シグナル履歴 |
| GET | `/api/v1/positions` | オープンポジション |
| GET | `/api/v1/trades` | トレード履歴 |
| GET | `/api/v1/trades/summary` | トレードサマリー |
| GET | `/api/v1/indicators/{symbol}/{timeframe}` | 指標スナップショット |
| GET | `/api/v1/candles/{symbol}/{timeframe}` | ローソク足データ |
| GET | `/api/v1/settings` | 現在の設定取得 |
| PUT | `/api/v1/settings` | 設定更新 |
| GET | `/api/v1/settings/presets` | プリセット一覧 |
| POST | `/api/v1/settings/presets/{name}` | プリセット適用 |

## WebSocketイベント

### 購読チャネル
- `ws://host/ws/market/{symbol}` - 市場データ
- `ws://host/ws/signals` - シグナル更新
- `ws://host/ws/dashboard` - 統合ダッシュボード

### イベントタイプ
- `candle_update` - ローソク足更新
- `signal_update` - シグナル変化（通知トリガー）
- `position_update` - ポジション変化
- `indicator_update` - 指標更新
- `account_update` - 口座情報更新
- `alert` - アラート通知

## 実装フェーズ

### Phase 1: バックエンド基盤
- [ ] web/ディレクトリ構造作成
- [ ] FastAPIアプリケーション（main.py）
- [ ] 設定・依存性注入（config.py, dependencies.py）
- [ ] ヘルスチェックエンドポイント

### Phase 2: REST API
- [ ] レスポンススキーマ定義
- [ ] サービス層実装
- [ ] 各ルーター実装（dashboard, signals, positions, trades, indicators, candles, settings）

### Phase 3: WebSocket
- [ ] ConnectionManager実装
- [ ] WebSocketルート追加
- [ ] イベントハンドラー実装

### Phase 4: Reactフロントエンド基盤
- [ ] Vite + React + TypeScript初期化
- [ ] Tailwind CSS設定
- [ ] APIクライアント実装
- [ ] WebSocketフック実装
- [ ] 基本レイアウトコンポーネント

### Phase 5: UIコンポーネント
- [ ] Chart.tsx（TradingView Lightweight Charts）
- [ ] SignalPanel.tsx
- [ ] PositionTable.tsx
- [ ] TradeHistory.tsx
- [ ] IndicatorPanel.tsx
- [ ] AccountInfo.tsx

### Phase 6: 通知・設定機能
- [ ] NotificationBell.tsx（通知表示）
- [ ] useNotification.ts（Web Notifications API）
- [ ] SettingsModal.tsx（設定変更UI）
- [ ] settings.py（設定API）

### Phase 7: 統合・テスト
- [ ] E2E動作確認
- [ ] エラーハンドリング強化
- [ ] ビルド・デプロイ設定

## 重要ファイル（参照用）

- `src/autotrader/core/entities.py` - Candle, Signal, Position, Trade, AccountInfo
- `src/autotrader/core/enums.py` - Timeframe, SignalType, ConfidenceLevel
- `src/autotrader/adapters/database/repositories.py` - データアクセス層
- `src/autotrader/decision/decision_engine.py` - DecisionEngine
- `src/autotrader/config/settings.py` - Settings, StrategyConfig

## 起動方法

```bash
# バックエンド起動
cd src/autotrader/web
uvicorn main:app --reload --port 8000

# フロントエンド開発サーバー起動（別ターミナル）
cd src/autotrader/web/frontend
npm run dev

# 本番ビルド
npm run build
# ビルド成果物はFastAPIのstaticから配信
```

## 検証方法

1. バックエンドAPI確認
   - `http://localhost:8000/docs` でSwagger UI確認
   - 各エンドポイントの動作テスト

2. フロントエンド確認
   - `http://localhost:5173` で開発サーバー確認
   - チャート表示、データ更新確認

3. WebSocket確認
   - ブラウザコンソールで接続ログ確認
   - リアルタイム更新確認

4. 通知確認
   - ブラウザ通知許可
   - シグナル発生時の通知テスト

5. 設定変更確認
   - 設定UIから変更
   - API経由で設定反映確認
