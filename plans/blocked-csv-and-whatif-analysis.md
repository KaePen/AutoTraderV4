# Implementation Plan: blocked_signals CSV出力修正 + What-If Trade Analysis

## Overview

2つの関連機能を設計する。(1) キューランナーの月並列スケジューラが`_worker_blocked_rows`をCSV出力していないバグの修正、(2) ブロックされたシグナルの仮想トレード追跡（タラレバ分析）機能の新規追加。両機能は出力パイプラインを共有するため、Phase 1でCSV出力基盤を修正し、Phase 2でWhat-If追跡ロジックを追加する。

## Requirements

### 機能1: blocked_signals.csv出力修正
- `backtest_queue_runner.py:1450`で`_worker_blocked_rows`がpopされていない（月結果JSONに混入）
- キューランナーにはブロック行をCSVにまとめる処理自体が存在しない
- CLI直接実行(`run_backtest.py`)では`FileEventListener._write_blocked_csv()`が処理するので出力される
- キューランナー経由でも同等のCSVが出力されるようにする

### 機能2: What-If Trade Analysis
- ブロックされたシグナルに対し仮想エントリー -> SL/TP追跡 -> 仮想決済を実施
- 実トレードに影響しない（エクイティ・ポジション管理に干渉しない）
- バックテスト専用（`backtest/`に配置）
- 月並列処理互換（月境界をまたぐ仮想ポジションの扱い）
- CSV出力は実トレードCSVと比較可能なフォーマット

---

## Architecture Changes

### 新規ファイル
- `autotrader/backtest/whatif_tracker.py` - 仮想ポジション追跡エンジン

### 変更ファイル
- `scripts/backtest_queue_runner.py` - blocked_rows pop漏れ修正 + CSV出力追加
- `autotrader/backtest/year_runner.py` - What-Ifトラッカー統合
- `autotrader/backtest/file_listener.py` - What-If CSV出力カラム定義 + TradeRowCollector拡張
- `autotrader/backtest/events.py` - SignalBlockedEventにSL/TP追加
- `autotrader/backtest/month_runner.py` - _worker_whatif_rows伝搬
- `autotrader/backtest/parallel_worker.py` - _worker_whatif_rows伝搬

---

## Implementation Steps

### Phase 1: blocked_signals.csv出力修正（バグフィックス）

#### Step 1.1: `_worker_blocked_rows` pop漏れ修正
**File**: `scripts/backtest_queue_runner.py` L1449-1451

`_worker_blocked_rows`もpopして月結果JSONへの混入を防ぐ。

```python
# 現状（L1449-1451）
result.pop("_worker_trade_rows", None)
result.pop("_worker_stats", None)
# _worker_blocked_rows が漏れている

# 修正後
result.pop("_worker_trade_rows", None)
result.pop("_worker_stats", None)
result.pop("_worker_blocked_rows", None)
```

- Dependencies: None
- Risk: Low

#### Step 1.2: キューランナーに月別CSV出力を追加
**File**: `scripts/backtest_queue_runner.py`

**設計**: 月完了時にCSV行をファイルに即座に書き出す（逐次追記方式）。

理由:
- メモリ効率: 全月分のCSV行をメモリに保持しない
- 再開互換: チェックポイント再開時は既存CSV月別ファイルをスキャン
- シンプル: `_execute_month`内でCSV書き出しを完結

**月別CSVの保存先**:
- `month_results/{result_id}/trades_{year}_{month:02d}.csv`
- `month_results/{result_id}/blocked_{year}_{month:02d}.csv`

**新関数 `_write_month_csv`**:

