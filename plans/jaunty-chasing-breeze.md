# AutoTraderV4 トレードロジック改善計画

## Context

トレードロジックの可視化調査で、3つの重大な問題（安全装置の機能不全、エントリーガードのバイパス、デッドコード）と4つの設計上の歪み、3つの軽微な品質問題を特定。
本計画はユーザー指定のロードマップ順に全10項目を修正する。

## 実行順序と依存関係

```
Phase 1 (🔴 重大): #3 → #1 → #2
Phase 2 (🟡 中程度): #5 → #4 → #6 → #7
Phase 3 (🟢 軽微): #9 → #8 → #10
```

各Phase完了後: `pytest tests/ -x` で全350+テストPASS確認 + 1ヶ月バックテスト比較

---

## Phase 1: 重大修正

### Fix #3: HTFデッドコード除去 + 役割整理（係数化）

**対象**: `src/autotrader/decision/unified/trade_bot.py`, `src/autotrader/decision/unified/timeframe_evaluator.py`

**A. デッドコード除去** (trade_bot.py)
- `_check_htf_trend_alignment()`内のreturn文後のRSIチェックコード（約15行）を削除

**B. HTFを加点→係数に変換** (timeframe_evaluator.py)
- `_score_htf_alignment()` の戻り値を (bonus: float, reason) → (multiplier: float, reason) に変更
  - aligned_count >= 2 → `1.3`（旧+4.0に相当）
  - aligned_count >= 1, contrary=0 → `1.15`（旧+2.0に相当）
  - contrary_count >= 2 → `0.7`（新：逆行ペナルティ追加）
  - contrary_count >= 1 → `0.85`
  - その他 → `1.0`
- `_calculate_score()`の呼び出し箇所: 加算→乗算に変更
  ```python
  # 旧: buy_score += htf_bonus
  # 新: buy_score = buy_score * htf_mult
  ```
- `ScoreBreakdown.htf`のdocstringを乗数の旨に更新

**テスト**: `test_timeframe_evaluator.py` に乗数範囲[0.7, 1.3]のテスト4件追加

---

### Fix #1: SoftGuardコンテキスト完全化

**対象**: `src/autotrader/decision/unified/trade_bot.py`

**変更内容**:

1. **sg_context構築をメソッド化** — `_build_soft_guard_context()` 新設
   ```python
   def _build_soft_guard_context(self, current_time, regime_result,
                                  tf_signals, consensus, plan) -> dict:
       return {
           "spread_pips": self._get_spread_pips(current_time),
           "current_time": current_time.to_pydatetime(),
           "atr_ratio": self._get_atr_ratio(plan.primary_tf, current_time),
           "recent_losses": self.state.consecutive_losses,
           "mtf_alignment": self._assess_mtf_alignment(tf_signals, consensus.direction),
           "trend_strength": regime_result.trend_strength,
       }
   ```

2. **`_get_atr_ratio()` 新設** — primary_tfの行から`atr_14 / atr_ma_20`を計算、欠損時1.0

3. **`_assess_mtf_alignment()` 新設** — tf_signalsの方向を集計→"aligned"/"mixed"/"conflicting"

4. **`_get_spread_pips()` 準備** — `_spread_provider`属性追加、`set_spread_provider()`メソッド追加（Phase 3 #9で実際の注入）

**後方互換**: バックテストでは`_spread_provider=None`→1.5フォールバック。atr_ratio/recent_losses/trend_strengthは既存データから取得。

**テスト**: 新ファイル `tests/unit/decision/unified/test_soft_guard_context.py`
- `_build_soft_guard_context()`が全6キーを返す
- `consecutive_losses=3` → penalty=0.2確認
- `atr_ratio=0.4`(低ボラ) → penalty=0.1確認
- MTF alignment "conflicting" → penalty=0.15確認

**受け入れ基準**: 連敗3でペナルティ発動ログ、spread悪化でエントリー率低下

---

### Fix #2: check_entry_conditions()有効化

**対象**: `src/autotrader/decision/unified/trade_bot.py`

**変更内容**:

1. **`_get_completed_tfs()` 新設** — current_timeの分/時から確定TFセットを返す
   ```python
   def _get_completed_tfs(self, current_time) -> set[str]:
       minute, hour = current_time.minute, current_time.hour
       completed = {"M1"}
       if minute % 5 == 0: completed.add("M5")
       if minute % 15 == 0: completed.add("M15")
       if minute == 0: completed.add("H1")
       if minute == 0 and hour % 4 == 0: completed.add("H4")
       if minute == 0 and hour == 0: completed.add("D1")
       return completed
   ```

2. **consolidate()呼び出しを置換** (line ~435)
   ```python
   # demo_modeはconsolidate()直呼びを維持
   if self.config.demo_mode:
       consensus = self.consensus.consolidate(consensus_signals, plan)
   else:
       completed = self._get_completed_tfs(current_time)
       if plan.entry_tf in completed:
           consensus = self.consensus.check_entry_conditions(
               consensus_signals, plan, plan.entry_tf)
       else:
           return self._hold_signal(f"entry_tf({plan.entry_tf})未確定")
   ```

**既存API**: `check_entry_conditions(tf_signals, plan, completed_tf)` がmode_aware_consensus.py:283に既に存在（確認済み）。内部で`consolidate()`を呼び、entry_tf方向一致もチェック。

**注意**: エントリー頻度が大幅に減少する（DAY_TRADEは5分毎、SWINGは1時間毎にしか判定されない）。これは設計意図通り。

**テスト**: `test_trade_bot.py`
- minute=3でentry_tf=M5 → HOLD("entry_tf未確定")
- minute=5でentry_tf=M5 → 通常処理
- entry_tf方向不一致 → HOLD
- demo_mode → バイパス確認

