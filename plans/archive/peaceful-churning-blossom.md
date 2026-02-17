# バックテストログ品質強化計画

## Context

現在のCSVログは45列あるが、以下の問題がある:
1. **欠落フィールド**: entry_threshold, penalty_total/breakdown, htf_alignment, parent_trade_id, session, mfe_R/mae_R, time_to_mfe 等が未出力
2. **exit_reason粒度不足**: TAKE_PROFITが1R/2R/TP区別なし
3. **部分決済のsig_dataロスト**: `_pos_mode_regime.pop()`で最初の部分決済時にsig_dataが消え、後続の決済のregime/mode/scoreが空になるバグ
4. **品質チェックなし**: regime/mode/scoreが欠落していてもサイレントに通過

**目的**: 全トレードの完全なメタデータ出力 + イベントログ + 品質assertion

---

## 現状のデータフロー

```
trade_bot._generate_signal_new()
  → ConsolidatedSignal(regime, mode, consensus_score, tf_score_breakdowns, confidence, sl_pips, tp_pips)
  ※ 未伝搬: htf_alignment, consensus.threshold, sg_result(penalty), trend_strength

runner._run_unified_year()
  → _pos_mode_regime[key] = {mode, regime, score_breakdowns, confidence, ...}
  → simulator.process_candle(candle, signal)
  → 決済検出: _sig_data = _pos_mode_regime.pop(key) ← ★popなので部分決済で消える
  → emitter.emit_trade_closed(..., signal_data=_sig_data)

file_listener._handle_position_closed()
  → CSV行構築 → _trade_rows.append(row)
```

---

## Phase 1: ConsolidatedSignalに不足メタデータ追加

**ファイル**: `src/autotrader/decision/unified/signal_consolidator.py`

ConsolidatedSignalに以下のフィールド追加:
```python
entry_threshold: float = 0.0
htf_alignment: float = 0.0
penalty_total: float = 0.0
penalty_breakdown: dict[str, float] = field(default_factory=dict)
trend_strength: float = 0.0
```

**ファイル**: `src/autotrader/decision/unified/trade_bot.py`

`_generate_signal_new()`のreturn文で設定:
```python
return ConsolidatedSignal(
    ...,
    entry_threshold=consensus.threshold,
    htf_alignment=htf_alignment,
    penalty_total=sg_result.total_penalty,
    penalty_breakdown={r.value: v for r, v in sg_result.penalties.items()},
    trend_strength=regime_result.trend_strength,
)
```

---

## Phase 2: ExitReason細分化

**ファイル**: `src/autotrader/core/enums.py`

ExitReasonに追加:
```python
TAKE_PROFIT_1R = "TAKE_PROFIT_1R"
TAKE_PROFIT_2R = "TAKE_PROFIT_2R"
BREAKEVEN = "BREAKEVEN"
```

**ファイル**: `src/autotrader/decision/unified/position_manager.py`

- `_check_partial_close()` 1R → `exit_reason=ExitReason.TAKE_PROFIT_1R`
- `_check_partial_close()` 2R → `exit_reason=ExitReason.TAKE_PROFIT_2R`
- `ManagementAction.partial_close()`: exit_reason引数追加
- `_check_sl()`: `current_sl == entry_price`時 → `ExitReason.BREAKEVEN`

---

## Phase 3: MFE/MAEのR換算 + time_to_mfe + session

**ファイル**: `src/autotrader/backtest/simulator.py`

### 3a. MFE/MAE R換算
`_open_position()`時にSL距離(pips)を`_mfe_mae[pid]["sl_pips"]`に保存。
`_update_mfe_mae()`でR換算追加:
```python
tracker["mfe_r"] = tracker["mfe"] / sl_pips if sl_pips > 0 else 0
tracker["mae_r"] = tracker["mae"] / sl_pips if sl_pips > 0 else 0
```

### 3b. time_to_mfe
`_update_mfe_mae()`でMFE更新時にタイムスタンプ記録:
```python
if fav > tracker["mfe"]:
    tracker["mfe"] = fav
    tracker["mfe_time"] = candle.time
```

### 3c. session判定
file_listener内でCSV行構築時にentry_time UTC時間から判定:
- 0-7: TOKYO, 7-12: LONDON, 12-17: NEWYORK, 17-24: TOKYO

---

## Phase 4: parent_trade_id + sig_dataバグ修正

**ファイル**: `src/autotrader/core/entities.py` — Trade.parent_trade_id: str | None = None 追加

**ファイル**: `src/autotrader/backtest/simulator.py` — `_partial_close_position()`で parent_trade_id=position.position_id 設定