```python
def _write_month_csv(
    result_id: str,
    year: int,
    month: int,
    trade_rows: list[dict],
    blocked_rows: list[dict],
    whatif_rows: list[dict] | None = None,
) -> None:
    """月別CSV出力（trade + blocked + whatif）"""
    from autotrader.backtest.file_listener import (
        BLOCKED_CSV_COLUMNS,
        CSV_COLUMNS,
        WHATIF_CSV_COLUMNS,
    )
    base = MONTH_RESULTS_DIR / result_id
    base.mkdir(parents=True, exist_ok=True)

    _items = [
        (trade_rows, f"trades_{year}_{month:02d}.csv", CSV_COLUMNS),
        (blocked_rows, f"blocked_{year}_{month:02d}.csv", BLOCKED_CSV_COLUMNS),
    ]
    if whatif_rows is not None:
        _items.append(
            (whatif_rows, f"whatif_{year}_{month:02d}.csv", WHATIF_CSV_COLUMNS),
        )
    for rows, filename, columns in _items:
        if not rows:
            continue
        path = base / filename
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=columns, extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
```

- Dependencies: Step 1.1
- Risk: Medium（サブプロセス内のファイルI/O追加。パフォーマンスへの影響は軽微）

#### Step 1.3: `_execute_month`内のCSV書き出し
**File**: `scripts/backtest_queue_runner.py` L1430-1460

pop前にCSV行を退避して月別CSVに書き出す。`result_id`は`Path(result_path).parent.name`で取得。

```python
# _collector._trade_rows / _collector._blocked_rows は L1435時点でアクセス可能

# CSV出力（pop前）
_result_id = Path(result_path).parent.name
_write_month_csv(
    result_id=_result_id,
    year=year,
    month=month,
    trade_rows=_collector._trade_rows,
    blocked_rows=_collector._blocked_rows,
)

# 既存のpop + blocked_rows追加
result.pop("_worker_trade_rows", None)
result.pop("_worker_stats", None)
result.pop("_worker_blocked_rows", None)
```

- Dependencies: Step 1.2
- Risk: Low

#### Step 1.4: ジョブ集約時のCSV結合
**File**: `scripts/backtest_queue_runner.py`

`_aggregate_job_single`（L820付近）にCSV結合ロジックを追加。

**新関数 `_merge_month_csvs`**:

```python
def _merge_month_csvs(
    result_id: str,
    start_year: int,
    end_year: int,
) -> dict[str, Path | None]:
    """月別CSVを結合して最終CSVを出力

    Returns:
        {"trades": Path, "blocked": Path, "whatif": Path} or None
    """
    base = MONTH_RESULTS_DIR / result_id
    out_dir = BACKTEST_RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for prefix in ("trades", "blocked", "whatif"):
        files = sorted(base.glob(f"{prefix}_*.csv"))
        if not files:
            results[prefix] = None
            continue
        out_path = out_dir / f"{result_id}_{prefix}.csv"
        header_written = False
        with open(out_path, "w", newline="", encoding="utf-8") as out:
            for csv_file in files:
                with open(csv_file, "r", encoding="utf-8") as inp:
                    reader = csv.DictReader(inp)
                    if not header_written:
                        writer = csv.DictWriter(
                            out, fieldnames=reader.fieldnames,
                            extrasaction="ignore",
                        )
                        writer.writeheader()
                        header_written = True
                    for row in reader:
                        writer.writerow(row)
        results[prefix] = out_path
    return results
```

`_aggregate_job_single`の末尾（L959付近、return前）に呼び出しを追加:

```python
_merge_month_csvs(result_id, start_year, end_year)
```

`_aggregate_job_multi_pair`にも同様。

- Dependencies: Step 1.3
- Risk: Low

#### Step 1.5: マルチペア版にも同様の修正
**File**: `scripts/backtest_queue_runner.py` `_execute_month_multi_pair`（L1462-）

マルチペア版でも`TradeRowCollector`相当のデータ収集を確認し、同様にCSV出力を追加。

- Dependencies: Step 1.3
- Risk: Medium（マルチペア版のデータ構造要確認）

---

### Phase 2: What-If Trade Analysis（新機能）

#### Step 2.1: WhatIfTrackerクラス
**File**: `autotrader/backtest/whatif_tracker.py`（新規）

**設計方針**:
- SL/TP単純判定で近似（トレーリングストップ等のPM exit logicはフル再現しない）
- 理由: What-Ifの目的は「勝てたか負けたか」の大まかな傾向把握。精密な再現はPM依存を生み`backtest/`に閉じられない
- `simulator._check_exit_conditions`と同じSL/TPヒット判定ロジック（ギャップ約定対応）

