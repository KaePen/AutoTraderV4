# M1 Smart Execution Layer（M1スマート実行層）

## Context

マクロ足（H1/H4/D1）のコンセンサスは有効だが、M1レベルの実行品質が低い。
シグナル生成（何をトレードするか）と注文実行（いつ/どう入るか）が分離されておらず、
コンセンサス発火→即座にM1足closeで約定する。M1マイクロ構造を一切考慮しないため、
マイクロレンジの天井/底付近でエントリーし、SLに引っかかる。

M1マイクロ反転フィルタ（T1-T6）は効果限定的（-1%～-8.5%）。
極値除外（negative screening）は良いトレードも排除するため利益を削る。

**アプローチ転換**: フィルタリング（除外）ではなく**実行品質の向上**（構造的SL・エントリー最適化）。

## 3つのメカニズム（各独立ON/OFF）

### 1. M1 Structure SL（構造的SL）— 最優先

ATRベースSL → M1スイングレベルベースSLに置換。

- BUY: `SL = last_swing_low - buffer_pips`
- SELL: `SL = last_swing_high + buffer_pips`
- min/max制限付き（デフォルト 15-60 pips）
- M1の`last_swing_low`/`last_swing_high`は`PrecomputeEngine`で計算済み

**変更ファイル**:
- `autotrader/decision/unified/config.py` — 4フィールド追加
- `autotrader/decision/unified/trade_bot.py` — SL計算ブロック（L1135付近）に挿入

**config追加**:
```python
m1_structure_sl_enabled: bool = False
m1_structure_sl_buffer_pips: float = 3.0
m1_structure_sl_min_pips: float = 15.0
m1_structure_sl_max_pips: float = 60.0
```

**挿入箇所**: `sl_pips = primary_signal.sl_pips * _overrides.sl_multiplier` の直後、
TREND SL min/max上書きの前。

```python
# M1構造的SL
if self.config.m1_structure_sl_enabled:
    _m1_row_sl = self._get_current_row("M1", current_time)
    if _m1_row_sl is not None:
        _pip_unit = 0.01  # JPYペア
        _current_close = candle.close if candle else (
            _m1_row_sl.get("close") if _m1_row_sl is not None else None
        )
        if _current_close is not None:
            if consensus.direction == SignalType.BUY:
                _swing = _m1_row_sl.get("last_swing_low")
                if _swing is not None and not pd.isna(_swing):
                    _struct_sl = (
                        (_current_close - float(_swing))
                        / _pip_unit
                        + self.config.m1_structure_sl_buffer_pips
                    )
                    _struct_sl = max(
                        self.config.m1_structure_sl_min_pips,
                        min(_struct_sl, self.config.m1_structure_sl_max_pips),
                    )
                    sl_pips = _struct_sl
            elif consensus.direction == SignalType.SELL:
                _swing = _m1_row_sl.get("last_swing_high")
                if _swing is not None and not pd.isna(_swing):
                    _struct_sl = (
                        (float(_swing) - _current_close)
                        / _pip_unit
                        + self.config.m1_structure_sl_buffer_pips
                    )
                    _struct_sl = max(
                        self.config.m1_structure_sl_min_pips,
                        min(_struct_sl, self.config.m1_structure_sl_max_pips),
                    )
                    sl_pips = _struct_sl
```

**Simulator変更**: 不要（sl_pipsは既存経路で伝搬）

---

### 2. M1 Execution Gate（実行ゲート）

Negative screening（極値除外）ではなく**Positive screening（好条件確認）**。
M1が方向に「味方している」ことを確認してからエントリー。

**3条件の加重スコア**:
1. **EMAアラインメント** (weight=1.0): BUY→ close>ema_20 AND ema_10>ema_20
2. **バーモメンタム** (weight=0.5): BUY→ close>open（陽線）
3. **BB健全ゾーン** (weight=0.5): bb_percent_b が 0.3-0.7 の範囲内

スコアがthreshold未満 → HOLD（次足で再判定、コンセンサスは毎足再計算されるので自然にリトライ）

**変更ファイル**:
- 新規: `autotrader/constraint/filters/m1_execution_gate.py`
- `autotrader/decision/unified/config.py` — 7フィールド追加
- `autotrader/decision/unified/trade_bot.py` — 初期化 + BCA後に判定挿入

