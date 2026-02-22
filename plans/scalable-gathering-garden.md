# LLM統合・経済データ収集・未実装プラン 一括実装計画

## Context

バックテストでLLMとファンダメンタルデータを活用するため、以下を一括実装する：
1. 経済イベント過去データの収集・CSVエクスポートシステム
2. BacktestFundamentalProviderのLLMバイアスシミュレーション強化
3. `steady-strolling-rose.md`（bonus_max_positions + pip_helper）
4. `unified-shimmying-sparkle.md`（通貨ペア別プリセット設定）

## 現状の制約と設計方針

### データソース（複数併用）
| ソース | 状態 | 取得範囲 |
|--------|------|----------|
| **ForexFactory（改良版）** | 現在時刻のみ→改良 | 過去週も取得可 |
| **MT5カレンダー** | 既存実装あり | 過去データ取得可能 |
| **FRED API** | 未実装 | USD指標の高品質補完 |

### BacktestFundamentalProvider現状
- CSV読み込みは完全実装済み（`load_csv()`）
- `get_context()` が `macro_bias_score=0.0` を返す（LLMシミュレーションなし）
- `data/fundamental/` ディレクトリが存在しない

---

## PR 1: 経済データ収集システム (`feat/fundamental-data-collector`)

### 変更ファイル一覧

| # | ファイル | 変更種別 |
|---|---------|---------|
| 1 | `src/autotrader/adapters/fundamental/forex_factory.py` | 改良 |
| 2 | `src/autotrader/adapters/fundamental/backtest_provider.py` | 改良 |
| 3 | `scripts/collect_fundamental_data.py` | 新規 |
| 4 | `scripts/run_backtest.py` | `--fundamental` フラグ追加 |

### Step 1: ForexFactory に過去週取得メソッドを追加

**ファイル**: `src/autotrader/adapters/fundamental/forex_factory.py`

`ForexFactoryClient` に以下を追加：

```python
def fetch_historical_year(
    self,
    year: int,
    currencies: list[str] | None = None,
) -> list[EconomicEvent]:
    """指定年の経済イベントを週ごとにスクレイピング

    ?week=jan01.YYYY 形式で全52週を取得。
    レートリミット: 週間呼び出しは無制限（バッチ収集用）

    Args:
        year: 対象年
        currencies: 対象通貨リスト

    Returns:
        list[EconomicEvent]: 年間全イベント
    """
```

- 週のURLを `?week=jan01.2024`, `?week=jan08.2024` ... と生成してループ
- 各週のHTMLをパースして `_parse_html()` を再利用
- 重複排除して返す

### Step 2: BacktestFundamentalProvider にLLMシミュレーション追加

**ファイル**: `src/autotrader/adapters/fundamental/backtest_provider.py`

`get_context()` を改良してLLMバイアスをシミュレート：

```python
def _estimate_bias_from_events(
    self,
    released_events: list[EconomicEvent],
    symbol: str,
) -> tuple[float, str]:
    """発表済みイベントからバイアスを計算

    surprise_magnitude（実績/予測乖離率）から
    symbol の通貨方向バイアスを計算する。

    Returns:
        tuple[float, str]: (bias_score, summary)
    """
```

バイアス計算ロジック：
- 実績 > 予測 → 発表通貨に対してポジティブバイアス
- 実績 < 予測 → 発表通貨に対してネガティブバイアス
- 高インパクト指標はバイアスを3倍で適用
- バイアスは -1.0〜+1.0 にクリップ

`get_context()` を以下に変更：
- `macro_bias_score`: 直前24時間の発表済みイベントのバイアス集計
- `post_event_bias_score`: 直前4時間の発表済み指標バイアス

### Step 3: 収集スクリプト作成

**ファイル**: `scripts/collect_fundamental_data.py`（新規）

```
使用方法:
  python scripts/collect_fundamental_data.py --year 2024 --source ff
  python scripts/collect_fundamental_data.py --year 2024 --source mt5
  python scripts/collect_fundamental_data.py --years 2018-2024 --source ff

オプション:
  --year YYYY        単一年指定
  --years YYYY-YYYY  年範囲指定
  --source ff/mt5/fred  データソース選択
  --currencies USD,JPY,EUR,GBP,...
  --output data/fundamental/  出力先ディレクトリ
```

出力形式: `data/fundamental/events_YYYY.csv`

```csv
event_id,event_time,currency,event_name,impact,actual,forecast,previous
ff_a1b2c3d4,2024-01-05T13:30:00+00:00,USD,Non-Farm Payrolls,high,216000,175000,199000
```

