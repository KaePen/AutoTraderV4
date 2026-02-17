# バックテストログ分離・強化計画

## Context

現在のバックテストログは1ファイル（`backtest_YYYYMMDD_HHMMSS.log`）に年別統計のみ出力。
トレード判断の根拠（各指標のスコア内訳）やなぜ負けたかの分析ができない。

**目的**: サマリーログ + 詳細トレードCSVの2ファイル出力にし、負けパターンの定量分析を可能にする。

**核心的問題**: `_calculate_score`で計算される7指標の個別貢献値がfloatに集約され消失している。

---

## 出力ファイル仕様

### 1. サマリーログ (`summary_YYYYMMDD_HHMMSS.log`)
- バックテスト開始/終了情報
- 年別統計（取引数、勝率、PF、純利益、最大DD、シャープ）
- 月別統計（verbose時）
- **追加**: Exit理由別統計（TP/SL/SIGNAL_REVERSAL別の勝率・損益）
- **追加**: 戦略モード別統計（SCALP/DAY/SWING別の勝率・損益）
- **追加**: レジーム別統計（TREND/RANGE/VOLATILE別の勝率・損益）

### 2. 詳細トレードCSV (`trades_YYYYMMDD_HHMMSS.csv`)

| カラム | 説明 |
|--------|------|
| trade_id | トレードID |
| direction | BUY/SELL |
| entry_time | エントリー時刻 |
| exit_time | 決済時刻 |
| holding_minutes | 保有時間（分） |
| entry_price | エントリー価格 |
| exit_price | 決済価格 |
| pips | 損益pips |
| profit_loss | 損益額 |
| exit_reason | 決済理由（TP/SL/SIGNAL_REVERSAL等） |
| regime | レジーム（TREND/RANGE等） |
| mode | 戦略モード（SCALP/DAY/SWING等） |
| confidence | 信頼度 |
| consensus_score | コンセンサススコア |
| sl_pips | 設定SL |
| tp_pips | 設定TP |
| score_trend | トレンド判定スコア（0-5.0） |
| score_adx | ADXボーナス（0/+2.0） |
| score_rsi | RSIフィルター（0/+1.0/-999） |
| score_macd_slope | MACDスロープ（±2.5） |
| score_divergence | ダイバージェンス（±2.0/±1.5） |
| score_ema_cross | EMAクロス（±2.5/±0.5） |
| score_stochastic | ストキャスティクス（±1.5/±0.5） |
| score_htf | HTF整合ボーナス |
| filters_applied | 適用フィルター |
| rationale | 判断理由テキスト |

---

## 実装手順（5ステップ）

### Step 1: ScoreBreakdown導入（データの源流）

**ファイル**: `src/autotrader/decision/unified/timeframe_evaluator.py`

1-1. `ScoreBreakdown` frozen dataclass を追加（TimeframeSignalの前に定義）
```python
@dataclass(frozen=True)
class ScoreBreakdown:
    trend: float = 0.0        # トレンド判定 (0-5.0)
    adx: float = 0.0          # ADX強度 (0/+2.0)
    rsi: float = 0.0          # RSI (0/+1.0, 過熱=-999)
    macd_slope: float = 0.0   # MACDスロープ (±2.5)
    divergence: float = 0.0   # ダイバージェンス (±2.0/±1.5)
    ema_cross: float = 0.0    # EMAクロス (±2.5/±0.5)
    stochastic: float = 0.0   # ストキャスティクス (±1.5/±0.5)
    htf: float = 0.0          # HTF整合性

    def to_dict(self) -> dict[str, float]:
        return asdict(self)
```

1-2. `_calculate_score` の戻り値を拡張:
- 現在: `tuple[float, float, list[str]]`
- 変更後: `tuple[float, float, list[str], ScoreBreakdown]`
- 各指標計算箇所で個別変数に記録し、最後にScoreBreakdownを生成

1-3. `TimeframeSignal` に `score_breakdown: ScoreBreakdown | None = None` フィールド追加

1-4. `evaluate` メソッドで `_calculate_score` の4番目の戻り値を受け取り、TimeframeSignalに渡す

### Step 2: ConsolidatedSignalにスコア内訳を伝播

**ファイル**: `src/autotrader/decision/unified/signal_consolidator.py`

2-1. `ConsolidatedSignal` に以下のフィールド追加:
```python
# TF別スコア内訳（ログ用）
tf_score_breakdowns: dict[str, dict[str, float]] = field(
    default_factory=dict
)
```

**ファイル**: `src/autotrader/decision/unified/trade_bot.py`

2-2. `_generate_signal_new` の `ConsolidatedSignal` 生成部分（L602-617）で:
- `tf_signals` から各TFの `score_breakdown.to_dict()` を集約
- `ConsolidatedSignal` に `tf_score_breakdowns` を渡す

```python
# L602付近のConsolidatedSignal生成を拡張
tf_breakdowns = {}
for tf_name, sig in tf_signals.items():
    if sig.score_breakdown is not None:
        tf_breakdowns[tf_name] = sig.score_breakdown.to_dict()

return ConsolidatedSignal(
    ...,  # 既存フィールド
    tf_score_breakdowns=tf_breakdowns,
)
```

### Step 3: emit_signalでスコア内訳を渡す

**ファイル**: `src/autotrader/backtest/events.py`

3-1. `SignalEvent` に `score_breakdowns` フィールド追加:
```python
score_breakdowns: dict[str, dict[str, float]] = field(
    default_factory=dict
)
```

