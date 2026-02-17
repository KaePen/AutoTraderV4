# AutoTraderV4

FX自動トレードボット - MT5連携、マルチタイムフレーム分析、テクニカル指標によるトレンドフォロー戦略

## 概要

AutoTraderV4は、外国為替（FX）取引を自動化するためのPythonベースのトレーディングボットです。
マルチタイムフレーム分析とテクニカル指標を組み合わせたトレンドフォロー戦略により、
高い勝率と安定したリターンを実現します。

### 特徴

- **マルチタイムフレーム分析**: M1からD1まで複数の時間足を同時に分析
- **テクニカル指標**: MACD、RSI、ADX、ボリンジャーバンドなどを統合
- **トレンドフォロー戦略**: 上位足トレンドとの整合性を重視したエントリー
- **並列バックテスト**: マルチプロセス処理による高速バックテスト
- **リスク管理**: ATRベースのダイナミックSL/TP設定

## パフォーマンス

最新バックテスト結果（2023年1月〜2024年1月、USDJPY）：

| 指標 | 値 |
|------|-----|
| トレード数 | 196回/月 |
| 勝率 | 50.74% |
| 年間リターン | 1.15% |
| プロフィットファクター | 1.06 |
| 最大ドローダウン | 0.51% |

## インストール

### 前提条件

