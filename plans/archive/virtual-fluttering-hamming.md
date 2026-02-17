# ログ品質改善 + オフ時間フィルター拡大（6項目）

## Context

現在のバックテスト成績（PF 1.90, 勝率66.8%）をさらに改善するための基盤整備。
ログの品質と情報量を向上させ、分析精度を上げた上でフィルター強化に進む。
「さらに攻める（サイズ上振れ）」を安全に行うための計測基盤。

## 実装順序

**Phase A** (ログ基盤): 項目4→2→3 — トレードロジック変更なし
**Phase B** (Exit改善): 項目5→6 — ExitReason精密化 + trigger/fill分離
**Phase C** (フィルター): 項目1 — トレード結果に影響する唯一の変更

---

## 項目4: スコア成分のprimary_tf優先取得

**変更ファイル**: `file_listener.py`, `runner.py`

### runner.py
`_pos_mode_regime[_key]` dictに `"primary_tf": consolidated.primary_tf` を追加

### file_listener.py (行476-497)
スコア内訳抽出をprimary_tf優先に変更:
```python
primary_tf = sig_data.get("primary_tf", "")
bd = breakdowns.get(primary_tf, {})
if not bd and breakdowns:
    bd = next(iter(breakdowns.values()), {})
```

---

## 項目2: 未スコア取引のログ欠落をゼロ

**変更ファイル**: `runner.py`, `file_listener.py`

### runner.py
- `_pos_mode_regime` のキーを `position_id` に変更（現在の `str(opened_at)` は精度問題あり）
  - 行1125: `_key = pos.position_id`
  - 行1244: `_ckey = new_trade.position_id or str(new_trade.opened_at)`
- 部分決済時: `pop` ではなく `get` を使い、親メタデータを子にも継承
- `_sig_data` が空の場合に警告ログ出力

### file_listener.py
- `sig_data` が空の場合にフォールバックとしてTradeEventの直接フィールドを参照

---

## 項目3: strategy_id（エントリールート識別）

**変更ファイル**: `mode_selector.py`, `signal_consolidator.py`, `trade_bot.py`, `runner.py`, `events.py`, `file_listener.py`

### mode_selector.py `_select_mode` (行154-205)
各returnパスで選択理由コードを返すように変更:
```python
def _select_mode(self, ...) -> tuple[TradingStrategyMode, str]:
    ...
    if regime == MarketRegime.HIGH_VOL:
        if is_active_session:
            return TradingStrategyMode.SCALPING, "HIGHVOL_ACTIVE"
        return TradingStrategyMode.DAY_TRADE, "HIGHVOL_INACTIVE"
    if volatility_level > cfg.high_vol_threshold:
        return TradingStrategyMode.SCALPING, "VOL_THRESHOLD"
    if regime == MarketRegime.TREND:
        return TradingStrategyMode.SWING, "TREND"
    return TradingStrategyMode.DAY_TRADE, "RANGE"
```

### TradingPlan dataclass
`selection_reason: str = ""` フィールド追加

### signal_consolidator.py ConsolidatedSignal
`strategy_id: str = ""` フィールド追加

### trade_bot.py
strategy_id構築: `f"{plan.mode.value}_{plan.selection_reason}"`

### CSV出力
`strategy_id` カラム追加（`mode` の直後）

---

## 項目5: exit_reason の精密分類

**変更ファイル**: `enums.py`, `position_manager.py`

### enums.py ExitReason (行96-108)
enum値のみ変更（Python名は維持、参照箇所の修正不要）:
```python
class ExitReason(str, Enum):
    STOP_LOSS = "SL_HIT"
    TAKE_PROFIT = "TP_HIT"
    TAKE_PROFIT_1R = "TP_1R"
    TAKE_PROFIT_2R = "TP_2R"
    BREAKEVEN = "BE_HIT"
    TRAILING_STOP = "TRAIL_HIT"
    TIME_EXIT = "TIME"
    MANUAL = "MANUAL"
    SIGNAL_REVERSAL = "SIGNAL_REV"
    FORCE_CLOSE = "FORCE_CLOSE"
```