**主要クラス**:

```python
@dataclass
class WhatIfPosition:
    """仮想ポジション"""
    id: str                    # "WI-{timestamp}-{direction}"
    entry_time: datetime
    symbol: str
    direction: str             # "BUY" / "SELL"
    entry_price: float
    sl_price: float
    tp_price: float
    sl_pips: float
    tp_pips: float
    consensus_score: float
    threshold: float
    block_reason: str
    regime: str = ""
    mode: str = ""
    mfe_pips: float = 0.0
    mae_pips: float = 0.0


@dataclass
class WhatIfResult:
    """仮想トレード結果"""
    position: WhatIfPosition
    exit_time: datetime
    exit_price: float
    exit_reason: str           # "SL" / "TP" / "TIMEOUT" / "MONTH_END"
    pips: float
    holding_minutes: float


class WhatIfTracker:
    """仮想ポジション追跡エンジン

    Args:
        pip_unit: 1pipの価格単位
        max_holding_candles: 最大保有足数（M1で480=8時間）
        enabled: 有効フラグ
    """

    def __init__(
        self,
        pip_unit: float = 0.01,
        max_holding_candles: int = 480,
        enabled: bool = False,
    ) -> None: ...

    def add_blocked_signal(
        self,
        entry_time, symbol, direction, entry_price,
        sl_pips, tp_pips, consensus_score, threshold,
        block_reason, regime="", mode="",
    ) -> None:
        """ブロックシグナルを仮想ポジションとして登録"""
        # SL/TP価格計算
        # BUY: sl = entry - sl_pips * pip_unit, tp = entry + tp_pips * pip_unit
        # SELL: sl = entry + sl_pips * pip_unit, tp = entry - tp_pips * pip_unit

    def update(self, candle: Candle) -> list[WhatIfResult]:
        """毎足の更新。SL/TPヒットを判定"""
        # 各仮想ポジションに対し:
        #   1. MFE/MAE更新
        #   2. SL/TPヒット判定（simulator._check_exit_conditionsと同等）
        #   3. タイムアウト判定

    def force_close_all(self, time, price) -> list[WhatIfResult]:
        """全仮想ポジション強制決済（月末・年末用）"""

    def _check_exit(self, pos, candle) -> WhatIfResult | None:
        """SL/TPヒット判定"""
        # BUY: low <= sl → SL, high >= tp → TP
        # SELL: high >= sl → SL, low <= tp → TP
        # ギャップ: open価格がSL/TPを超えている場合はopenで約定
```

- Dependencies: None
- Risk: Low（新規ファイル）

#### Step 2.2: SignalBlockedEventにSL/TP情報を追加
**File**: `autotrader/backtest/events.py`

```python
@dataclass
class SignalBlockedEvent(BacktestEvent):
    # 既存フィールド
    event_type: EventType = field(default=EventType.SIGNAL_BLOCKED)
    symbol: str = ""
    would_be_direction: str = ""
    consensus_score: float = 0.0
    threshold: float = 9.0
    block_reason: str = ""
    regime: str = ""
    mode: str = ""
    # 追加フィールド（What-If用、デフォルト値で後方互換）
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    entry_price: float = 0.0
```

`emit_signal_blocked`にも対応パラメータを追加（デフォルト値付き）。

year_runner.py L312の呼び出しに`sl_pips`, `tp_pips`, `entry_price`を追加:
```python
_emitter.emit_signal_blocked(
    candle_time=candle_time,
    symbol=runner.config.symbol,
    direction=_dir,
    score=_cs,
    threshold=consolidated.entry_threshold or 9.0,
    block_reason=consolidated.rationale or "",
    regime=consolidated.regime or "",
    mode=consolidated.mode or "",
    sl_pips=consolidated.sl_pips,
    tp_pips=consolidated.tp_pips,
    entry_price=candle.close,
)
```

