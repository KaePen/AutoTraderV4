# AutoTraderV4

FX自動トレードシステム - MetaTrader 5連携、マルチタイムフレーム分析、テクニカル+ファンダメンタル統合戦略

## 概要

AutoTraderV4は、外国為替（FX）取引を自動化するPythonベースのトレーディングシステムです。
M1からD1まで8つの時間足を同時に分析し、コンセンサススコアリングでエントリー判断を行います。

- **マルチタイムフレーム分析**: M1, M5, M15, M30, H1, H4, H8, D1 を並列評価
- **テクニカル指標**: MACD, RSI, ADX, ストキャスティクス, ボリンジャーバンド, SMC（スマートマネーコンセプト）
- **市場構造分析**: BOS/CHoCH検出, スイングハイ/ロー, 流動性ゾーン
- **ファンダメンタル統合**: 経済指標イベントのリスク評価・ニュースセンチメント分析
- **並列バックテスト**: 年単位のマルチプロセス並列実行 + Parquetキャッシュ
- **リスク管理**: ATRベースの動的SL/TP、HardGuard/SoftGuardの多層防御
- **13通貨ペア対応**: USDJPY, EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCHF, USDCAD, EURJPY, GBPJPY, AUDJPY, CADJPY, CHFJPY

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

# 全機能
uv sync --all-extras

# 開発用（pytest, ruff, mypy等）
uv sync --extra dev
```

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

## バックテスト

### 基本実行

```bash
# デフォルト設定（USDJPY 2020-2024年、M15基準）
uv run python scripts/run_backtest.py

# 年範囲指定
uv run python scripts/run_backtest.py --years 2020-2025

# 日付範囲指定（--yearsより優先）
uv run python scripts/run_backtest.py --start-date 2023-06-01 --end-date 2025-09-30

# 通貨ペア・時間足指定
uv run python scripts/run_backtest.py --symbol EURUSD --timeframe M15

# 固定ロット（動的サイジング無効）
uv run python scripts/run_backtest.py --fixed-lot --volume 1.0

# コンセンサス閾値変更
uv run python scripts/run_backtest.py --consensus-threshold 9.0
```

### 実行モード

```bash
# ウォークフォワード検証（過学習チェック）
uv run python scripts/run_backtest.py --walk-forward --years 2015-2025

# 診断モード（データ品質・シグナル統計の確認）
uv run python scripts/run_backtest.py --diagnose --years 2023

# 特定時刻のシグナルデバッグ
uv run python scripts/run_backtest.py --debug-signal "2023-03-15 10:30"

# 軽量バックテスト（サンプリング実行）
uv run python scripts/run_backtest.py --quick

# シーケンシャル実行（デバッグ用、並列なし）
uv run python scripts/run_backtest.py --sequential
```

### ファンダメンタル統合

```bash
# 経済イベントデータを使用
uv run python scripts/run_backtest.py --fundamental

# イベントLLM分析を使用
uv run python scripts/run_backtest.py --event-llm

# Phase 2b統合フル有効化
uv run python scripts/run_backtest.py --fundamental-phase2b
```

### 主要引数一覧

**期間・シンボル**

| 引数 | デフォルト | 説明 |
|------|-----------|------|
| `--years` | `2020-2024` | 年範囲 |
| `--start-date` | - | 開始日（YYYY-MM-DD） |
| `--end-date` | - | 終了日（YYYY-MM-DD） |
| `--symbol` | `USDJPY` | 通貨ペア |
| `-tf, --timeframe` | `M15` | 基準時間足 |
| `--timeframes` | - | TFリスト（カンマ区切り） |

**資金・リスク**

| 引数 | デフォルト | 説明 |
|------|-----------|------|
| `--initial-balance` | `1,000,000` | 初期残高（JPY） |
| `--volume` | `1.0` | 取引ロット |
| `--max-positions` | `1` | 最大同時ポジション数 |
| `--fixed-lot` | - | 固定ロット使用 |
| `--risk-pct` | `0.04` | 基本リスク率 |
| `--max-risk-pct-abs` | `0.07` | 絶対最大リスク率 |
| `--equity-floor` | `0.30` | 取引停止の資金下限率 |

**ポジション管理**

| 引数 | デフォルト | 説明 |
|------|-----------|------|
| `--trailing-start-r` | `1.5` | トレーリング開始R値 |
| `--trailing-atr-mult` | `1.5` | ATRトレーリング倍率 |
| `--no-breakeven-1r` | - | 1R建値移動を無効化 |
| `--partial-1r-ratio` | `0.05` | 1R部分決済比率 |
| `--partial-2r-ratio` | `0.05` | 2R部分決済比率 |
| `--no-time-exit` | - | 時間決済を無効化 |

**コスト・実行**

| 引数 | デフォルト | 説明 |
|------|-----------|------|
| `--spread` | preset値 | スプレッド上書き（pips） |
| `--slippage` | preset値 | スリッページ上書き（pips） |
| `--commission` | preset値 | ロット当たり手数料 |
| `--max-year-workers` | `5` | 年並列の最大ワーカー数 |
| `--consensus-threshold` | `8.0` | コンセンサス閾値 |

### バックテスト出力

結果はコンソールに出力されます（Richライブラリによるカラー表示対応）:

- 総取引数、勝率、非敗率
- プロフィットファクター、純利益（JPY）
- 最大ドローダウン（%）、シャープレシオ
- 年別詳細テーブル

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

MT5からのエクスポートは `export_mt5_chart.py` で実行可能:

```bash
# USDJPY 全時間足
uv run python scripts/export_mt5_chart.py

