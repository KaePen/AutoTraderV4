# AutoTraderV4

FX自動トレードシステム — MetaTrader 5連携、マルチタイムフレーム分析、テクニカル+ファンダメンタル統合戦略

## 概要

AutoTraderV4は、外国為替（FX）取引を自動化するPythonベースのトレーディングシステムです。
M1からD1まで8つの時間足を同時に分析し、コンセンサススコアリングでエントリー判断を行います。
バックテストとリアルトレードで**同一のトレードロジック**を共有するアーキテクチャにより、
バックテスト結果とリアルトレードの乖離を最小化しています。

### 主要機能

- **マルチタイムフレーム分析**: M1, M5, M15, M30, H1, H4, H8, D1 を並列評価
- **テクニカル指標**: MACD, RSI, ADX, ストキャスティクス, ボリンジャーバンド, SMC（スマートマネーコンセプト）
- **市場構造分析**: BOS/CHoCH検出, スイングハイ/ロー, 流動性ゾーン
- **ファンダメンタル統合**: 経済指標イベントのリスク評価・LLMニュースセンチメント分析
- **月単位並列バックテスト**: 1月=1CPUのアトミック並列実行 + Parquetインジケータキャッシュ
- **リスク管理**: ATRベースの動的SL/TP、HardGuard/SoftGuardの多層防御
- **マルチペア運用**: 8通貨ペア同時運用（6 JPY + 2 USD）、共有エクイティ管理

### 対応通貨ペア

| ペア | 区分 | 状態 |
|------|------|------|
| USDJPY, EURJPY, GBPJPY, AUDJPY, CADJPY, CHFJPY | JPY | 運用中 |
| EURUSD, GBPUSD | USD | 運用中 |
| AUDUSD, NZDUSD, USDCHF, USDCAD | USD | 定義済み（無効） |

---

## セットアップ

### 前提条件