**受け入れ基準**: 非close時刻で必ずHOLD理由、方向不一致でエントリーゼロ

---

## Phase 2: 設計改善

### Fix #5: EMAクロス非対称ペナルティ緩和

**対象**: `src/autotrader/decision/unified/timeframe_evaluator.py` (lines ~401-406)

**変更**: 逆方向ペナルティ -2.5 → **-1.2**（比率5:1 → 2.4:1）

```python
# 旧: buy_score -= 2.5 / sell_score -= 2.5
# 新: buy_score -= 1.2 / sell_score -= 1.2
```

**テスト**: ScoreBreakdownのema_cross値確認2件

---

### Fix #4: MAX_POSSIBLE_SCORE統一

**対象**: `src/autotrader/decision/unified/timeframe_evaluator.py`

**変更**:
- `MAX_POSSIBLE_SCORE` = Fix #3(HTF係数化)後の実際最大値に更新
  - core(5.0)+ADX(2.0)+RSI(1.0)+MACD_slope(2.5)+div(1.5)+EMA(0.5)+stoch(0.5) = **13.0**
  - ※HTF乗数(最大1.3x)はscoreに後掛けだが、15.0定数は掛け前の13.0にすべき
- `compute_max_possible_score()` classmethodを追加（各インジケータ最大値の合計を返す）
- `NORMALIZED_MIN_SCORES`は自動的に再計算される（`ratio × MAX_POSSIBLE_SCORE`）

**テスト**: `compute_max_possible_score() == MAX_POSSIBLE_SCORE`の一致テスト

---

### Fix #6: TREND → DAY_TRADE許可

**対象**: `src/autotrader/decision/unified/mode_selector.py`

**変更**: `_select_mode()`のTRENDブランチを条件分岐に拡張
```python
if regime == TREND:
    if abs(htf_alignment) >= threshold or volatility < 0.8:
        return SWING, "TREND_SWING"
    if is_active_session and 0.8 <= volatility <= 1.3:
        return DAY_TRADE, "TREND_DAY_ACTIVE"
    return SWING, "TREND_SWING_DEFAULT"
```

**テスト**: `test_mode_selector.py`に3ケース追加

---

### Fix #7: フィルターチェーン統合

**対象**: `src/autotrader/decision/unified/trade_bot.py`

**変更**: 11個のインラインフィルタ(~170行)を`_apply_entry_filters()`メソッドに抽出
- **Hard blocks**: HTF整合、SoftGuard≥0.8、London UTC7、RANGE+DAY弱トレンド
- **Threshold adjustments**: TOKYO/WeakHours/TokyoNight/RANGE+DAYプレミアム
- **Signal quality**: BB幅、MACDスロープ
- 重複(UTC 4-6)を整理

**テスト**: `TestEntryFilters`クラスに各フィルタ独立テスト5件

---

## Phase 3: 軽微修正

### Fix #9: 実スプレッド取得

**対象**: `src/autotrader/live/engine.py`, `src/autotrader/decision/unified/trade_bot.py`

**変更**:
- engine.pyで`_cached_spread`属性追加、tick毎にMT5から更新
- `bot.set_spread_provider(self._get_cached_spread)`で注入
- バックテスト: `bot.set_spread_provider(lambda: config.spread_pips)`

### Fix #8: SL/TP二重定義統合

**対象**: `src/autotrader/decision/unified/timeframe_evaluator.py`

**変更**: 未使用`ATR_MULTIPLIERS` dict削除、`SL_ATR_MULTIPLIERS`/`TP_DEFAULT_RATIOS`をclass定数に統一

### Fix #10: 保険SLの適応化

**対象**: `src/autotrader/decision/unified/position_manager.py`

**変更**: `-0.1R`固定 → `max(r_based_offset, spread×3)`、上限`0.3R`

---

## 修正ファイル一覧

| ファイル | Fix# |
|---------|------|
| `src/autotrader/decision/unified/trade_bot.py` | #1, #2, #3, #7 |
| `src/autotrader/decision/unified/timeframe_evaluator.py` | #3, #4, #5, #8 |
| `src/autotrader/decision/unified/mode_selector.py` | #6 |
| `src/autotrader/decision/unified/position_manager.py` | #10 |
| `src/autotrader/live/engine.py` | #9 |
| `tests/unit/decision/unified/test_soft_guard_context.py` (新規) | #1 |
| `tests/unit/decision/unified/test_trade_bot.py` | #2, #7 |
| `tests/unit/decision/unified/test_timeframe_evaluator.py` | #3, #4, #5, #8 |
| `tests/unit/decision/unified/test_mode_selector.py` | #6 |
| `tests/unit/decision/unified/test_position_manager.py` | #10 |
| `tests/unit/live/test_engine.py` | #9 |

## 検証手順

1. 各Phase完了後: `pytest tests/ -x` (全350+テストPASS)
2. Phase 1完了後: 1ヶ月バックテスト実行 → エントリー数・PF・勝率を基準値と比較
3. Phase 2完了後: 同じバックテスト → Phase 1との差分確認
4. 受け入れ基準チェック:
   - [ ] SoftGuardに3+キー渡される（spread/atr_ratio/consecutive_losses）
   - [ ] consecutive_losses=3 → penalty=0.2のログ
   - [ ] entry_tf非確定時 → HOLD("entry_tf未確定")
   - [ ] 方向不一致 → エントリーゼロ
   - [ ] HTFデッドコード除去済み
   - [ ] HTF係数化(0.7-1.3)
   - [ ] MAX_POSSIBLE_SCORE = 13.0 (テスト検証)
   - [ ] EMA逆行ペナルティ = -1.2
   - [ ] TREND+DAY_TRADE可能
   - [ ] フィルタ統合メソッド化
