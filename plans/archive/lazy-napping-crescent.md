# 勝率70% / 年間収益率50% 改善計画

## Context

現在のP0統合ベスト（2020-2024）: 勝率65.6%, PF 1.75, 年間収益率36.8%, DD 2.60%
データ分析（2010-2025, 8525件）で「削るべき負け」と「伸ばせる勝ち」が特定済み。
3つのエントリーフィルタ + 早期BE移動 + バックテスト精度改善で目標達成を狙う。

---

## Phase 1: エントリーフィルタリング（勝率+3-5pt）

全て `trade_bot.py` の `_generate_signal_new()` 内に追加。

### 1A. penalty>=0.8 常時ブロック

**修正**: `trade_bot.py` 行523-530
- `self.config.connect_soft_guard` ガードを削除し、penalty>=0.8ブロックを常時有効化
- confidence調整（行583-601）は `connect_soft_guard` ガード付きのまま維持
- P1テストで利益減だったのはconfidence調整が原因。ブロックのみ分離すれば安全

```python
# 変更前
if (
    self.config.connect_soft_guard
    and sg_result.total_penalty >= 0.8
):

# 変更後
if sg_result.total_penalty >= 0.8:
```

### 1B. TOKYO off-hours スコアフィルター

**修正**: `trade_bot.py` 行530の後に追加
- UTC 4-7時 + penalty>0 + consensus.score<6.4 → HOLD
- TOKYO+penalty=0.15でscore<6.4の群: 合計-55,940損失

```python
hour_utc = current_time.hour if hasattr(
    current_time, 'hour'
) else current_time.to_pydatetime().hour
if (
    4 <= hour_utc <= 7
    and sg_result.total_penalty > 0
    and consensus.score < 6.4
):
    return self._hold_signal(
        f"TOKYOオフ時間フィルター: hour={hour_utc}, "
        f"score={consensus.score:.1f}<6.4"
    )
```

### 1C. TREND+SWING 弱トレンド制限

**修正**: `trade_bot.py` 行541の後（RANGE+DAY制限の直後）に追加
- TREND + SWING + trend_strength<0.6 → HOLD

```python
if (
    regime_result.regime == MarketRegime.TREND
    and plan.mode == TradingStrategyMode.SWING
    and regime_result.trend_strength < 0.6
):
    return self._hold_signal(
        f"TREND+SWING制限: trend_strength="
        f"{regime_result.trend_strength:.2f}"
    )
```

---

## Phase 2: 早期ブレークイーブン移動（勝率+1-2pt, 収益率++）

SL exits中38%がMFE>=0.5R。0.7R到達でBE移動し、取りこぼしを削減。

### 2A. PositionManagerConfig拡張

**修正**: `position_manager.py` PositionManagerConfig
```python
early_breakeven_r: float = 0.7
early_breakeven_enabled: bool = True
```

### 2B. 早期BEロジック追加

**修正**: `position_manager.py` `_check_partial_close()`の1Rチェック前
- `self._early_be_applied: set[str]` を追加（init/reset/unregisterにも）
- 0.7R到達で `ManagementAction.update_sl(entry_price)` を返す
- 1R到達時のBE移動と重複しないよう `_early_be_applied` で管理

### 2C. パラメータスイープ

early_breakeven_r = 0.5, 0.6, 0.7, 0.8 で比較検証。
ノイズ負け（BEで決済後にTP方向へ進む）のリスクとのバランスを確認。

---

## Phase 3: バックテスト精度改善

### 3A. ギャップ約定処理

**修正**: `simulator.py` `_check_exit_conditions()` + `_check_exit_conditions_pm()`
- バーのopenがSL/TPを超過（ギャップ）→ open価格で約定
- 現状: 常にSL/TP価格で約定 → sl_pips=15なのにpips=-270のような異常ケースの原因

```python
# BUY + SL例
if sl and candle.low <= sl:
    if candle.open < sl:  # ギャップ: openがSL越え
        return candle.open - slip, ExitReason.STOP_LOSS
    return sl - slip, ExitReason.STOP_LOSS
```

### 3B. position_id 追加（4ファイル）

| ファイル | 修正内容 |
|---------|---------|
| `entities.py` Trade | `position_id: str \| None = None` 追加 |
| `events.py` TradeEvent | `position_id: str = ""` 追加 |
| `file_listener.py` CSV_COLUMNS | `"position_id"` 追加 |
| `simulator.py` _close_position | Trade生成時に `position_id=position.position_id` 設定 |

---

## 実装順序

```
Step 1: Phase 1A (penalty>=0.8ブロック) → バックテスト比較
Step 2: Phase 1B (TOKYOフィルター) → バックテスト比較
Step 3: Phase 1C (TREND+SWING制限) → バックテスト比較
Step 4: Phase 1 全体統合 → バックテスト比較
Step 5: Phase 2 (早期BE移動 + パラメータスイープ) → バックテスト比較
Step 6: Phase 3A (ギャップ処理) → 全体再バックテスト
Step 7: Phase 3B (position_id) → CSV出力確認
```

各ステップのバックテスト: `uv run python scripts/run_backtest.py 2020 2024`

## 期待効果

| 改善 | 勝率寄与 | 取引数影響 |
|------|---------|-----------|
| 1A penalty>=0.8 ブロック | +1-2pt | -5-8% |
| 1B TOKYO off-hours フィルター | +0.5-1pt | -2-3% |
| 1C TREND+SWING制限 | +0.5-1pt | -1-2% |
| 2 早期BE移動 | +1-2pt | 0% |
| **合計** | **+3-6pt → 68-72%** | **-8-13%** |

## 修正対象ファイル

- `src/autotrader/decision/unified/trade_bot.py` — Phase 1全体
- `src/autotrader/decision/unified/position_manager.py` — Phase 2
- `src/autotrader/backtest/simulator.py` — Phase 3A
- `src/autotrader/core/entities.py` — Phase 3B
- `src/autotrader/backtest/events.py` — Phase 3B
- `src/autotrader/backtest/file_listener.py` — Phase 3B