3-2. `BacktestEventEmitter.emit_signal` に `score_breakdowns` パラメータ追加

**ファイル**: `src/autotrader/backtest/runner.py`

3-3. emit_signal呼び出し（L1057-1074）に以下を追加:
```python
tf_scores={
    tf: {"buy": sig.buy_strength, "sell": sig.sell_strength}
    for tf, sig in ...  # ← ただしここでtf_signalsにアクセスできない
},
score_breakdowns=consolidated.tf_score_breakdowns,
```

> **注意**: runner.pyからはConsolidatedSignal経由でのみデータにアクセスするため、
> Step 2でConsolidatedSignalにデータを載せることが必須。

3-4. シグナルデータをトレード決済時に紐付けるため、既存の`_pos_mode_regime`パターンを拡張:
```python
# 現在: dict[str, tuple[str, str]]  (opened_at → (mode, regime))
# 拡張: dict[str, dict]  (opened_at → {mode, regime, breakdowns, confidence, ...})
_pos_signal_data: dict[str, dict] = {}
```

emit_signal直後に保存し、emit_trade_closed時に紐付ける。

### Step 4: FileEventListenerの2ファイル出力化

**ファイル**: `src/autotrader/backtest/file_listener.py`

4-1. `__init__` を修正:
```python
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
self.summary_file = self.log_dir / f"summary_{timestamp}.log"
self.trades_file = self.log_dir / f"trades_{timestamp}.csv"

# 旧ファイル名は削除
# self.log_file = ...
```

4-2. シグナル→トレード紐付け用の内部状態を追加:
```python
self._pending_signal: dict | None = None  # 最新シグナル
self._trade_rows: list[dict] = []         # CSV行蓄積
self._exit_stats: dict[str, dict] = {}    # Exit理由別統計
self._mode_stats: dict[str, dict] = {}    # モード別統計
self._regime_stats: dict[str, dict] = {}  # レジーム別統計
```

4-3. `_handle_signal` でシグナルデータを一時保存（max_positions=1なので最新1件で十分）

4-4. `_handle_position_closed` でCSV行を構築:
- TradeEventの基本データ + 保存済みシグナルのスコア内訳を統合
- `_trade_rows` に追加
- 統計カウンターを更新

4-5. `_handle_backtest_end` で:
- CSVファイルを一括書き出し（`csv.DictWriter`使用）
- サマリーファイルにExit理由別/モード別/レジーム別統計を出力

4-6. 既存の年別統計出力（`_handle_year_end`）はサマリーファイルに出力先変更

### Step 5: テスト修正

- `_calculate_score` の戻り値変更に伴うテスト修正（現在テストなし → 影響なし）
- 新規: FileEventListenerの2ファイル出力テスト（任意）

---

## 修正ファイル一覧

| # | ファイル | 変更内容 |
|---|---------|---------|
| 1 | `src/autotrader/decision/unified/timeframe_evaluator.py` | ScoreBreakdown追加、_calculate_score戻り値拡張、TimeframeSignalフィールド追加 |
| 2 | `src/autotrader/decision/unified/signal_consolidator.py` | ConsolidatedSignal.tf_score_breakdowns追加 |
| 3 | `src/autotrader/decision/unified/trade_bot.py` | _generate_signal_newでスコア内訳をConsolidatedSignalに伝播 |
| 4 | `src/autotrader/backtest/events.py` | SignalEvent.score_breakdowns追加、emit_signalパラメータ追加 |
| 5 | `src/autotrader/backtest/runner.py` | emit_signalにscore_breakdowns渡し、_pos_signal_dataパターン追加 |
| 6 | `src/autotrader/backtest/file_listener.py` | 2ファイル出力化（サマリーlog + 詳細CSV） |

---

## データフロー（修正後）

```
_calculate_score → (float, float, list[str], ScoreBreakdown)
    ↓ 各指標の貢献値が個別に保持
evaluate → TimeframeSignal(score_breakdown=...)
    ↓
_generate_signal_new → ConsolidatedSignal(tf_score_breakdowns=...)
    ↓
runner.emit_signal(score_breakdowns=...) → SignalEvent
    ↓
FileEventListener._handle_signal → _pending_signal に蓄積
    ↓
FileEventListener._handle_position_closed → CSV行構築
    ↓
FileEventListener._handle_backtest_end → CSV書き出し + サマリー統計
```

---

## 検証方法

1. バックテスト実行:
```bash
cd /home/yamas/projects/AutoTraderV4
python scripts/run_backtest.py --years 2023 --verbose
```

2. 出力確認:
```bash
ls logs/backtest_log/
# summary_YYYYMMDD_HHMMSS.log と trades_YYYYMMDD_HHMMSS.csv が生成されること

# CSVの確認
head -5 logs/backtest_log/trades_*.csv
# ヘッダー行 + データ行が正しく出力されていること

# サマリーの確認
cat logs/backtest_log/summary_*.log
# 年別統計 + Exit理由別/モード別/レジーム別統計が出力されていること
```

3. CSVの分析テスト:
```python
import pandas as pd
df = pd.read_csv("logs/backtest_log/trades_XXXXXXXX_XXXXXX.csv")
# 負けトレードのスコア内訳確認
losers = df[df["profit_loss"] < 0]
print(losers[["score_macd_slope", "score_ema_cross", "regime"]].describe())
```

4. 既存テスト:
```bash
python -m pytest tests/ -x
```
