# BT公正性改善 実装プラン

## 背景

バックテスト監査で発見された楽観バイアス3件を修正する。
先読みバイアス（look-ahead bias）は実質なし（確認済み）。

## Task 1: 次足open約定への変更（エントリー遅延）[HIGH]

### 問題
`simulator.py` の `_get_entry_price()` が `candle.close` で約定。
bar[i]のcloseを見てシグナル生成 → 同bar[i]のcloseで約定は現実に不可能。

### 修正方針
pending signal機構を追加。bar[i]でシグナル → bar[i+1]のopenで約定。

### 修正ファイル: `autotrader/backtest/simulator.py`

#### 1a. `__init__` にフィールド追加（L244付近）
```python
# 次足約定用: pending signal
self._pending_signal: Signal | None = None
self._pending_consensus: tuple[float, float] | None = None
self._pending_fundamental: object | None = None
```

#### 1b. `reset()` にクリア追加（L298付近）
```python
self._pending_signal = None
self._pending_consensus = None
self._pending_fundamental = None
```

#### 1c. `process_candle()` 冒頭にpending実行を追加（L331直後、exitチェック前）

```python
# 前足からのpendingエントリーを今足のopenで約定
if self._pending_signal is not None:
    self._execute_pending_entry(candle)
```

#### 1d. 新メソッド `_execute_pending_entry()` 追加

```python
def _execute_pending_entry(self, candle: Candle) -> None:
    """前足のpendingシグナルを今足openで約定"""
    signal = self._pending_signal
    self._pending_signal = None
    self._pending_consensus = None
    self._pending_fundamental = None

    state = self.state

    # 非PM時: 反対ポジションをopenで決済
    if not self._use_pm:
        for pos in list(state.open_positions):
            if self._is_opposite_signal(pos, signal):
                exit_price = self._get_exit_price_at_open(
                    pos.signal_type, candle,
                )
                trade = self._close_position(
                    pos, exit_price, candle.time,
                    ExitReason.SIGNAL_REVERSAL, candle.open,
                )
                # trades listはprocess_candleのローカル変数
                # → process_candle側でpending_tradesとして回収

    # ポジション枠チェック
    _eff_max = self.config.max_positions
    if (
        self.config.bonus_max_positions > 0
        and signal.consensus_score is not None
        and signal.consensus_score >= self.config.bonus_score_threshold
    ):
        _eff_max += self.config.bonus_max_positions
    if len(state.open_positions) < _eff_max:
        position = self._open_position(
            signal, candle, at_open=True,
        )
        if position:
            state.open_positions.append(position)
```

**注意**: `_execute_pending_entry` からのtrade回収が必要。
実装案: `_pending_trades: list[Trade]` フィールドを追加し、
`process_candle` 冒頭で `trades.extend(self._pending_trades)` する。

#### 1e. `_get_entry_price()` を修正（L1097-1106）

`at_open` パラメータを追加:
```python
def _get_entry_price(
    self, signal_type: SignalType, candle: Candle,
    at_open: bool = False,
) -> float:
    # ... override_price 処理は変更なし ...
    spread = self._get_spread_for_candle(candle, self._current_row_data)
    half_spread = spread / 2
    base_price = candle.open if at_open else candle.close
    if signal_type == SignalType.BUY:
        return base_price + half_spread + self._slippage_price
    else:
        return base_price - half_spread - self._slippage_price
```

#### 1f. `_get_exit_price_at_open()` 新メソッド追加

```python
def _get_exit_price_at_open(
    self, signal_type: SignalType, candle: Candle,
) -> float:
    """open価格での決済価格"""
    spread = self._get_spread_for_candle(candle, self._current_row_data)
    half_spread = spread / 2
    if signal_type == SignalType.BUY:
        return candle.open - half_spread
    else:
        return candle.open + half_spread
```

#### 1g. `_open_position()` に `at_open` パラメータ追加（L796-812）

```python
def _open_position(
    self, signal: Signal, candle: Candle,
    strategy_id: str | None = None,
    at_open: bool = False,
) -> Position | None:
    entry_price = self._get_entry_price(
        signal.signal_type, candle, at_open=at_open,
    )
    # ... 以降変更なし
```

#### 1h. 即時エントリーをpending保存に変更

