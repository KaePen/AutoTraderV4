# AutoTraderV4

FX自動トレードシステム — MetaTrader 5連携、マルチタイムフレーム分析、8通貨ペア同時運用

---

## 1. はじめに

AutoTraderV4は、外国為替（FX）取引を自動化するPythonベースのトレーディングシステムです。
M1からD1まで8つの時間足を同時に分析し、コンセンサススコアリングでエントリー判断を行います。

### 特徴

- **8通貨ペア同時運用**: USDJPY, EURJPY, GBPJPY, AUDJPY, CADJPY, CHFJPY, EURUSD, GBPUSD
- **マルチタイムフレーム分析**: M1〜D1の8時間足を並列評価
- **テクニカル指標**: MACD, RSI, ADX, ストキャスティクス, ボリンジャーバンド, SMC
- **多層リスク管理**: ATRベース動的SL/TP、HardGuard/SoftGuardの二重防御
- **バックテスト/リアル共通ロジック**: 同一コードで検証と運用が可能

---

## 2. 環境準備

### 2.1 必要なソフトウェア

| ソフトウェア | バージョン | 用途 |
|-------------|-----------|------|
| **Python** | 3.12以上 | ボット本体 |
| **uv** | 最新 | Pythonパッケージ管理 |
| **Git** | 最新 | ソースコード管理 |
| **MetaTrader 5** | 最新 | FXブローカー接続（Windows専用） |

### 2.2 Python のインストール

公式サイトからPython 3.12以上をダウンロード・インストールしてください。

https://www.python.org/downloads/

インストール時に **「Add Python to PATH」にチェック** を入れてください。

確認:
```bash
python --version
# Python 3.12.x が表示されればOK
```

### 2.3 uv のインストール

`uv` はPythonのパッケージマネージャーです。pipより高速に依存関係を解決します。

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

確認:
```bash
uv --version
```

### 2.4 リポジトリの取得

```bash
git clone https://github.com/KaePen/AutoTraderV4.git
cd AutoTraderV4
```

### 2.5 Visual C++ ランタイムのインストール（Windows必須）

`numba` ライブラリが依存する Microsoft Visual C++ ランタイムが必要です。
インストールされていない場合、起動時に `OSError: Could not find/load shared object file` エラーが発生します。

```powershell
# winget でインストール（推奨）
winget install Microsoft.VCRedist.2015+.x64
```