- Dependencies: None
- Risk: Low（デフォルト値付き追加フィールドのみ）

#### Step 2.3: year_runner.pyへのWhatIfTracker統合
**File**: `autotrader/backtest/year_runner.py`

**統合ポイント**:

1. `run_unified_year`引数に`whatif_enabled: bool = False`を追加
2. ループ開始前にWhatIfTrackerインスタンスを生成

```python
from autotrader.backtest.whatif_tracker import WhatIfTracker
whatif_tracker = WhatIfTracker(
    pip_unit=sim_config.pip_unit,
    max_holding_candles=480,
    enabled=whatif_enabled,
)
```

3. ループ内の**毎足先頭**でupdate（SL/TPチェックはエントリー直後の足から開始）

```python
# L154のforループ内、MFE/MAE更新等の後に
if whatif_tracker.enabled:
    whatif_tracker.update(candle)
```

4. L302-330: HOLD判定かつスコア>0の箇所でWhatIfTrackerに登録

```python
if whatif_tracker.enabled and _cs is not None and _cs > 0:
    whatif_tracker.add_blocked_signal(
        entry_time=candle_time,
        symbol=runner.config.symbol,
        direction=_dir,
        entry_price=candle.close,
        sl_pips=consolidated.sl_pips,
        tp_pips=consolidated.tp_pips,
        consensus_score=_cs,
        threshold=consolidated.entry_threshold or 9.0,
        block_reason=consolidated.rationale or "",
        regime=consolidated.regime or "",
        mode=consolidated.mode or "",
    )
```

5. ループ終了後にforce_close_all（未決済の仮想ポジション処理）

```python
if whatif_tracker.enabled and last_candle:
    whatif_tracker.force_close_all(
        time=last_candle.time, price=last_candle.close
    )
```

6. 結果を返却値に含める

```python
if whatif_tracker.enabled:
    result["_whatif_results"] = [
        _whatif_result_to_row(wr) for wr in whatif_tracker.results
    ]
```

**`_whatif_result_to_row`ヘルパー関数**:

```python
def _whatif_result_to_row(wr: WhatIfResult) -> dict:
    """WhatIfResult -> CSV行辞書"""
    p = wr.position
    return {
        "whatif_id": p.id,
        "symbol": p.symbol,
        "direction": p.direction,
        "entry_time": p.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
        "exit_time": wr.exit_time.strftime("%Y-%m-%d %H:%M:%S"),
        "holding_minutes": f"{wr.holding_minutes:.0f}",
        "entry_price": f"{p.entry_price:.5f}",
        "exit_price": f"{wr.exit_price:.5f}",
        "pips": f"{wr.pips:.1f}",
        "exit_reason": wr.exit_reason,
        "sl_pips": f"{p.sl_pips:.1f}",
        "tp_pips": f"{p.tp_pips:.1f}",
        "mfe_pips": f"{p.mfe_pips:.1f}",
        "mae_pips": f"{p.mae_pips:.1f}",
        "consensus_score": f"{p.consensus_score:.2f}",
        "threshold": f"{p.threshold:.2f}",
        "block_reason": p.block_reason,
        "regime": p.regime,
        "mode": p.mode,
    }
```

- Dependencies: Step 2.1
- Risk: Medium（year_runner.pyは中核モジュール。ただしwhatif_enabled=Falseがデフォルト）

#### Step 2.4: What-If CSV出力カラム定義
**File**: `autotrader/backtest/file_listener.py`

```python
WHATIF_CSV_COLUMNS = [
    "whatif_id",
    "symbol",
    "direction",
    "entry_time",
    "exit_time",
    "holding_minutes",
    "entry_price",
    "exit_price",
    "pips",
    "exit_reason",
    "sl_pips",
    "tp_pips",
    "mfe_pips",
    "mae_pips",
    "consensus_score",
    "threshold",
    "block_reason",
    "regime",
    "mode",
]
```

**FileEventListenerへの追加**:
- `__init__`に`self.whatif_file`と`self._whatif_rows`追加
- `_write_whatif_csv()`メソッド追加
- `_handle_backtest_end`内で`_write_whatif_csv()`呼び出し
- `merge_worker_data`にwhatif_rows引数追加