**config追加**:
```python
m1_exec_gate_enabled: bool = False
m1_exec_gate_ema_weight: float = 1.0
m1_exec_gate_bar_weight: float = 0.5
m1_exec_gate_bb_weight: float = 0.5
m1_exec_gate_bb_low: float = 0.3
m1_exec_gate_bb_high: float = 0.7
m1_exec_gate_threshold: float = 1.0
```

**挿入箇所**: `_generate_signal_new()` 内、micro_reversal_filter チェックの直後。

---

### 3. Retrace Entry（リトレースエントリー）— 最高インパクト

コンセンサス発火後、即時エントリーせずM1のリトレース（押し/戻り）を待つ。
エントリー価格が改善 → MAE低減 → SLヒット率低下。

**仕組み**:
1. コンセンサスBUY発火 → `target_price = close - M1_ATR × factor` で保留
2. 後続M1バーの`low ≤ target_price` → target_priceでエントリー
3. `max_wait_bars`超過 → fallbackで現在価格エントリーまたはキャンセル

**変更ファイル**:
- `autotrader/core/entities.py` — `Signal`に`entry_price: float | None = None`追加
- `autotrader/decision/unified/scoring/consolidator.py` — `ConsolidatedSignal`に`entry_price`追加
- `autotrader/decision/unified/trade_bot.py` — `PendingEntry`状態 + ロジック
- `autotrader/decision/unified/config.py` — 4フィールド追加
- `autotrader/backtest/year_runner.py` — entry_price伝搬（SL/TP基準価格をcandle.closeからentry_priceに変更）
- `autotrader/backtest/simulator.py` — `_get_entry_price()`でentry_price override対応

**config追加**:
```python
m1_retrace_entry_enabled: bool = False
m1_retrace_atr_factor: float = 0.5
m1_retrace_max_wait_bars: int = 5
m1_retrace_fallback_entry: bool = True
```

**trade_bot変更概要**:
```python
# インスタンス変数追加
self._pending_entry: PendingEntry | None = None

# _generate_signal_new() 冒頭に保留チェック挿入
if self._pending_entry is not None:
    m1_row = self._get_current_row("M1", current_time)
    # リトレース到達判定 → エントリー実行
    # タイムアウト判定 → fallback or cancel
    # どちらでもない → HOLD（待機継続）

# コンセンサス通過後・最終シグナル構築前
if self.config.m1_retrace_entry_enabled:
    # PendingEntry作成、HOLD返却
```

**year_runner.py変更** (L272-282):
```python
_base_price = (
    consolidated.entry_price
    if consolidated.entry_price is not None
    else candle.close
)
if consolidated.direction == SignalType.BUY:
    sl_price = _base_price - sl_pips / 100
    tp_price = _base_price + tp_pips / 100
else:
    sl_price = _base_price + sl_pips / 100
    tp_price = _base_price - tp_pips / 100
```

**simulator.py変更**:
```python
def _get_entry_price(self, signal_type, candle, override_price=None):
    if override_price is not None:
        spread = self._get_spread_for_candle(candle)
        half_spread = spread / 2
        if signal_type == SignalType.BUY:
            return override_price + half_spread + self._slippage_price
        else:
            return override_price - half_spread - self._slippage_price
    # 既存ロジック...
```

---

## 実装順序

1. **Phase 1: M1 Structure SL** — 最小変更（config + trade_bot inline）
2. **Phase 2: M1 Execution Gate** — 新ファイル + 統合（micro_reversalと同パターン）
3. **Phase 3: Retrace Entry** — 最大変更（entities, consolidator, trade_bot, year_runner, simulator）
4. **Phase 4: テスト + ゴールデンテスト**
5. **Phase 5: バックテスト検証**

## テスト

- 各メカニズムのユニットテスト
- `tests/golden/test_backtest_golden.py` にデフォルトOFF確認テスト追加
- M1マイクロ反転フィルタの既存テストは影響なし

## バックテスト検証

```bash
T0: Baseline (全OFF)
T1: M1 Structure SL のみ
T2: M1 Execution Gate のみ
T3: Retrace Entry のみ
T4: Structure SL + Execution Gate
T5: 全3メカニズム
T6: 各メカニズムのパラメータチューニング
```

USDJPY 2020-2025。成功基準: WR改善 or PF改善、利益横ばい以上。
