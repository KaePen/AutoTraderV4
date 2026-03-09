# マルチ通貨ペア同時実行バックテスト（時系列インターリーブ方式）

## Context

現行バックテストは「1通貨ペア × 複数年並列」で各ペアが独立資金プール（1M円）を持つ。
ライブでは1口座で6ペアが資金を共有するため、構造的乖離がある。

**目的**: 6 JPYペアを時系列インターリーブで同時実行し、共有資金プール＋グローバルポジション制限で
ライブに近い条件を再現。品質フィルタ（CT）＋リスク調整で最適設定を特定する。

## 設計

### アーキテクチャ: 時系列インターリーブ方式

```
Year 2020:
  6ペアの基準TFバーを時系列順にマージ
  → bar(USDJPY, 09:00) → bar(EURJPY, 09:00) → bar(GBPJPY, 09:00) → ...
  → bar(USDJPY, 09:05) → bar(EURJPY, 09:05) → ...

  共有状態:
    portfolio.equity: 1,000,000（全ペア共通）
    portfolio.global_open_positions: 合計ポジション数
    portfolio.global_exposure_lot: 合計ロット
```

### なぜ run_unified_year() を再利用しないか

`run_unified_year()` は1ペア完結型でペア間共有stateを持てない。
グローバルポジション制限（「全ペア合計6ポジションまで」等）の実装には、
バーループを再実装してペア間の状態を共有する必要がある。

### コア構造

```python
# ペアごとに独立した Bot + Simulator を保持
pair_contexts: dict[str, PairContext]  # {symbol: (bot, simulator, arrays, ...)}

# 共有状態（全ペアで1つ）
@dataclass
class PortfolioState:
    equity: float                    # 共有資金
    initial_equity: float
    peak_equity: float
    global_open_positions: int       # 全ペア合計ポジション数
    global_exposure_lot: float       # 全ペア合計ロット
    per_pair_positions: dict[str, int]
    per_pair_exposure: dict[str, float]
    blocked_global: int              # グローバル制限発動回数
    blocked_per_pair: int            # ペア別制限発動回数
```

### 処理フロー（1年分）

```
1. 6ペアのデータをロード（BacktestRunner.load_data() 利用）
2. 各ペアの基準TFタイムスタンプをマージ・ソート
3. 各タイムスタンプで全ペアを順次処理:
   a. simulator.state.balance = portfolio.equity  （共有equity同期）
   b. bot.state に global exposure を反映
   c. signal = bot.generate_signal()
   d. グローバル制限チェック → 制限超過なら signal = None
   e. balance_before = simulator.state.balance
   f. simulator.process_candle(candle, signal)
   g. pnl_delta = simulator.state.balance - balance_before
   h. portfolio.equity += pnl_delta, ポジション数/ロット更新
4. 月変わりで月次PnL記録
5. 年末に全ポジション強制決済
```

### シグナルゲーティング（グローバル制限）

`process_candle()` は exit と entry を1回で処理する。グローバル制限は
**process_candle の前に signal を None 化**することで実現:

```python
if signal and not portfolio.can_open_position(sym, config):
    signal = None  # エントリーブロック
    portfolio.blocked_global += 1
```

これにより既存コードを変更せず、exit 処理は常に実行しつつ entry のみブロックできる。

### Equity 同期戦略

```python
# 各ペアの candle 処理前に共有 equity を同期
ctx.simulator.state.balance = portfolio.equity
ctx.simulator.state.equity = portfolio.equity
bot.state = dataclasses.replace(bot.state, equity=portfolio.equity)

# 処理後の PnL 差分をキャプチャ
pnl_delta = ctx.simulator.state.balance - balance_before
portfolio.equity += pnl_delta
```

### ロット計算

- `portfolio.equity × config.base_risk_pct` で全ペア統一リスクのロットを算出
- per-pair の `max_lot_per_trade` は各ペアの preset 値を使用

## テストマトリクス

| テスト | global_max_pos | per_pair_max | max_exposure | risk% | CT | 狙い |
|--------|---------------|-------------|--------------|-------|-----|------|
| M0 | 6 | 1 | 10.0 | 0.02 | 9.0 | ベースライン |
| M1 | 6 | 1 | 10.0 | 0.02 | 10.0 | 軽い品質UP |
| M2 | 6 | 1 | 10.0 | 0.02 | 11.0 | 中程度品質UP |
| M3 | 6 | 1 | 10.0 | 0.02 | 12.0 | 強い品質UP |
| M4 | 6 | 2 | 10.0 | 0.015 | 11.0 | 2pos/pair |
| M5 | 8 | 2 | 12.0 | 0.015 | 10.0 | 8pos合計 |
| M6 | 4 | 1 | 6.0 | 0.03 | 12.0 | 少数精鋭 |

## 実装計画

### 新規ファイル: `scripts/run_multi_pair_backtest.py`

#### 関数構成

1. **データクラス**
   - `MultiPairConfig` — テストケースのパラメータ
   - `PortfolioState` — 共有state + 制限チェック + 月次追跡
   - `PairContext` — ペアごとの bot/simulator/arrays

2. **`load_pair_data(symbol, data_dir)`**
   - `BacktestServiceConfig.from_preset()` → `BacktestService.create_runner()` → `runner.load_data()`
   - 既存パターンを `run_portfolio_backtest.py` から再利用

3. **`build_bot_config(symbol, multi_config)`**
   - `run_portfolio_backtest.py:build_bot_config()` のロジックを再利用
   - `consensus_threshold`, `base_risk_pct` をテストケースから注入
   - signal設定は YAML から読み込み

4. **`setup_pair_context(symbol, runner, year, bot_config)`**
   - `year_runner.py:77-155` を参考に bot + simulator + CandleArrays を初期化
   - 基準TF選択: M5 > M15 > H1 優先（M1は不使用 — マルチペアでは重すぎる）