**TradeRowCollectorへの追加**:
- `self._whatif_rows: list[dict] = []`追加

- Dependencies: Step 2.1
- Risk: Low

#### Step 2.5: month_runner.py / parallel_worker.py のwhatif_rows伝搬
**File**: `autotrader/backtest/month_runner.py` L362-365
**File**: `autotrader/backtest/parallel_worker.py` L140-148

```python
# month_runner.py
result["_worker_whatif_rows"] = _collector._whatif_rows

# parallel_worker.py
result["_worker_whatif_rows"] = _collector._whatif_rows
```

- Dependencies: Step 2.4
- Risk: Low

#### Step 2.6: キューランナーのWhat-If CSV出力
**File**: `scripts/backtest_queue_runner.py`

`_execute_month`内で`_write_month_csv`にwhatif_rowsも渡す:

```python
_write_month_csv(
    result_id=_result_id,
    year=year,
    month=month,
    trade_rows=_collector._trade_rows,
    blocked_rows=_collector._blocked_rows,
    whatif_rows=_collector._whatif_rows,
)

result.pop("_worker_whatif_rows", None)
```

- Dependencies: Step 1.2, Step 2.5
- Risk: Low

#### Step 2.7: 有効化フラグの配線

**CLI** (`scripts/run_backtest.py`): `--whatif`フラグ追加

**キューランナー**: ジョブoverridesで制御
```json
{
  "overrides": {
    "backtest": { "whatif_enabled": true }
  }
}
```

`_execute_month`内でジョブoverridesから読み取り、`run_unified_year`に渡す:
```python
_whatif = bt_ovr.get("whatif_enabled", False)
result = run_unified_year(
    ...,
    whatif_enabled=_whatif,
)
```

- Dependencies: Step 2.3
- Risk: Low

---

## Data Flow

### CLI直接実行パス（既存、blocked_signalsは動作中）
```
run_backtest.py
  -> BacktestService.run()
    -> run_unified_year() + FileEventListener
      -> SignalBlockedEvent -> FileEventListener._handle_signal_blocked()
      -> _write_blocked_csv() at BACKTEST_END
  -> blocked_signals_{timestamp}.csv
  -> (Phase2) whatif_trades_{timestamp}.csv
```

### キューランナーパス（Phase 1修正後）
```
backtest_queue_runner.py
  -> _execute_month() (subprocess)
    -> run_unified_year() + TradeRowCollector
      -> _collector._trade_rows / _collector._blocked_rows / _collector._whatif_rows
    -> _write_month_csv()
      -> month_results/{id}/trades_YYYY_MM.csv
      -> month_results/{id}/blocked_YYYY_MM.csv
      -> month_results/{id}/whatif_YYYY_MM.csv   (Phase 2)
    -> result.pop() -> JSON保存（CSV行なし）
  -> _aggregate_job_single()
    -> _merge_month_csvs()
      -> backtest_results/{id}_trades.csv
      -> backtest_results/{id}_blocked.csv
      -> backtest_results/{id}_whatif.csv        (Phase 2)
```

---

## CSV出力フォーマット

### blocked_signals.csv（既存カラム、変更なし）
| カラム | 型 | 説明 |
|--------|-----|------|
| timestamp | datetime | ブロック時刻 |
| symbol | str | 通貨ペア |
| would_be_direction | str | BUY / SELL |
| consensus_score | float | コンセンサススコア |
| threshold | float | エントリー閾値 |
| block_reason | str | ブロック理由 |
| regime | str | レジーム |
| mode | str | モード |