- Python 3.12以上
- [uv](https://github.com/astral-sh/uv) パッケージマネージャー

### セットアップ

```bash
# リポジトリをクローン
git clone https://github.com/your-username/AutoTraderV4.git
cd AutoTraderV4

# uvで依存関係をインストール
uv sync

# 開発用依存関係も含める場合
uv sync --all-extras
```

### オプション: Numba高速化

数値計算の高速化にNumbaを使用する場合：

```bash
uv sync --extra fast
```

## 設定

### 環境変数

`.env`ファイルをプロジェクトルートに作成：

```env
# トレーディングモード: BACKTEST, PAPER, LIVE
TRADING_MODE=BACKTEST

# 対象通貨ペア
SYMBOL=USDJPY

# データベース
DATABASE_URL=sqlite:///data/autotrader.db

# Ollama（LLM判断を使用する場合）
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2

# リスク管理
MAX_DAILY_LOSS_PCT=5.0
MAX_POSITION_COUNT=3
MIN_MARGIN_RATIO=150.0

# ログレベル
LOG_LEVEL=INFO
```

### 戦略パラメータ

`src/autotrader/config/settings.py`で戦略パラメータを調整可能：

```python
@dataclass(frozen=True)
class StrategyConfig:
    min_signals: int = 4        # 最小シグナル数
    signal_margin: int = 2      # 買い/売りの差分マージン
    adx_threshold: float = 20.0 # ADX閾値
    sl_atr_mult: float = 2.0    # SLのATR倍率
    tp_atr_mult: float = 3.0    # TPのATR倍率
    use_mtf: bool = True        # MTF確認を使用
    rsi_oversold: float = 30.0  # RSI売られすぎ
    rsi_overbought: float = 70.0 # RSI買われすぎ
```

プリセット設定：
- `StrategyConfig.optimized()` - 最適化済み（推奨）
- `StrategyConfig.conservative()` - 保守的（低リスク）
- `StrategyConfig.aggressive()` - 積極的（高リターン狙い）

## 使用方法

### バックテスト実行

#### 高速バックテスト（並列処理）

```bash
uv run python scripts/run_fast_backtest.py \
    --symbol USDJPY \
    --start 2023-01-01 \
    --end 2024-01-01 \
    --base-tf M15 \
    --chunk-months 3 \
    --workers 4
```

オプション：
- `--symbol`: 通貨ペア（デフォルト: USDJPY）
- `--start`: 開始日（YYYY-MM-DD形式）
- `--end`: 終了日（YYYY-MM-DD形式）
- `--base-tf`: 基準タイムフレーム（M1, M5, M15, H1, H4, D1）
- `--chunk-months`: チャンクサイズ（月単位、デフォルト: 3）
- `--workers`: ワーカー数（デフォルト: CPU数）
- `--data-dir`: データディレクトリパス
- `--compare`: 通常バックテストと比較

#### クイックバックテスト

```bash
uv run python scripts/quick_backtest.py
```

### データ準備

MT5からエクスポートしたCSVファイルを`data/csv/`ディレクトリに配置：

```
data/csv/
├── USDJPY_M1_2023.csv
├── USDJPY_M5_2023.csv
├── USDJPY_M15_2023.csv
├── USDJPY_H1_2023.csv
└── USDJPY_H4_2023.csv
```

CSVフォーマット（MT5標準）：
```csv
time,open,high,low,close,tick_volume,spread,real_volume
2023.01.02 00:00:00,130.123,130.456,130.100,130.300,1234,5,0
```

## 主要コンポーネント

### TimeframeEvaluator

時間足別にシグナルを評価するコンポーネント。

```python
from autotrader.decision.unified import TimeframeEvaluator

evaluator = TimeframeEvaluator(timeframe="M15")
evaluator.set_higher_tf_data({"H1": h1_df, "H4": h4_df})
signal = evaluator.evaluate(row, candle)
```

**シグナル生成ロジック**:
1. トレンド判定: 価格とSMA20/50の位置関係
2. MACDモメンタム: MACDとシグナルラインのクロス
3. ADXフィルター: トレンド強度（>20で有効、>25で強いトレンド）
4. RSIフィルター: 過熱/過冷を除外（80超/20未満）
5. 上位足整合性: HTFトレンドとの一致をボーナス/ペナルティ

### FastBacktestEngine

並列処理を使用した高速バックテストエンジン。

```python
from autotrader.backtest.fast_backtest import (
    FastBacktestConfig,
    FastBacktestEngine,
)

config = FastBacktestConfig(
    symbol="USDJPY",
    start_date=datetime(2023, 1, 1),
    end_date=datetime(2024, 1, 1),
    chunk_months=3,
    max_workers=4,
)

engine = FastBacktestEngine(config)
result = engine.run(base_df, market_data)
```

**特徴**:
- 期間をチャンクに分割して並列処理
- ProcessPoolExecutorによるマルチプロセス実行
- チャンク間のトレード引継ぎ処理

### TradeSimulator

SL/TPベースのポジション管理を行うシミュレーター。

```python
from autotrader.backtest.simulator import (
    SimulatorConfig,
    TradeSimulator,
)

config = SimulatorConfig(
    initial_balance=1_000_000.0,
    spread_pips=1.5,
    max_positions=1,
    default_volume=0.1,
)

simulator = TradeSimulator(config)
trades = simulator.process_candle(candle, signal)
```

**機能**:
- スプレッド・スリッページ考慮
- SL/TP自動判定
- シグナル反転による決済
- ドローダウン追跡
- 戦略別ポジション管理

## アーキテクチャ

```
src/autotrader/
├── backtest/           # バックテストエンジン
│   ├── fast_backtest.py   # 並列バックテスト
│   ├── simulator.py       # トレードシミュレーター
│   ├── metrics.py         # パフォーマンス指標
│   └── data_loader.py     # データ読み込み
├── calculator/         # 指標計算
│   ├── technical/         # テクニカル指標
│   │   ├── trend.py       # トレンド指標（SMA, EMA）
│   │   ├── momentum.py    # モメンタム指標（RSI, MACD）
│   │   └── volatility.py  # ボラティリティ指標（ATR, BB）
│   ├── features/          # 特徴量計算
│   └── precompute.py      # 事前計算エンジン
├── constraint/         # 制約・フィルター
│   ├── hard_guard.py      # ハードフィルター
│   └── filters/           # 各種フィルター
├── decision/           # 売買判断
│   └── unified/           # 統合トレードボット
│       ├── trade_bot.py       # メインボット
│       ├── timeframe_evaluator.py  # 時間足評価
│       ├── signal_consolidator.py  # シグナル統合
│       └── position_manager.py     # ポジション管理
├── config/             # 設定
│   └── settings.py        # アプリケーション設定
└── core/               # コアモジュール
    ├── entities.py        # エンティティ定義
    └── enums.py           # 列挙型定義
```

## テスト

```bash
# 全テスト実行
uv run pytest

# カバレッジ付き
uv run pytest --cov=autotrader --cov-report=term-missing

# 特定のテスト
uv run pytest tests/unit/test_calculator.py -v
```

## 開発

### コードスタイル

```bash
# リンター
uv run ruff check src/

# フォーマッター
uv run ruff format src/

# 型チェック
uv run mypy src/autotrader/
```

### 設定ファイル

- `pyproject.toml`: プロジェクト設定、依存関係
- `ruff`: PEP8準拠、79文字制限

## ライセンス

MIT License

## 注意事項

- 本ソフトウェアは教育・研究目的で開発されています
- 実際のトレードで使用する場合は、十分なテストと検証を行ってください
- 過去のパフォーマンスは将来の結果を保証するものではありません
- 投資判断は自己責任で行ってください