**シングルポジションパス（L391-394付近）**:
```python
# 変更前:
if not state.open_positions:
    position = self._open_position(signal, candle)
    if position:
        state.open_positions.append(position)

# 変更後:
if not state.open_positions:
    self._pending_signal = signal
    self._pending_consensus = consensus_scores
    self._pending_fundamental = fundamental_assessment
```

**マルチポジションパス（L457-460付近）**:
```python
# 変更前:
if len(state.open_positions) < _eff_max:
    position = self._open_position(signal, candle)
    if position:
        state.open_positions.append(position)

# 変更後:
if len(state.open_positions) < _eff_max:
    self._pending_signal = signal
    self._pending_consensus = consensus_scores
    self._pending_fundamental = fundamental_assessment
```

**非PMの反対シグナル決済もpending化（L376-388, L435-446）**:
反対シグナルによる決済は「成行注文」なので本来は次足openで執行すべき。
ただし、PM経路ではPM.evaluate()がclose価格で判定するため、
非PM経路のみpending化する。

→ 実装の複雑さを考慮し、**Phase 1ではエントリーのみpending化**。
反対シグナル決済のpending化はPhase 2で検討。

### 注意点

- 年の最終barでpendingが残る → 約定されずに消える（正しい動作）
- bot.state.equityとの同期: pendingエントリーはbot側に未反映だが、
  simulator側でmax_positions制約がかかるため二重エントリーは防止される
- year_runner.pyの変更は不要（simulator内部で完結）


## Task 2: PM経路にintrabar SL/TP判定を追加 [MEDIUM]

### 問題
`_check_exit_conditions_pm()` が `current_price=candle.close` でPM評価。
bar内でSLを貫通してもcloseがSL内なら見逃す → 勝率・DDが楽観的。

### 修正ファイル: `autotrader/backtest/simulator.py`

#### 2a. `_check_exit_conditions_pm()` の先頭にintrabarチェック追加（L552-558の間）

```python
def _check_exit_conditions_pm(
    self, position, candle, current_signal=None,
    consensus_scores=None, fundamental_assessment=None,
) -> tuple[float, ExitReason, float] | None:
    if self._pm is None:
        return None

    managed = self._pm.get_position(position.position_id)
    if managed is None:
        return None

    # === 追加: intrabar SL/TP判定（high/low使用） ===
    intrabar = self._check_intrabar_sl_tp(position, candle)
    if intrabar is not None:
        _fill, _reason, _trigger = intrabar
        # PMからポジション登録解除
        self._pm.unregister_position(position.position_id)
        # Exit詳細理由を保存
        self._exit_details[position.position_id] = (
            f"intrabar_{_reason.value}"
        )
        return intrabar
    # === 追加ここまで ===

    # 既存のPM evaluate処理（変更なし）
    atr = 0.002
    # ...
```

#### 2b. 新メソッド `_check_intrabar_sl_tp()` 追加

`_check_exit_conditions()` のSL/TP判定ロジックを再利用:
```python
def _check_intrabar_sl_tp(
    self, position: Position, candle: Candle,
) -> tuple[float, ExitReason, float] | None:
    """PM経路用: high/lowでSL/TPブリーチを検出

    _check_exit_conditions と同じロジックだが、
    PM経路から呼び出される専用メソッド。
    """
    sl = position.stop_loss
    tp = position.take_profit
    slip = self._slippage_price

    if position.signal_type == SignalType.BUY:
        if sl and candle.low <= sl:
            if candle.open < sl:
                return (candle.open - slip, ExitReason.STOP_LOSS, sl)
            return (sl - slip, ExitReason.STOP_LOSS, sl)
        if tp and candle.high >= tp:
            if candle.open > tp:
                return (candle.open - slip, ExitReason.TAKE_PROFIT, tp)
            return (tp - slip, ExitReason.TAKE_PROFIT, tp)
    else:
        if sl and candle.high >= sl:
            if candle.open > sl:
                return (candle.open + slip, ExitReason.STOP_LOSS, sl)
            return (sl + slip, ExitReason.STOP_LOSS, sl)
        if tp and candle.low <= tp:
            if candle.open < tp:
                return (candle.open + slip, ExitReason.TAKE_PROFIT, tp)
            return (tp + slip, ExitReason.TAKE_PROFIT, tp)

    return None
```

**代替案**: `_check_exit_conditions()` を直接呼ぶ。
ただしPM登録解除が必要なため、専用メソッドの方が安全。

### 注意点