### whatif_trades.csv（新規）
| カラム | 型 | 説明 |
|--------|-----|------|
| whatif_id | str | 仮想ポジションID |
| symbol | str | 通貨ペア |
| direction | str | BUY / SELL |
| entry_time | datetime | 仮想エントリー時刻 |
| exit_time | datetime | 仮想決済時刻 |
| holding_minutes | int | 保有時間（分） |
| entry_price | float | エントリー価格（close） |
| exit_price | float | 決済価格 |
| pips | float | 獲得pips |
| exit_reason | str | SL / TP / TIMEOUT / MONTH_END |
| sl_pips | float | SL幅 |
| tp_pips | float | TP幅 |
| mfe_pips | float | 最大含み益 |
| mae_pips | float | 最大含み損 |
| consensus_score | float | コンセンサススコア |
| threshold | float | エントリー閾値 |
| block_reason | str | 元のブロック理由 |
| regime | str | レジーム |
| mode | str | モード |

---

## 月境界問題への対処

**問題**: 月並列処理では月ごとに独立してバックテストを実行するため、仮想ポジションが月末で切れる。

**解決策**: `force_close_all`でexit_reason="MONTH_END"として強制決済する。これは実トレードのFORCE_CLOSEと同等の扱い。

**影響**: 月末のWhatIf結果は不正確になるが、全体の統計的傾向把握には影響しない。exit_reason="MONTH_END"でフィルタリング可能。

**CLI直接実行時**: 年単位でループするため月境界問題は発生しない。期間最後の足でforce_close_all（exit_reason="PERIOD_END"）。

---

## パフォーマンス影響

- **メモリ**: WhatIfPositionは軽量（約200バイト/件）。ブロックシグナルは典型的に年間200-500件。同時オープン仮想ポジション数十件以下。
- **CPU**: 毎足のupdate()はO(n)、n < 50。M1の年間26万足に対して無視できるコスト。
- **I/O**: 月別CSV出力は月1回。ジョブ集約時のCSV結合は1回。
- **whatif_enabled=False時**: WhatIfTracker.update()はearly returnするため追加コスト0。

---

## Testing Strategy

### Unit Tests
- `tests/backtest/test_whatif_tracker.py`
  - BUY SLヒット / BUY TPヒット / SELL SLヒット / SELL TPヒット
  - ギャップ約定（open価格がSL/TP超え）
  - タイムアウト決済
  - MFE/MAE追跡の正確性
  - force_close_all
  - enabled=False時の無操作確認
  - 複数仮想ポジション同時追跡

### Integration Tests
- キューランナーの月別CSV出力 -> ジョブ集約CSV結合
- year_runner + WhatIfTracker統合（ブロックシグナルが正しく登録・追跡される）

---

## Risks & Mitigations

- **Risk**: year_runner.pyの変更でパフォーマンスが劣化する
  - Mitigation: whatif_enabled=Falseがデフォルト。無効時はearly returnでコスト0。

- **Risk**: SL/TP単純判定が実際のPM決済と乖離する
  - Mitigation: What-Ifの目的は統計的傾向把握。精密な利益額は不要。exit_reasonで分析対象を制御可能。

- **Risk**: 月境界でのMONTH_END強制決済がノイズになる
  - Mitigation: exit_reason="MONTH_END"でフィルタリング可能。分析時に除外推奨。

- **Risk**: キューランナーの月別CSV出力でI/Oパフォーマンスが劣化
  - Mitigation: CSVは月に1回の書き込み。数百行程度で軽微。

- **Risk**: `consolidated.sl_pips`がHOLD時に0の場合がある
  - Mitigation: sl_pips=0またはtp_pips=0の場合はWhat-If登録をスキップ。

---

## Success Criteria

- [ ] キューランナー経由のバックテストで`{result_id}_blocked.csv`が出力される
- [ ] CLI直接実行と同じカラム・内容のblocked_signals CSVが得られる
- [ ] `_worker_blocked_rows`が月結果JSONに混入しない
- [ ] whatif_enabled=True時に`{result_id}_whatif.csv`が出力される
- [ ] WhatIf結果のSL/TPヒット判定がsimulatorの`_check_exit_conditions`と一致する
- [ ] whatif_enabled=False（デフォルト）時にパフォーマンス影響がない
- [ ] 月並列処理でMONTH_END強制決済が正しく動作する
- [ ] `--whatif`フラグ / ジョブoverridesで有効化できる