または [Microsoft 公式](https://aka.ms/vs/17/release/vc_redist.x64.exe) からダウンロードしてインストールしてください。

> インストール後、Windowsを**再起動**してから次の手順へ進んでください。

### 2.6 依存パッケージのインストール

```powershell
# 基本インストール（ライブトレードに必要な最小構成）
uv sync

# MT5連携を含む（推奨）
uv sync --extra mt5

# 全機能
uv sync --all-extras
```

#### extras 一覧

| extras名 | 内容 | いつ必要か |
|---------|------|-----------|
| `mt5` | MetaTrader5, pywin32 | リアルトレード時（Windows専用） |
| `live` | feedparser | RSSニュース取得 |
| `fast` | numba | JITコンパイル高速化 |
| `gdelt` | requests | GDELTニュース取得 |
| `bigquery` | google-cloud-bigquery | GDELT BigQuery |
| `dev` | pytest, mypy, ruff | 開発・テスト |

#### MT5パッケージのインストール確認

`uv sync --extra mt5` 実行後、MetaTrader5 パッケージが正しくインストールされたか確認してください:

```powershell
.venv\Scripts\python.exe -c "import MetaTrader5; print('MetaTrader5 OK')"
```

`MetaTrader5 OK` と表示されれば正常です。エラーが出る場合は個別にインストールしてください:

```powershell
.venv\Scripts\pip install MetaTrader5
```

### 2.7 MetaTrader 5 の準備

1. ブローカーからMT5をダウンロード・インストール
2. デモまたはリアル口座を開設
3. MT5にログインし、対象通貨ペアのチャートを開く
4. ツール → オプション → Expert Advisors で「アルゴリズム取引を許可」を有効化

### 2.8 環境変数の設定（任意）

`.env` ファイルをプロジェクトルートに作成すると、起動時に自動読み込みされます:

```env
# MT5接続情報
MT5_LOGIN=123456
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker-Server
MT5_TERMINAL_PATH=C:/Program Files/MetaTrader 5/terminal64.exe

# 自動トレードON/OFF
AUTOTRADER_AUTO_TRADE=false

# ログレベル
LOG_LEVEL=INFO
```

> **注意**: `.env` ファイルにはパスワードが含まれます。gitにコミットしないでください（`.gitignore` で除外済み）。
>
> **Windows の注意**: `.env` は必ず **UTF-8 (BOM なし)** で保存してください。メモ帳や PowerShell の `>` リダイレクトはデフォルトで UTF-16 になるため、起動時に `UnicodeDecodeError` が発生します。
> ```powershell
> # UTF-8 BOMなしで .env を作成する方法
> [System.IO.File]::WriteAllText(".env", (Get-Content ".env" -Raw), [System.Text.UTF8Encoding]::new($false))
> ```

---

## 3. 起動方法

### 3.1 かんたん起動（推奨）

プロジェクトルートの `start_at4.bat` をダブルクリックするだけで起動できます。

```
AutoTraderV4/
  start_at4.bat    ← これをダブルクリック
```

コマンドプロンプトが開き、ログが表示されます。
起動後、ブラウザで http://localhost:8000 にアクセスしてダッシュボードを確認できます。

停止するにはコマンドプロンプトで `Ctrl+C` を押してください。

### 3.2 コマンドラインから起動

```bash
cd AutoTraderV4
uv run python -m autotrader.web
```

### 3.3 MT5接続付きで起動

環境変数でMT5の接続情報を指定して起動します:

```bash
MT5_LOGIN=123456 \
MT5_PASSWORD=your_password \
MT5_SERVER=YourBroker-Server \
MT5_TERMINAL_PATH="C:/Program Files/MetaTrader 5/terminal64.exe" \
AUTOTRADER_AUTO_TRADE=true \
uv run python -m autotrader.web
```

または `.env` ファイルに記載しておけば、引数なしで起動できます。

### 3.4 Web UI

起動後、以下のURLでダッシュボードにアクセスできます:

| URL | 内容 |
|-----|------|
| http://localhost:8000 | メインダッシュボード |
| http://localhost:8000/api/v1/health | ヘルスチェック |

#### 主要エンドポイント

| パス | 説明 |
|------|------|
| `/` | ダッシュボード画面 |
| `/api/v1/dashboard` | 口座情報・サマリー |
| `/api/v1/signals` | シグナル一覧 |
| `/api/v1/positions` | オープンポジション |
| `/api/v1/trades` | 取引履歴 |
| `/api/v1/settings` | パラメータ設定（GET/PUT） |
| `/ws/market/{symbol}` | 市場データWebSocket |
| `/ws/dashboard` | ダッシュボードWebSocket |

#### Web UI 環境変数

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `AUTOTRADER_WEB_HOST` | `0.0.0.0` | ホスト |
| `AUTOTRADER_WEB_PORT` | `8000` | ポート |
| `AUTOTRADER_WEB_DEBUG` | `false` | デバッグモード |

---

## 4. ファイル構造

```
AutoTraderV4/
├── start_at4.bat               # ワンクリック起動バッチ
├── pyproject.toml               # プロジェクト定義・依存関係
├── config/
│   ├── trading_defaults.yaml    # 全トレードロジックのSSOT（グローバルデフォルト）
│   ├── symbol_overrides.yaml    # 通貨ペア別上書き（symbols.{SYMBOL} のみ参照）
│   ├── modes.yaml               # demo/live モード差分
│   ├── symbol_presets.yaml      # SymbolPreset レガシーソース（後方互換）
│   └── accounts.yaml            # MT5アカウント情報
│
├── autotrader/                  # === メインパッケージ ===
│   ├── core/                    # 共通エンティティ・インターフェース
│   ├── calculator/              # 指標計算（バックテスト・リアル共通）
│   ├── constraint/              # トレード制約（HardGuard/SoftGuard）
│   ├── decision/unified/        # シグナル生成・ポジション管理
│   ├── backtest/                # バックテスト実行基盤
│   ├── live/                    # リアルトレード実行基盤
│   ├── adapters/                # 外部サービスアダプタ（MT5, DB, LLM）
│   ├── web/                     # FastAPI Web UI
│   └── config/                  # 設定管理
│
├── scripts/                     # ユーティリティスクリプト
├── tests/                       # テストコード
└── .env                         # 環境変数（git管理外）
```

### アーキテクチャの特徴

バックテストとリアルトレードは**同一のトレードロジック**を共有しています:

```
バックテスト:   CSV → 前処理 → [共通ロジック] → メトリクス出力
リアルトレード: MT5 →          [共通ロジック] → MT5注文実行
```

`calculator/`, `constraint/`, `decision/` の3層はバックテスト・リアルの両方から呼び出されます。
これにより、バックテスト結果とリアルトレードの乖離を最小化しています。

---

## 5. ボットの性能

### 8ペアマルチバックテスト結果

初期資金100万円、2020〜2025年のM1データで検証した結果です。

#### インサンプル（IS: 2023-2025）

| 指標 | 値 |
|------|-----|
| プロフィットファクター | 3.52 |
| シャープレシオ | 7.24 |
| 最大ドローダウン | 1.79% |
| 勝率 | 86.0% |
| 純利益 | +3,467,000円 |
| 取引回数 | 3,022回 |

#### アウトオブサンプル（OOS: 2020-2022）

| 指標 | 値 |
|------|-----|
| プロフィットファクター | 3.34 |
| シャープレシオ | 8.03 |
| 最大ドローダウン | 1.77% |
| 勝率 | 84.9% |
| 純利益 | +2,522,000円 |
| 取引回数 | 2,303回 |

#### スプレッドストレステスト（IS 2023-2025）

スプレッドを通常の1〜3倍に拡大した場合の耐性テストです:

| スプレッド倍率 | PF | 最大DD | 純利益 |
|--------------|-----|--------|--------|
| x1.0（通常） | 3.08 | 2.15% | 3.48M |
| x1.5 | 2.71 | 2.28% | 3.02M |
| x2.0 | 2.19 | 4.01% | 2.32M |
| x3.0 | 1.54 | 4.28% | 1.19M |

> スプレッド3倍でもPF 1.54（利益 > 損失）を維持しています。

### 運用構成

| 項目 | 設定値 |
|------|--------|
| 対象ペア | 8ペア（6 JPY + 2 USD） |
| 最大同時ポジション | 4 |
| JPY同方向上限 | 3ペアまで |
| 1トレードリスク | 0.5%（DD 2%目標） |
| コンセンサス閾値 | 18.0 |

---

## 6. 通貨ペア別設定

`config/symbol_presets.yaml` で通貨ペアごとのパラメータを一元管理しています:

| カテゴリ | パラメータ例 |
|---------|------------|
| 基本 | `pip_value`, `pip_unit`, `spread_pips`, `slippage_pips` |
| SL/TP | `default_sl_pips`, `default_tp_pips` |
| リスク | `base_risk_pct`, `max_positions` |
| シグナル | `consensus_threshold`, `bca_min_edge` |
| ポジション管理 | `trailing_start_r`, `partial_close_1r_ratio` |

バックテストでもリアルでも同じ `get_preset("USDJPY")` で取得します。

---

## 7. バックテスト

### キューランナー（推奨）

バックテストは**キューランナー**経由で実行します。別ターミナルで常駐起動:

```bash
uv run python scripts/backtest_queue_runner.py --cpu-threads 12
```

キューファイルにジョブを記述して投入:

```json
{
  "jobs": [
    {
      "symbol": "USDJPY",
      "years": "2020-2025",
      "description": "USDJPY検証"
    }
  ]
}
```

#### 対話コマンド

| コマンド | 動作 |
|---------|------|
| `status` | 稼働状態・進捗を表示 |
| `pause` / `resume` | 一時停止 / 再開 |
| `stop` | 全タスク停止 |
| `cpu N` | CPUスレッド数を変更 |
| `quit` | ランナー終了 |

### 直接実行（開発・デバッグ用）

```bash
# シングルペア
uv run python scripts/run_backtest.py --symbol USDJPY --years 2023-2025

# マルチペア
uv run python scripts/run_multi_pair_backtest.py --tests R1
```

---

## 8. 開発

### テスト

```bash
uv run pytest                    # 全テスト実行
uv run pytest -v                 # 詳細出力
uv run pytest tests/unit/        # ユニットテストのみ
```

### コード品質

```bash
uv run ruff check autotrader/    # リンター
uv run ruff format autotrader/   # フォーマッター
uv run mypy autotrader/          # 型チェック
```

---

## 注意事項

- 本ソフトウェアは教育・研究目的で開発されています
- 実際のトレードで使用する場合は、十分なテストと検証を行ってください
- 過去のパフォーマンスは将来の結果を保証するものではありません
- 投資判断は自己責任で行ってください

## ライセンス

MIT License
