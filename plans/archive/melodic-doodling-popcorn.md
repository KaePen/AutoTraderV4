# 次期改善タスク実装計画

## Context

`plans/next_task.md` のデータ分析結果に基づく改善実装。
現在のベスト: 取引4,624 / 勝率65.44% / PF 1.74 / 純利益+2,899,181 / DD平均1.77%

**最重要発見**: PM（PositionManager）有効時のSL/TP判定が `candle.close` のみで行われており、
非PM経路の `candle.low/high` 判定と不整合。H4足のSWING_TRENDで1R到達を見逃し、
「MFE>=0.5Rなのに負ける」問題の根本原因の一つ。

---

## 実装順序と内容

### Step 1: P0-1 SL/TP約定モデル修正（最重要・検証品質）

**問題**: `simulator.py` L437-443 で `_pm.evaluate(current_price=candle.close)` のみ使用。
PM内部の `_check_sl()` は `current_price <= current_sl`、`_check_tp()` も `current_price >= tp` で
close価格のみ判定。非PM経路は `candle.low <= sl` / `candle.high >= tp` でhigh/low判定。

**修正方針**: `_check_exit_conditions_pm()` にhigh/low SL/TP先行判定を追加。
SL/TPがhigh/lowで到達していれば非PM経路と同等のギャップ約定ロジックを使用。
未到達の場合のみPM evaluate()で時間決済/反転/部分利確/トレーリングを判定。

**修正ファイル**:
- `src/autotrader/backtest/simulator.py` — `_check_exit_conditions_pm()` (L410-486)
  - Step1: PM内部のcurrent_sl/original_tpを取得し、high/lowでSL/TP判定（ギャップ約定対応）
  - Step2: high/lowで1R到達を判定（BUYなら `candle.high >= 1r_price`）→ PM部分利確呼出
  - Step3: 上記未到達時のみ `pm.evaluate(candle.close)` で残り判定
  - BE/Trailing/通常SLの識別はPM内部のcurrent_sl・trailing_activated・entry_priceで判定

**注意点**:
- PM.get_position()でManagedPositionのcurrent_sl, original_tp, entry_price, r_valueを取得
- 1R/2R部分利確の判定もhigh/lowベースに変更（`candle.high >= entry + r_value` for BUY）
- PM内部の`_partial_closed_1r`等のフラグ管理との整合を保つ
- 設定フラグ: `SimulatorConfig.pm_use_highlow: bool = True`

**テスト**:
- `test_pm_sl_hit_by_low`: close>SL だが low<=SL → SLヒット
- `test_pm_tp_hit_by_high`: close<TP だが high>=TP → TPヒット
- `test_pm_1r_partial_by_high`: close<1R だが high>=1R → 部分利確実行
- `test_pm_gap_down_sl`: open<SL → ギャップ約定(open-slip)

---

### Step 2: P0-2 DAY_TRADE_RANGE x TOKYO x penalty=0.00 フィルター

**根拠**: 200件 / PF 0.89 / 純利益-21,853。score>=6.4で66件/PF1.11に改善。

**修正ファイル**:
- `src/autotrader/decision/unified/trade_bot.py` — `_generate_signal_new()` L552後に追加
  ```
  RANGE + DAY_TRADE + TOKYO(4-6UTC) + penalty==0.0 + score<6.4 → HOLD
  ```
- `src/autotrader/decision/unified/config.py` — `UnifiedBotConfig` に追加
  - `enable_tokyo_range_zero_penalty_filter: bool = True`
  - `tokyo_range_zero_penalty_threshold: float = 6.4`

**テスト**: `tests/unit/decision/unified/test_trade_bot.py`
- HOLD条件の正常系/通過条件/フラグOFF時

---

### Step 3: P0-3 SIGNAL_REV安全化

**根拠**: 113件 / 純利益-141,533 / 平均MFE 0.59R。含み損時の反転決済を停止。

**修正ファイル**:
- `src/autotrader/decision/unified/position_manager.py`
  - `_check_signal_reversal()` (L411-434): `position.current_r < 0.2` なら反転無視
  - `PositionManagerConfig` に追加:
    - `signal_reversal_min_r_enabled: bool = True`
    - `signal_reversal_min_r: float = 0.2`
- `src/autotrader/backtest/simulator.py` (L270-282): 非PM経路にも含み益チェック追加

**テスト**: `tests/unit/decision/unified/test_position_manager.py`
- current_r<0.2 → HOLD / current_r>=0.2 → FULL_CLOSE / フラグOFF

---

### Step 4: P0-4 TP_1R trigger/fillログ

**問題**: position_event_logger.pyにtrigger_priceカラムなし。

**修正ファイル**:
- `src/autotrader/backtest/position_event_logger.py`
  - `POSITION_EVENT_COLUMNS` に `trigger_price` 追加
  - `log()` に `trigger_price: float = 0.0` パラメータ追加
- `src/autotrader/backtest/simulator.py`
  - `_partial_close_position()` に `trigger_price` パラメータ追加
  - L455-461の呼出元から `action.trigger_price` を渡す
  - L556-578のログ出力で `trigger_price` を記録

---

### Step 5: P1-1 DAY_TRADE_RANGE x TOKYO x penalty=0.15 閾値引上

**根拠**: 758件/PF1.44 → score>=6.2で415件/PF1.91（利益ほぼ維持）。

**修正ファイル**:
- `src/autotrader/decision/unified/trade_bot.py` — P0-2フィルターの直後に追加
  ```
  RANGE + DAY_TRADE + TOKYO(4-6UTC) + 0<penalty<=0.2 + score<6.2 → HOLD
  ```
- `src/autotrader/decision/unified/config.py`
  - `enable_tokyo_range_low_penalty_filter: bool = False` (P1なのでデフォルトOFF)
  - `tokyo_range_low_penalty_threshold: float = 6.2`

---

### Step 6: CLIフラグ統合・バックテスト検証

- `src/autotrader/backtest/adapters/cli.py` にフラグ追加:
  - `--no-tokyo-range-p0` / `--no-signal-rev-safe` / `--tokyo-range-p1` / `--no-pm-highlow`
- ホールドアウト検証: 2015-2022で調整、2023-2025で評価
- 感度テスト: 閾値±0.2で結果が崩れないか

---

## 検証手順

1. **Step 1完了後**: バックテスト実行し、非PM経路との結果比較
   - SL_HIT/TP_HIT/TP_1R件数が増加するはず（high/lowで検出漏れが解消）
   - SWING_TREENDの「MFE>=0.5Rで負け」率が低下するはず
2. **全Step完了後**: 全体バックテスト（2015-2025）
   - 目標: 勝率67%+, PF 1.8+, DD 1.5%以下
3. **ホールドアウト検証**: 2023-2025で未知データ評価
4. **感度テスト**: 各閾値±0.2
