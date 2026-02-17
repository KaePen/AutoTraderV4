# P0改善4項目の実装計画

## Context
2015-2025バックテスト分析（trades_20260209_192219.csv）から、TOKYO×penalty=0.15が足を引っている（1,145件、勝率54.24%、PF 0.99）ことが判明。加えてログ品質の問題（strategy_id欠落752件、trigger_price欠落）も発見。「壊さず増益」のP0改善4項目を実装する。

---

## P0-1: TOKYOオフ時間フィルター閾値引上（6.4→6.6）

**ファイル**: `src/autotrader/decision/unified/trade_bot.py`
**場所**: `_generate_signal_new()` 行524-532

**変更内容**:
- 行525: `4 <= hour_utc <= 7` → `4 <= hour_utc <= 6`（hour=7はP0-2で分離）
- 行527: `consensus.score < 6.4` → `consensus.score < 6.6`
- ログメッセージも`<6.6`に更新

**期待効果**: 取引数-615、勝率+2.53pt、PF+0.19、純利益+111k

---

## P0-2: LONDON×penalty=0.15ブロック

**ファイル**: `src/autotrader/decision/unified/trade_bot.py`
**場所**: P0-1の直前に挿入（行524付近）

**追加コード**:
```python
# LONDONオフ時間ブロック（hour=7はLONDON境界）
if hour_utc == 7 and sg_result.total_penalty > 0:
    return self._hold_signal(
        f"LONDONオフ時間ブロック: hour={hour_utc}, "
        f"penalty={sg_result.total_penalty:.2f}"
    )
```

**理由**: LONDON×penalty=0.15は97件/PF 0.99で僅差マイナス。hour=7のみ（UTC 7時はoptimal_hours=range(8,18)の外でOFF_HOURS=0.15が付く唯一のLONDON時間帯）

---

## P0-3: strategy_id欠落（score=0経路）の修正

**ファイル**: `src/autotrader/backtest/runner.py`
**場所**: `_run_unified_year()` 行1103, 1110

**根本原因**: 部分利確後にPositionオブジェクトのvolumeが変わると、`pos not in prev_positions`がTrueになり、既存ポジションを「新規」と誤検出。結果、HOLD時のconsolidatedデータ（score=0、strategy_id=""）で`_pos_mode_regime`が上書きされる。

**変更内容**:
```python
# Before (行1103)
prev_positions = simulator.get_open_positions()
# After
prev_position_ids = {
    p.position_id
    for p in simulator.get_open_positions()
}

# Before (行1110)
if pos not in prev_positions:
# After
if pos.position_id not in prev_position_ids:
```

**効果**: 752件のstrategy_id=NaN/score=0問題が解消。トレード品質に影響なし（検出ロジックのみ修正）。

---

## P0-4: trigger_price記録の修正

### 4a: PositionManager経由のexit

**ファイル**: `src/autotrader/decision/unified/position_manager.py`

**変更1**: `_check_time_exit`にcurrent_price引数追加（行391-407）
```python
def _check_time_exit(
    self,
    position: ManagedPosition,
    current_time: datetime,
    current_price: float,  # 追加
) -> ManagementAction | None:
    ...
    if elapsed >= max_minutes:
        return ManagementAction.full_close(
            ...,
            trigger_price=current_price,  # 追加
        )
```

**変更2**: `_check_signal_reversal`にcurrent_price引数追加（行409-430）
```python
def _check_signal_reversal(
    self,
    position: ManagedPosition,
    current_signal: SignalType,
    current_price: float,  # 追加
) -> ManagementAction | None:
    ...
    if is_reversal:
        return ManagementAction.full_close(
            ...,
            trigger_price=current_price,  # 追加
        )
```

**変更3**: `evaluate`メソッド内の呼び出し更新（行312, 318）
```python
# 行312
action = self._check_time_exit(position, current_time, current_price)
# 行318
action = self._check_signal_reversal(position, current_signal, current_price)
```

### 4b: PM無効時のSIGNAL_REVERSAL

**ファイル**: `src/autotrader/backtest/simulator.py`
**場所**: `process_candle()` 行277-281

```python
# Before
trade = self._close_position(
    pos, exit_price, candle.time,
    ExitReason.SIGNAL_REVERSAL,
    0.0,  # trigger_price
)
# After
trade = self._close_position(
    pos, exit_price, candle.time,
    ExitReason.SIGNAL_REVERSAL,
    candle.close,  # trigger_price
)
```

---

## 実装順序

```
1. P0-4 (trigger_price修正) ← 純粋に追加的、動作変更なし
2. P0-3 (strategy_id修正)  ← バグ修正、トレード判断に影響なし
3. P0-1+P0-2 (セッションフィルター) ← トレード判断を変更
4. バックテスト検証
```

---

## テスト修正

**ファイル**: `tests/unit/decision/unified/test_position_manager.py`
- `_check_time_exit`と`_check_signal_reversal`の呼び出しにcurrent_price引数を追加
- trigger_priceが正しく設定されるテストケース追加

**ファイル**: `tests/unit/decision/unified/test_trade_bot.py`
- セッションフィルターの閾値変更に合わせたテスト更新

---

## 検証手順

### Step 1: ユニットテスト
```bash
python -m pytest tests/unit/decision/unified/ -v
```

### Step 2: 全期間バックテスト（2015-2025）
```bash
python scripts/run_backtest.py --years 2015-2025
```

**期待値**:
- 取引数: ~4,075（現4,690から-615）
- 勝率: ~68%（現65.59%から+2.5pt）
- PF: ~1.93（現1.74から+0.19）
- strategy_id=NaN: 0件（現752件）
- trigger_price=0のTIME/SIGNAL_REV: 0件

### Step 3: ホールドアウト検証（2023-2025 out-of-sample）
```bash
python scripts/run_backtest.py --years 2023-2025
```
- In-sampleと同方向の改善があればOK

### Step 4: CSV品質チェック
```python
# strategy_id欠落チェック
# trigger_price欠落チェック（TIME/SIGNAL_REV）
```