# 通貨ペア・期間指定
uv run python scripts/export_mt5_chart.py --symbol EURUSD --start 2020-01-01 --end 2025-12-31

# 特定の時間足のみ
uv run python scripts/export_mt5_chart.py --timeframes M5 H1 D1
```

---

## トレードロジック

### アーキテクチャ

バックテストとリアルトレードは**同一のトレードロジック**を共有します:

```
バックテスト:   CSV --> 前処理 --> [共通ロジック] --> コンソール出力
リアルトレード: MT5 --> [共通ロジック] --> MT5注文実行
```

共通ロジックのコア:
- `UnifiedTradeBot.generate_signal()` - シグナル生成の単一実装
- `PositionManager` - ポジション管理（トレーリングSL、ブレークイーブン）
- `HardGuard` / `SoftGuard` - 多層リスクフィルタ

### シグナル生成パイプライン

```
各時間足データ
  |
TimeframeEvaluator（時間足別評価）
  |- テクニカル指標: RSI, MACD, ADX, Stoch, BB
  |- 市場構造: BOS/CHoCH, スイングH/L, 流動性
  |- スコア: -1.0 ~ +1.0
  |
ModeAwareScoreConsensus（マルチTF統合）
  |- 役割別重み付け: Entry / Confirmation / Higher TF
  |- 品質フィルター: ADX強度、RSI過熱除外
  |- コンセンサススコア算出
  |
FilterManager（フィルタリング）
  |- TrendFilter: 上位足整合性
  |- VolatilityFilter: ATRレジーム判定
  |- SessionFilter: 取引セッション（東京/ロンドン/NY）
  |- ADXFilter: トレンド強度
  |- EventFilter: 経済イベント前後の抑制
  |
HardGuard（絶対禁止条件）
  |- 証拠金不足 / 日次損失上限
  |- 最大ポジション数超過
  |- 取引時間外 / データ品質不良
  |- 高インパクトニュース前後
  |
SoftGuard（ペナルティ条件）
  |- スプレッド拡大
  |- セッション非推奨帯
  |- ボラティリティ異常
  |