- PMが `UPDATE_SL` で更新したSLは `position.stop_loss` に同期済み（L618-631）
  → 次barのintrabarチェックで更新済みSLが使われる（正しい動作）
- TPはPMで動的変更されない（position.take_profitは初期値のまま）


## Task 3: DD算出をmark-to-market化 [MEDIUM]

### 問題
`year_runner.py:846` で `metrics.max_drawdown_pct` を使用。
これは `MetricsCalculator._calculate_drawdown()` がクローズ済みトレードのみで
再構築したequity curveからの算出 → 含み損を反映していない。

一方、`simulator.state.max_drawdown` は `_update_equity()` + `_update_drawdown()` で
毎足mark-to-market更新済み。

### 修正ファイル: `autotrader/backtest/year_runner.py`

#### 3a. L846の1行変更

```python
# 変更前:
"max_drawdown": metrics.max_drawdown_pct * 100,

# 変更後:
"max_drawdown": simulator.state.max_drawdown * 100,
```

### 注意点

- `simulator.state.max_drawdown` は比率（0.0〜1.0）で格納されている
  → `* 100` でパーセント変換は同じ
- `metrics.max_drawdown_pct` も他の用途（equity curve出力等）で使われるため削除しない
- Sharpe ratio等の他メトリクスは `MetricsCalculator` 側のままで問題なし


## Task 4: テスト実行・検証

### 手順

1. `uv run pytest tests/ -x -q` で全テスト実行
2. 失敗テストの修正（主にsimulator関連）:
   - エントリー価格がclose→openに変わるため、期待値の修正が必要
   - PM経路のSL/TP判定追加で追加のexit発生の可能性
3. 新規テスト追加を検討:
   - `test_pending_signal_entry.py`: pendingが次足openで約定されること
   - `test_pm_intrabar_sltp.py`: PM経路でhigh/lowのSL貫通が検出されること
   - `test_dd_mark_to_market.py`: DDが含み損を反映すること

### 関連テストファイル

- `tests/unit/backtest/test_simulator.py` - 主要
- `tests/unit/backtest/test_year_runner.py` - DD出力
- `tests/unit/decision/test_*.py` - 間接的に影響の可能性


## Task 5: 比較バックテストのキュー投入

### 手順

1. PRマージ後、ローカルmainをpull
2. 以下のジョブを `D:\Projects\AutoTraderV4_data\backtest_queue.json` に追加:

```json
{
  "jobs": [
    {
      "id": "FAIR-M1-6jpy",
      "type": "multi_pair",
      "symbols": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY"],
      "years": "2023-2025",
      "description": "BT公正性改善後 6JPY比較（修正前: T47 WR81.6% DD1.32%）",
      "multi_pair_config": {
        "name": "6JPY-FAIR",
        "global_max_positions": 6,
        "per_pair_max_positions": 1,
        "global_max_exposure_lot": 10.0,
        "base_risk_pct": 0.003,
        "consensus_threshold": 17.0,
        "spread_multiplier": 1.0,
        "test_name": "T47"
      }
    }
  ]
}
```

### 比較対象

修正前（T47検証済み）:
- WR: 81.6%, PF: 3.70, DD: 1.32%, Sharpe: 7.62

修正後の予想:
- WR: 数%低下（PM intrabar SLによるSL検出増加）
- DD: 数%上昇（mark-to-market化 + 次足open約定のスリップ）
- PF: 若干低下

### 既存ジョブの扱い

**重要**: `backtest_queue.json` の既存ジョブは削除しない。末尾に追加する。


## 実装順序

```
Task 1 (次足open約定) → Task 2 (PM intrabar) → Task 3 (DD m2m)
                                                      ↓
                                              Task 4 (テスト検証)
                                                      ↓
                                         PR作成 → マージ → pull
                                                      ↓
                                              Task 5 (比較BT)
```

Task 1-3 は同一worktree・同一ブランチ・1PRで実施可能。

## worktreeワークフロー

```bash
# 1. セッション開始時の掃除
git worktree list
git worktree prune
rm -rf .claude/worktrees/*/

# 2. worktree作成
EnterWorktree name="fix/bt-fairness-improvements"

# 3. 実装（Task 1-3）
# 4. テスト（Task 4）
# 5. コミット・push・PR作成・マージ
# 6. ExitWorktree → pull → BT投入（Task 5）
```