### position_manager.py — TRAILING_STOP識別

**ManagedPosition** (行96-132)に追加:
```python
trailing_activated: bool = False
```

**_check_trailing** (行480-510): トレーリングSL更新時に `position.trailing_activated = True` を設定

**_check_sl** (行328-355): SLヒット時の判定を3段階に:
```python
if is_breakeven:
    exit_reason = ExitReason.BREAKEVEN
elif position.trailing_activated:
    exit_reason = ExitReason.TRAILING_STOP
else:
    exit_reason = ExitReason.STOP_LOSS
```

---

## 項目6: trigger_price と fill_price を記録

**変更ファイル**: `position_manager.py`, `simulator.py`, `events.py`, `runner.py`, `file_listener.py`

### ManagementAction (行25-41)
`trigger_price: float = 0.0` フィールド追加（frozen=True対応済み）

### position_manager.py 各メソッド
trigger_price設定:
- `_check_sl`: `trigger_price = position.current_sl`
- `_check_tp`: `trigger_price = position.original_tp`
- `_check_time_exit`: `trigger_price = 0.0`（時間決済、価格トリガーなし）
- `_check_signal_reversal`: `trigger_price = 0.0`
- `_check_partial_close`: 1R/2Rレベルの理論価格

ManagementAction.full_close / partial_close のシグネチャに `trigger_price` を追加

### simulator.py

**`_check_exit_conditions`** (行339-379) — 戻り値を3値タプルに:
```python
-> tuple[float, float, ExitReason] | None  # (fill_price, trigger_price, reason)
```
- SLヒット: `trigger = sl`, `fill = sl - slip` or `candle.open - slip`
- TPヒット: `trigger = tp`, `fill = tp - slip` or `candle.open - slip`

**`_check_exit_conditions_pm`** (行381-453) — 同様に3値タプル:
```python
if action.action_type == ManagementActionType.FULL_CLOSE:
    exit_price = self._get_exit_price(...)
    return exit_price, action.trigger_price, action.exit_reason
```

**呼び出し元** (process_candle等): 3値タプルを展開

### `_exit_metrics` に trigger_price を追加
runner.py で TradeEvent emit時に取得

### events.py TradeEvent
`trigger_price: float = 0.0`, `fill_price: float = 0.0` 追加

### file_listener.py
CSV_COLUMNS: `"trigger_price"`, `"fill_price"` を `"exit_price"` の直後に追加

---

## 項目1: オフ時間フィルター拡大（penalty>=0.15 → score>=6.4必須）

**変更ファイル**: `trade_bot.py`

### trade_bot.py (行520-532)
現在のTOKYO限定フィルターを汎用化:
```python
# Before (TOKYO 4-7 UTC限定)
if (4 <= hour_utc <= 7
    and sg_result.total_penalty > 0
    and consensus.score < 6.4):

# After (全オフ時間帯)
if (sg_result.total_penalty >= 0.15
    and consensus.score < 6.4):
```
対象拡大: 4-7 UTC → 4-7 + 18-21 UTC（reject_hours 22-3は既にpenalty>=0.8でブロック済み）

---

## 検証方法

各Phase完了後にバックテスト実行:
```bash
python scripts/run_backtest.py --year 2024
```

### Phase A/B 検証（ログ改善のみ）
- 取引数・勝率・PFが変わらないことを確認
- CSVに新カラム（strategy_id, trigger_price, fill_price）が出力されること
- score_*フィールドが全トレードで0でないこと
- exit_reasonが新ラベル（SL_HIT, TP_HIT, TRAIL_HIT, BE_HIT等）であること

### Phase C 検証（フィルター変更）
- 18-21 UTC帯の低スコアトレード（<6.4）がフィルタリングされること
- 全体のPF・勝率の変化を確認

### 回帰テスト
```bash
python -m pytest tests/ -x
```