Signal（エントリー判断）
```

### ポジション管理

`PositionManager` が提供する機能:

| 機能 | 説明 |
|------|------|
| 建値移動 | 1R到達で損益分岐点にSL移動 |
| 部分決済 | 0.5R / 1R / 2R で段階的に利益確保 |
| トレーリングSL | ATRベースの動的SL追従 |
| 時間決済 | 長時間停滞ポジションの自動撤退 |
| コンセンサス逆転Exit | 反対シグナル発生時の決済 |

### 通貨ペア別設定

`config/symbol_presets.yaml` で通貨ペアごとにパラメータを定義:

| パラメータ | 説明 |
|-----------|------|
| `pip_value` | 1pipあたりの価値（JPYペア: 100, USDペア: 10） |
| `spread_pips` | 標準スプレッド |
| `default_sl_pips` | デフォルトSL幅 |
| `default_tp_pips` | デフォルトTP幅 |
| `max_positions` | 最大同時ポジション数 |
| `base_risk_pct` | 口座リスク率 |
| `use_position_manager` | トレーリングSL有効/無効 |

---

## スクリプト一覧

### バックテスト・最適化

| スクリプト | 説明 | コマンド例 |
|-----------|------|-----------|
| `run_backtest.py` | 標準バックテスト | `uv run python scripts/run_backtest.py --years 2020-2024` |
| `run_param_optimization.py` | 6段階パラメータ最適化 | `python scripts/run_param_optimization.py --stage 0` |
| `run_m1_exploration.py` | M1 TF x 閾値 x ポジション数探索 | `python scripts/run_m1_exploration.py --phase 1` |
| `run_tf_combination_search.py` | 8TF全255通り組み合わせ探索 | `python scripts/run_tf_combination_search.py --years 2023-2025` |
| `run_comparison.py` | 3パターン比較バックテスト | `python scripts/run_comparison.py` |

### データ収集

| スクリプト | 説明 | コマンド例 |
|-----------|------|-----------|
| `export_mt5_chart.py` | MT5チャートCSVエクスポート | `python scripts/export_mt5_chart.py --symbol EURUSD` |
| `collect_fundamental_data.py` | 経済カレンダーデータ収集 | `python scripts/collect_fundamental_data.py --years 2018-2024 --source ff` |
| `collect_gdelt_news.py` | GDELTニュース収集 | `python scripts/collect_gdelt_news.py --source gkg --years 2022-2024` |
| `filter_news_by_source.py` | ニュースをFX専門ソースでフィルタ | `python scripts/filter_news_by_source.py --years 2015-2025` |
| `scrape_news_content.py` | ニュース記事本文スクレイピング | `python scripts/scrape_news_content.py --years 2020-2025 --resume` |

### ファンダメンタル

| スクリプト | 説明 | コマンド例 |
|-----------|------|-----------|
| `generate_fundamental_llm.py` | イベント/ニュース分析CSV事前生成 | `python scripts/generate_fundamental_llm.py events --symbol USDJPY --years 2020-2024` |

### 運用

| スクリプト | 説明 | コマンド例 |
|-----------|------|-----------|
| `pr_watcher.py` | PR自動マージ・worktree掃除デーモン | `python -u scripts/pr_watcher.py` |
| `fix_ghost_positions.py` | DBゴーストポジション修正 | `python scripts/fix_ghost_positions.py --dry-run` |

---

## プロジェクト構造

```
AutoTraderV4/
  autotrader/                   # メインパッケージ
    core/                       # 共通エンティティ・インターフェース
      entities.py               #   Signal, Trade, Position, Candle
      enums.py                  #   列挙型（Direction, Timeframe等）
      interfaces/               #   DataProvider, TradeExecutor, Guard
    calculator/                 # 指標計算（バックテスト・リアル共通）
      technical/                #   トレンド, モメンタム, ボラティリティ
      market_structure/         #   BOS/CHoCH, スイング, 流動性
      features/                 #   レジーム, MTF整合, ダイバージェンス
    constraint/                 # トレード制約
      hard_guard.py             #   絶対禁止条件
      soft_guard.py             #   ペナルティ条件
      filters/                  #   トレンド, ボラ, セッション, ADX, イベント
    decision/unified/           # シグナル生成・ポジション管理
      trade_bot.py              #   UnifiedTradeBot（メイン）
      mode_aware_consensus.py   #   マルチTFコンセンサス
      position_manager.py       #   トレーリング・建値・部分決済
      config.py                 #   UnifiedBotConfig
    backtest/                   # バックテスト実行基盤
      engine.py                 #   ParallelMultiTFBacktestEngine
      simulator.py              #   TradeSimulator
      runner.py                 #   BacktestRunner（年並列）
      walk_forward.py           #   ウォークフォワード検証
    live/                       # リアルトレード実行基盤
      engine.py                 #   LiveTradingEngine
      engine_manager.py         #   EngineManager（マルチシンボル）
    adapters/                   # 外部サービスアダプタ
      mt5/                      #   MT5接続・データ・注文
      fundamental/              #   経済指標・ニュース・LLM分析
      ollama/                   #   ローカルLLMクライアント
      database/                 #   SQLAlchemy DB
    web/                        # FastAPI Web UI
      routers/                  #   APIルーター群
      templates/                #   Jinja2 HTMLテンプレート
      websocket/                #   WebSocketハンドラ
    config/                     # 設定管理
      settings.py               #   環境変数読み込み
      trading_params.py         #   SymbolPreset
      config_loader.py          #   YAML設定ローダー
  config/                       # YAML設定ファイル
    symbol_presets.yaml         #   通貨ペア別パラメータ
    live_trading.yaml           #   本番トレード設定
    demo_trading.yaml           #   デモトレード設定
    accounts.yaml               #   MT5アカウント情報
  data/                         # データディレクトリ
    {SYMBOL}/chart/             #   MT5チャートCSV
    fundamental/                #   経済指標・ニュースデータ
  scripts/                      # 実行スクリプト
  tests/unit/                   # ユニットテスト
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

# 特定テスト
uv run pytest tests/unit/decision/unified/test_trade_bot.py -v
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