- Python 3.12以上
- [uv](https://github.com/astral-sh/uv) パッケージマネージャー
- MetaTrader 5（リアルトレード・データエクスポート時。Windows専用）

### インストール

```bash
git clone https://github.com/KaePen/AutoTraderV4.git
cd AutoTraderV4

# 基本インストール
uv sync

# MT5連携を含む
uv sync --extra mt5

# MT5 + ライブ機能（ニュース収集等）
uv sync --extra mt5 --extra live

# 全機能（mt5, live, fast, gdelt, bigquery, dev）
uv sync --all-extras

# 開発用（pytest, ruff, mypy等）
uv sync --extra dev
```

### extras 一覧

| extras名 | 内容 |
|---------|------|
| `mt5` | MetaTrader5, pywin32（Windows専用） |
| `live` | feedparser（RSSニュース取得） |
| `fast` | numba（JITコンパイル高速化） |
| `gdelt` | requests（GDELTニュース取得） |
| `bigquery` | google-cloud-bigquery（GDELT BigQuery） |
| `dev` | pytest, mypy, ruff, hypothesis |

### 環境変数

`.env` ファイルをプロジェクトルートに作成:

```env
# トレーディングモード
TRADING_MODE=BACKTEST

# 対象通貨ペア
SYMBOL=USDJPY

# データベース
DATABASE_URL=sqlite:///data/autotrader.db
LOCAL_DATABASE_URL=sqlite:///data/local_state.db

# Ollama LLM（ファンダメンタル分析に使用）
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2

# リスク管理
MAX_DAILY_LOSS_PCT=5.0
MAX_POSITION_COUNT=2
MIN_MARGIN_RATIO=150.0

# ログ
LOG_LEVEL=INFO
```

---

## アーキテクチャ

バックテストとリアルトレードは**同一のトレードロジック**を共有します:

```
バックテスト:   CSV --> 前処理 --> [共通トレードロジック] --> メトリクス出力
リアルトレード: MT5 --> [共通トレードロジック] --> MT5注文実行
```

### レイヤー構造

```
core/               共通エンティティ・インターフェース（Signal, Trade, Position, Candle）
calculator/         指標計算（テクニカル・マーケット構造）  ← バックテスト・リアル共用
constraint/         トレード制約（HardGuard, SoftGuard）   ← バックテスト・リアル共用
decision/           シグナル生成・ポジション管理           ← バックテスト・リアル共用
backtest/           データI/O・シミュレーション実行・メトリクス
live/               MT5接続・リアルタイム実行
adapters/           外部サービスアダプタ（MT5, DB, Ollama, ファンダメンタル）
config/             設定管理
web/                FastAPI ダッシュボード
```

### シグナル生成パイプライン

```
各時間足データ
  │
TimeframeEvaluator（時間足別評価）
  ├─ テクニカル指標: RSI, MACD, ADX, Stoch, BB
  ├─ 市場構造: BOS/CHoCH, スイングH/L, 流動性
  └─ スコア: -1.0 ~ +1.0
  │
ModeAwareScoreConsensus（マルチTF統合）
  ├─ 役割別重み付け: Entry / Confirmation / Higher TF
  ├─ 品質フィルター: ADX強度、RSI過熱除外
  └─ コンセンサススコア算出
  │
FilterManager + HardGuard / SoftGuard（多層フィルタリング）
  ├─ TrendFilter, VolatilityFilter, SessionFilter, ADXFilter, EventFilter
  ├─ HardGuard: 証拠金不足 / 日次損失上限 / 最大ポジション超過 / 時間外
  └─ SoftGuard: スプレッド拡大 / セッション非推奨帯 / ボラ異常（ペナルティ減算）
  │
Signal（エントリー判断）
```

### ポジション管理

`PositionManager` が提供する機能:

| 機能 | 説明 |
|------|------|
| 建値移動 | 1R到達で損益分岐点にSL移動 |
| 部分決済 | 0.5R / 1R / 2R で段階的に利益確保 |
| トレーリングSL | ATRベースの動的SL追従（2段階） |
| 時間決済 | 長時間停滞ポジションの自動撤退 |
| コンセンサス逆転Exit | 反対シグナル発生時の決済 |

---

## バックテスト

### キューランナー（推奨）

バックテストは**キューランナー**経由で実行します。別ターミナルで常駐起動:

```bash
uv run python scripts/backtest_queue_runner.py --cpu-threads 12
```

キューファイル `backtest_queue.json` にジョブを記述して投入:

```json
{
  "jobs": [
    {
      "id": "TEST-USDJPY",
      "symbol": "USDJPY",
      "years": "2020-2025",
      "description": "USDJPY検証",
      "overrides": {
        "bot": { "consensus_threshold": 17.0 },
        "pm":  { "trailing_start_r": 1.5 },
        "backtest": { "spread_multiplier": 1.0 }
      }
    }
  ]
}
```

マルチペアジョブ:

```json
{
  "jobs": [
    {
      "id": "multi-8pair-T1",
      "type": "multi_pair",
      "symbols": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY", "EURUSD", "GBPUSD"],
      "years": "2023-2025",
      "description": "8ペア統合テスト",
      "multi_pair_config": {
        "name": "8PAIR-T1",
        "global_max_positions": 8,
        "per_pair_max_positions": 1,
        "global_max_exposure_lot": 12.0,
        "base_risk_pct": 0.004,
        "consensus_threshold": 17.0,
        "spread_multiplier": 1.0
      }
    }
  ]
}
```

#### 対話コマンド

| コマンド | 動作 |
|---------|------|
| `status` | 稼働状態・CPU使用数・各ジョブ進捗を表示 |
| `pause` | 新規タスク取得を一時停止 |
| `resume` | 一時停止解除 |
| `stop` | 全タスク停止 + completed_idsクリア |
| `cpu N` | CPUスレッド数を動的変更 |
| `quit` | 全停止してランナー終了 |

#### 実行フロー

1. 全TF×全年のインジケータを事前計算（`PrecomputeEngine`、Parquetキャッシュ）
2. 年×月の全組み合わせをタスクキューに投入（1月=1CPU）
3. 月完了時にチェックポイント保存（`month_results/`）→ 途中再開可能
4. 全月完了 → ジョブ集約結果を `backtest_results/{result_id}.json` に出力

### 直接実行（開発・デバッグ用）

コード変更時の動作確認に限り直接実行を許可:

```bash
# シングルペア
uv run python scripts/run_backtest.py --symbol USDJPY --years 2023-2025

# マルチペア
uv run python scripts/run_multi_pair_backtest.py --tests R1

# ウォークフォワード検証
uv run python scripts/run_backtest.py --walk-forward --years 2015-2025

# 診断モード
uv run python scripts/run_backtest.py --diagnose --years 2023
```

#### 主要引数（run_backtest.py）

| 引数 | デフォルト | 説明 |
|------|-----------|------|
| `--symbol` | `USDJPY` | 通貨ペア |
| `--years` | `2020-2024` | 年範囲（例: `2020-2025`） |
| `--start-date` / `--end-date` | - | 日付範囲（yearsより優先） |
| `-tf, --timeframe` | `M15` | 基準時間足 |
| `--initial-balance` | `1,000,000` | 初期残高（JPY） |
| `--risk-pct` | `0.04` | 基本リスク率 |
| `--consensus-threshold` | `8.0` | コンセンサス閾値 |
| `--walk-forward` | - | ウォークフォワード検証 |
| `--diagnose` | - | 診断モード |
| `--fundamental` | - | 経済イベントデータ使用 |
| `--sequential` | - | シーケンシャル実行（デバッグ用） |

### 結果出力

| パス | 内容 |
|------|------|
| `backtest_results/{result_id}.json` | ジョブ集約結果（全年統合） |
| `month_results/{result_id}/` | 月別チェックポイント |

結果にはRichライブラリによるカラーテーブルで以下を表示:
総取引数、勝率、非敗率、プロフィットファクター、純利益、最大ドローダウン、シャープレシオ、年別詳細

### データ準備

チャートデータはMT5からエクスポートしたタブ区切りCSVを使用:

```
data/{SYMBOL}/chart/
  USDJPY_M1_20100104_20251231.csv
  USDJPY_M5_20100104_20251231.csv
  USDJPY_M15_20100104_20251231.csv
  USDJPY_H1_20100104_20251231.csv
  USDJPY_H4_20100104_20251231.csv
  USDJPY_D1_20100104_20251231.csv
```

データディレクトリは `get_data_dir()` で自動検出されます（デフォルト: プロジェクトルートの `data/`）。

---

## Web UI

FastAPI + Jinja2 によるダッシュボードで、リアルタイムの監視・制御を行います。

### 起動

```bash
# 標準起動（ポート8000）
uv run python -m autotrader.web

# 開発モード（自動リロード）
uv run uvicorn autotrader.web.main:app --host 0.0.0.0 --port 8000 --reload
```

起動後: http://localhost:8000

### MT5接続付きで起動

```bash
MT5_LOGIN=123456 \
MT5_PASSWORD=your_password \
MT5_SERVER=YourBroker-Server \
MT5_TERMINAL_PATH="C:/Program Files/MetaTrader 5/terminal64.exe" \
AUTOTRADER_SYMBOL=USDJPY \
AUTOTRADER_AUTO_TRADE=true \
uv run python -m autotrader.web
```

### バックテストWeb UI

キューランナーの状態監視・制御用の別ダッシュボード:

```bash
uv run python scripts/backtest_web_ui.py --port 8888
```

### Web UI 環境変数

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `AUTOTRADER_WEB_HOST` | `0.0.0.0` | ホスト |
| `AUTOTRADER_WEB_PORT` | `8000` | ポート |
| `AUTOTRADER_WEB_DEBUG` | `false` | デバッグモード |
| `AUTOTRADER_WEB_WS_HEARTBEAT_INTERVAL` | `30` | WebSocketハートビート間隔（秒） |

### 主要エンドポイント

| パス | 説明 |
|------|------|
| `/` | ダッシュボード画面 |
| `/api/v1/health` | ヘルスチェック |
| `/api/v1/dashboard` | 口座情報・サマリー |
| `/api/v1/signals` | シグナル一覧 |
| `/api/v1/positions` | オープンポジション |
| `/api/v1/trades` | 取引履歴 |
| `/api/v1/indicators/{symbol}/{timeframe}` | 指標スナップショット |
| `/api/v1/candles/{symbol}/{timeframe}` | ローソク足データ |
| `/api/v1/settings` | パラメータ設定（GET/PUT） |
| `/api/v1/trading/mt5/connect` | MT5接続（POST） |
| `/ws/market/{symbol}` | 市場データWebSocket |
| `/ws/signals` | シグナルWebSocket |
| `/ws/dashboard` | ダッシュボードWebSocket |

---

## 通貨ペア別設定

`config/symbol_presets.yaml` で通貨ペアごとにパラメータを一元管理（Single Source of Truth）:

| カテゴリ | パラメータ例 |
|---------|------------|
| 基本 | `pip_value`, `pip_unit`, `spread_pips`, `slippage_pips` |
| SL/TP | `default_sl_pips`, `default_tp_pips` |
| リスク | `base_risk_pct`, `max_risk_pct_abs`, `max_positions` |
| シグナル | `consensus_threshold`, `macd_slope_deadzone`等のオーバーライド |
| ポジション管理 | `trailing_start_r`, `partial_1r_ratio`等 |

バックテストでもリアルでも同じ `get_preset("USDJPY")` で取得します。

---

## スクリプト一覧

### バックテスト

| スクリプト | 説明 |
|-----------|------|
| `backtest_queue_runner.py` | キューランナー（月単位並列、常駐プロセス） |
| `backtest_web_ui.py` | キューランナーWeb UI（状態監視・制御） |
| `run_backtest.py` | シングルペアバックテスト（直接実行） |
| `run_multi_pair_backtest.py` | マルチペアバックテスト（共有エクイティ） |
| `analyze_whatif.py` | What-If分析（ブロックシグナルの機会損失定量化） |

### 運用

| スクリプト | 説明 |
|-----------|------|
| `pr_watcher.py` | PR自動マージ・worktree掃除デーモン |
| `mt5/CalendarExporter.mq5` | MT5経済カレンダーエクスポート（MQL5） |

---

## プロジェクト構造

```
AutoTraderV4/
  autotrader/                     # メインパッケージ
    core/                         # 共通エンティティ・インターフェース
      entities.py                 #   Signal, Trade, Position, Candle
      enums.py                    #   列挙型（Direction, Timeframe等）
      interfaces/                 #   DataProvider, TradeExecutor, Guard
      diagnostics.py              #   診断データ構造
    calculator/                   # 指標計算（バックテスト・リアル共通）
      technical/                  #   トレンド, モメンタム, ボラティリティ
        batch.py                  #   バッチ計算（全TF一括）
      market_structure/           #   BOS/CHoCH, スイング, 流動性
      features/                   #   レジーム, MTF整合, ダイバージェンス
    constraint/                   # トレード制約
      hard_guard.py               #   絶対禁止条件
      soft_guard.py               #   ペナルティ条件
      filters/                    #   トレンド, ボラ, セッション, ADX, イベント
    decision/unified/             # シグナル生成・ポジション管理
      trade_bot.py                #   UnifiedTradeBot（メイン）
      mode_aware_consensus.py     #   マルチTFコンセンサス
      position_manager.py         #   トレーリング・建値・部分決済
      config.py                   #   UnifiedBotConfig
    backtest/                     # バックテスト実行基盤
      engine.py                   #   バックテストエンジン本体
      simulator.py                #   TradeSimulator
      runner.py                   #   BacktestRunner
      month_runner.py             #   月単位並列実行
      year_runner.py              #   年単位実行管理
      walk_forward.py             #   ウォークフォワード検証
      metrics.py                  #   パフォーマンス指標計算
      metrics_aggregator.py       #   複数期間メトリクス集計
      csv_data_provider.py        #   CSVデータ提供
      whatif_tracker.py           #   What-If分析用追跡
      config.py                   #   バックテスト固有設定
    live/                         # リアルトレード実行基盤
      engine.py                   #   LiveTradingEngine
      engine_manager.py           #   EngineManager（マルチシンボル）
      data_feed.py                #   リアルタイムデータフィード
      order_service.py            #   注文実行サービス
      position_sync.py            #   MT5⇔DB同期
      tick_entry_optimizer.py     #   M1ティックエントリー最適化
      fundamental_service.py      #   ファンダメンタルリアルタイム取得
    adapters/                     # 外部サービスアダプタ
      mt5/                        #   MT5接続・データ・注文
      fundamental/                #   経済指標・ニュース・LLM分析
        exchange_calendar_provider.py  # 取引所カレンダー（exchange-calendars）
        forex_factory.py          #   ForexFactory経済指標
        collector.py              #   データ収集統括
      ollama/                     #   ローカルLLMクライアント
      database/                   #   SQLAlchemy DB
    web/                          # FastAPI Web UI
      routers/                    #   APIルーター群
      templates/                  #   Jinja2 HTMLテンプレート
      websocket/                  #   WebSocketハンドラ
      auth/                       #   JWT認証
      middleware/                 #   HTTPSリダイレクト等
    config/                       # 設定管理
      settings.py                 #   環境変数読み込み
      trading_params.py           #   SymbolPreset・get_preset()
      config_loader.py            #   YAML設定ローダー
  config/                         # YAML設定ファイル
    symbol_presets.yaml           #   通貨ペア別パラメータ（SSOT）
    live_trading.yaml             #   本番トレード設定
    demo_trading.yaml             #   デモトレード設定
    accounts.yaml                 #   MT5アカウント情報
  scripts/                        # 実行スクリプト
  tests/                          # テスト
    golden/                       #   回帰テスト
    unit/                         #   ユニットテスト
```

---

## テスト

```bash
# 全テスト実行（カバレッジ付き）
uv run pytest

# 詳細出力
uv run pytest -v

# HTMLカバレッジレポート
uv run pytest --cov=autotrader --cov-report=html

# 特定ディレクトリ
uv run pytest tests/unit/backtest/
uv run pytest tests/unit/decision/

# 回帰テスト
uv run pytest tests/golden/
```

## 開発

```bash
# リンター
uv run ruff check autotrader/

# フォーマッター
uv run ruff format autotrader/

# 型チェック
uv run mypy autotrader/
```

---

## 注意事項

- 本ソフトウェアは教育・研究目的で開発されています
- 実際のトレードで使用する場合は、十分なテストと検証を行ってください
- 過去のパフォーマンスは将来の結果を保証するものではありません
- 投資判断は自己責任で行ってください

## ライセンス

MIT License