### Step 4: run_backtest.py に `--fundamental` フラグ追加

**ファイル**: `scripts/run_backtest.py`

引数追加:
```python
parser.add_argument(
    "--fundamental",
    action="store_true",
    help="経済イベントCSVを自動読み込み（data/fundamental/events_YYYY.csv）",
)
parser.add_argument(
    "--fundamental-dir",
    default="data/fundamental",
    help="経済イベントCSVディレクトリ",
)
```

`--fundamental` が指定された場合、`BacktestRunner.run()` に
`fundamental_csv` リストを渡す（複数年に対応）。

---

## PR 2: 未実装プラン一括実装 (`feat/live-presets-and-bonus`)

### 変更ファイル一覧

| # | ファイル | 変更種別 |
|---|---------|---------|
| 1 | `src/autotrader/decision/unified/config.py` | bonus フィールド追加 |
| 2 | `src/autotrader/live/engine.py` | bonus チェック + pip ヘルパー |
| 3 | `config/symbol_presets.yaml` | 新規作成 |
| 4 | `src/autotrader/config/trading_params.py` | SymbolPreset + get_preset 追加 |
| 5 | `src/autotrader/config/__init__.py` | エクスポート追加 |
| 6 | `src/autotrader/backtest/runner.py` | from_preset() 追加 |
| 7 | `src/autotrader/backtest/service.py` | from_preset() 追加 |
| 8 | `scripts/run_backtest.py` | プリセット自動適用 |
| 9 | `src/autotrader/config/config_loader.py` | symbol キー対応 |
| 10 | `config/live_trading.yaml` | symbol: USDJPY 追加 |
| 11 | `tests/unit/config/test_symbol_preset.py` | TDDテスト |

詳細実装は `plans/steady-strolling-rose.md` および `plans/unified-shimmying-sparkle.md` 参照。
既存プランに定義されたコードをそのまま実装する。

---

## ワークフロー（2 PR 並列実施）

### PR 1: データ収集
```bash
BRANCH="feat/fundamental-data-collector"
WORKTREE="/d/Projects/AutoTraderV4/tmp/feat_fundamental-data-collector"
git -C /d/Projects/AutoTraderV4 pull origin main
git -C /d/Projects/AutoTraderV4 branch "$BRANCH"
git -C /d/Projects/AutoTraderV4 worktree add "$WORKTREE" "$BRANCH"
# 編集後
git -C "$WORKTREE" add src/ scripts/
git -C "$WORKTREE" commit -m "feat: 経済イベントデータ収集システムとLLMシミュレーション追加"
git -C "$WORKTREE" push -u origin "$BRANCH"
"C:/Program Files/GitHub CLI/gh.exe" pr create --repo KaePen/AutoTraderV4 --base main ...
git -C /d/Projects/AutoTraderV4 worktree remove "$WORKTREE" --force
git -C /d/Projects/AutoTraderV4 branch -d "$BRANCH"
```

### PR 2: プラン実装
```bash
BRANCH="feat/live-presets-and-bonus"
WORKTREE="/d/Projects/AutoTraderV4/tmp/feat_live-presets-and-bonus"
git -C /d/Projects/AutoTraderV4 pull origin main
git -C /d/Projects/AutoTraderV4 branch "$BRANCH"
git -C /d/Projects/AutoTraderV4 worktree add "$WORKTREE" "$BRANCH"
# 編集後
git -C "$WORKTREE" commit -m "feat: bonus_max_positions・pip_helper・シンボルプリセット実装"
git -C "$WORKTREE" push -u origin "$BRANCH"
```

---

## 検証手順

### PR 1 検証
```bash
# 1. テスト実行
python -m pytest tests/unit/adapters/fundamental/ -v

# 2. データ収集試行（ForexFactory）
python scripts/collect_fundamental_data.py --year 2024 --source ff

# 3. バックテスト実行（ファンダメンタルデータあり）
python scripts/run_backtest.py --symbol USDJPY --years 2024 --fundamental --no-parallel

# 4. ログで以下を確認
# [BacktestFundamental] XX件読込: events_2024.csv
# [Fundamental] macro_bias_score が 0.0 以外を返すケースがある
```

### PR 2 検証
```bash
# 1. テスト実行
python -m pytest tests/unit/config/test_symbol_preset.py -v
python -m pytest tests/ -x -q

# 2. EURUSDプリセットでバックテスト
python scripts/run_backtest.py --symbol EURUSD --years 2024 --no-parallel
```