5. **`run_multi_pair_year(year, contexts, multi_config, portfolio)`** — 核心
   - タイムスタンプマージ → インターリーブループ
   - `year_runner.py:155-700` を参考にバーイテレーション再実装
   - equity同期 → signal生成 → グローバル制限 → process_candle → state更新

6. **`run_test_case(test_case, runners, symbols)`**
   - 2020-2025 の6年を逐次実行（年ごとにfresh bot/simulator）
   - データは `runners` からキャッシュ再利用

7. **`aggregate_results(test_name, portfolio, all_trades)`**
   - 月次PnL → DD/Sharpe/WR/PF 計算
   - `run_portfolio_backtest.py:aggregate_portfolio()` のロジックを再利用

8. **`generate_report(results)`**
   - `reports/multi_pair_backtest.md` に出力
   - テストマトリクスサマリー + ペア別内訳 + 制限発動統計

9. **`main()`**
   - CLI: `--data-dir`, `--tests`（M0,M1,...）, `--symbols`
   - データロード（6ペア、1回のみ）→ テストマトリクス実行 → レポート

### 参照ファイル（変更なし）

| ファイル | 参照内容 |
|---------|---------|
| `autotrader/backtest/year_runner.py` | バー処理ループ (L155-700)、月次集計 |
| `autotrader/backtest/simulator.py` | TradeSimulator API、SimulatorState |
| `autotrader/backtest/runner.py` | BacktestRunner データロード |
| `autotrader/backtest/service.py` | BacktestService.create_runner() |
| `autotrader/decision/unified/trade_bot.py` | BotState, generate_signal() |
| `scripts/run_portfolio_backtest.py` | build_bot_config(), 集約ロジック |
| `config/symbol_presets.yaml` | プリセット値 |

## データ・ログ出力設計

### データ入力

既存の `data/{SYMBOL}/` 構造をそのまま利用。各ペアの BacktestRunner が独立してデータをロード。

```
{data_dir}/
├── USDJPY/chart/    # TFごとのCSV/Parquet
├── EURJPY/chart/
├── GBPJPY/chart/
├── AUDJPY/chart/
├── CADJPY/chart/
└── CHFJPY/chart/
```

- チャートデータ: `{data_dir}/{SYMBOL}/chart/`
- インジケータキャッシュ: `{data_dir}/{SYMBOL}/.indicator_cache/`（既存キャッシュを再利用）

### ログ出力先

マルチ通貨ペア用の専用フォルダを作成:

```
{log_dir}/
├── USDJPY/              # 既存（単一ペアバックテスト用）
├── EURJPY/
├── ...
└── multi_pair/          # 新規: マルチ通貨ペア専用
    ├── summary_{YYYYMMDD_HHMMSS}.log     # ポートフォリオ全体サマリー
    ├── trades_{YYYYMMDD_HHMMSS}.csv      # 全ペアのトレードを1ファイルに統合
    └── monthly_{YYYYMMDD_HHMMSS}.csv     # 月次PnL推移
```

**ログパス解決**: `autotrader/config/paths.py` の `get_log_dir()` に従い、
`D:/Projects/AutoTraderV4_data/logs/multi_pair/` に出力。

### トレードCSVフォーマット

既存カラム（58列）をそのまま利用（symbolカラムでペア識別）。
マルチペア固有の情報を3列追加:

```
追加カラム:
  global_positions_at_entry    # エントリー時の全ペア合計ポジション数
  global_exposure_at_entry     # エントリー時の全ペア合計ロット
  portfolio_equity_at_entry    # エントリー時のポートフォリオ残高
```

### サマリーログ内容

```
=== マルチ通貨ペアバックテスト サマリー ===
テスト名: M2_CT11
期間: 2020-2025
初期残高: 1,000,000

--- ポートフォリオ全体 ---
総利益: +X,XXX,XXX
年間収益率: XX.X%
最大DD: X.XX%
Sharpe: X.XX
WR: XX.X% (XXXX wins / XXXX trades)
PF: X.XX
月間勝率: XXX.X%

--- 通貨ペア別内訳 ---
| ペア     | 利益     | WR    | PF   | Trades | 寄与率 |
|----------|----------|-------|------|--------|--------|
| USDJPY   | +XXX,XXX | XX.X% | X.XX | XXX    | XX.X%  |
| EURJPY   | +XXX,XXX | XX.X% | X.XX | XXX    | XX.X%  |
| ...

--- グローバル制限発動統計 ---
| 制限種別              | 発動回数 | スキップされたシグナル数 |
|-----------------------|----------|-------------------------|
| global_max_positions  | XXX      | XXX                     |
| per_pair_max_positions| XXX      | XXX                     |
| global_max_exposure   | XXX      | XXX                     |
```

## パフォーマンス見積もり

- M5基準: ~75K bars/year/pair × 6 pairs = ~450K iterations/year
- 6年 = ~2.7M iterations/test case
- 7テストケース = ~19M iterations
- 推定: 1テスト15-30分、全体2-3.5時間
- 最適化: データロードは1回のみ（テスト間でキャッシュ）

## 検証方法

1. M0テスト単体が完走し、月次・年次メトリクスが生成されること
2. `global_max_positions=6` で7個目のエントリーがブロックされること
3. 制限発動統計がレポートに出力されること
4. 共有equity でDD計算が正しいこと（全ペアのPnLが1つのequity curveに反映）
5. 独立プール方式（S0-S6）と比較して妥当な数値であること（利益は低下、DDも低下想定）
6. テストマトリクス全体で WR>65%, DD<5% の設定を特定

## レポート出力

最終結果を `reports/multi_pair_backtest.md` に出力