**ファイル**: `src/autotrader/backtest/runner.py` — **pop→get修正**:
```python
# 修正前
_sig_data = _pos_mode_regime.pop(_ckey, {})
# 修正後
_sig_data = _pos_mode_regime.get(_ckey, {})
# 完全クローズ時のみpop
_still_open = any(
    p.position_id == _sig_data.get("position_id", "")
    for p in simulator.state.open_positions
)
if not _still_open:
    _pos_mode_regime.pop(_ckey, None)
```

---

## Phase 5: runner.pyからの新フィールド伝搬

**ファイル**: `src/autotrader/backtest/runner.py`

`_pos_mode_regime[_key]`に追加:
```python
"entry_threshold": consolidated.entry_threshold,
"htf_alignment": consolidated.htf_alignment,
"penalty_total": consolidated.penalty_total,
"penalty_breakdown": json.dumps(consolidated.penalty_breakdown),
"trend_strength": consolidated.trend_strength,
```

`emit_trade_closed()`に新フィールド追加。

---

## Phase 6: TradeEvent + CSV拡張

**ファイル**: `src/autotrader/backtest/events.py` — TradeEventに追加:
```python
parent_trade_id: str = ""
entry_threshold: float = 0.0
htf_alignment: float = 0.0
penalty_total: float = 0.0
penalty_breakdown: str = ""
trend_strength: float = 0.0
mfe_r: float = 0.0
mae_r: float = 0.0
time_to_mfe_minutes: float = 0.0
session: str = ""
```

**ファイル**: `src/autotrader/backtest/file_listener.py` — CSV_COLUMNS追加:
```
parent_trade_id, entry_threshold, penalty_total, penalty_breakdown,
htf_alignment, trend_strength, mfe_r, mae_r, time_to_mfe_minutes,
session, score_components
```

---

## Phase 7: ポジションイベントログ

**新ファイル**: `src/autotrader/backtest/position_event_logger.py`

イベントタイプ: OPEN, PARTIAL_CLOSE_1R, PARTIAL_CLOSE_2R, SL_MOVE_BE, SL_MOVE_1R, TRAILING_UPDATE, FULL_CLOSE

カラム: timestamp, position_id, event_type, price, volume_before, volume_after, sl_before, sl_after, current_r, reason

**実装**: simulator.pyの各アクション箇所からイベント記録。FileEventListenerで`position_events_*.csv`出力。

---

## Phase 8: ログ品質assertion

**ファイル**: `src/autotrader/backtest/runner.py`

バックテスト完了後に品質チェック:
```python
def _validate_trade_log(self, trades):
    errors = []
    for t in trades:
        if not t.regime or t.regime == "UNKNOWN":
            errors.append(f"regime欠落: {t.trade_id}")
        if not t.mode or t.mode == "UNKNOWN":
            errors.append(f"mode欠落: {t.trade_id}")
        if (t.consensus_score or 0) == 0 and not t.parent_trade_id:
            errors.append(f"score=0: {t.trade_id}")
    if errors:
        raise AssertionError(f"ログ品質エラー({len(errors)}件):\n" + "\n".join(errors[:10]))
```

---

## 変更ファイル一覧

| Phase | ファイル | 変更内容 |
|-------|----------|----------|
| 1 | signal_consolidator.py | ConsolidatedSignalに5フィールド追加 |
| 1 | trade_bot.py | 新フィールドを設定してreturn |
| 2 | enums.py | ExitReasonに3値追加 |
| 2 | position_manager.py | 1R/2R/BEのExitReason細分化 |
| 3 | simulator.py | MFE R換算, time_to_mfe, SL距離保存 |
| 4 | entities.py | Trade.parent_trade_id追加 |
| 4 | simulator.py | partial_closeでparent_trade_id設定 |
| 4 | runner.py | pop→get修正, 新フィールド伝搬 |
| 5 | runner.py | 新メタデータをemitに追加 |
| 6 | events.py | TradeEventに10フィールド追加 |
| 6 | file_listener.py | CSV_COLUMNS拡張 + 行構築 |
| 7 | position_event_logger.py | 新規: イベントログ |
| 7 | simulator.py | イベント発行追加 |
| 8 | runner.py | _validate_trade_log() |

---

## 検証手順

1. `uv run python scripts/run_backtest.py --years 2023` 実行
2. CSVの新カラムが全て非空か確認
3. `position_events_*.csv`のイベント時系列確認
4. 品質assertionが通過（欠落なし）
5. PF/勝率がログ拡張前と同一（ロジック変更なし）
