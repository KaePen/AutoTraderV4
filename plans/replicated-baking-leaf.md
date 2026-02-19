# ティック監視 + M1シグナル エントリー最適化

## Context

現在のSCALPINGモードはM1足ベースでシグナル生成し、シグナル発生時に即座に成行注文を実行する。
しかしOHLCVデータ更新は60秒間隔のため、実際のエントリータイミングは市場のミクロ状態を反映していない。

**目的**: M1シグナルパイプラインを100%維持したまま、シグナル発生後にティックデータを短時間監視し、
スプレッド縮小・モメンタム確認等の最適条件でエントリーするレイヤーを追加する。

## アーキテクチャ変更

```
現在: M1シグナル生成 → 即座にMT5注文
変更: M1シグナル生成 → TickEntryOptimizer(監視開始) → tick polling(100ms) → 条件成立 → MT5注文
                                                     → タイムアウト(30s) → 成行注文
```

## Phase 1: MT5トランスポート層拡張

### `src/autotrader/adapters/mt5/connection.py`
- `MT5Transport` ABCに `copy_ticks_from()` 抽象メソッド追加（line 122の後）
- `DirectTransport` に実装追加（line 305の後）
- MT5 Python APIの `mt5.copy_ticks_from(symbol, date_from, count, flags)` をラップ
- 既存の `COPY_TICKS_ALL` 定数（`constants.py` line 84-87）を利用

### `src/autotrader/adapters/mt5/data_provider.py`
- `get_tick_fast(symbol)` 追加: セッションチェック省略の高速tick取得（高頻度ポーリング用）
- `get_recent_ticks(symbol, count)` 追加: 直近Ntick一括取得

## Phase 2: TickEntryOptimizer コア実装

### 新規: `src/autotrader/live/tick_entry_config.py`

```python
@dataclass(frozen=True)
class TickEntryConfig:
    enabled: bool = False
    enabled_modes: tuple[str, ...] = ("SCALPING",)
    poll_interval_sec: float = 0.1        # 100ms
    max_monitoring_sec: float = 30.0      # タイムアウト
    spread_threshold_pips: float = 1.5    # スプレッド閾値
    spread_weight: float = 0.4
    momentum_window_ticks: int = 10       # 方向一致評価tick数
    momentum_min_ticks: int = 5           # 最低観測数
    momentum_threshold: float = 0.6       # 方向一致率閾値
    momentum_weight: float = 0.35
    retracement_enabled: bool = False     # リトレース評価（オプション）
    retracement_weight: float = 0.25
    composite_threshold: float = 0.6      # 総合閾値
    execute_on_timeout: bool = True       # タイムアウト時成行実行
    conflict_policy: str = "replace"      # 新シグナル衝突時: replace/ignore/best
```

### 新規: `src/autotrader/live/tick_entry_optimizer.py`

**状態マシン**:
```
IDLE → MONITORING → EXECUTING → IDLE
                  → TIMED_OUT → IDLE
                  → CANCELLED → IDLE
```

**主要メソッド**:
- `start_monitoring(signal, cs)`: IDLE→MONITORING遷移、シグナル保持
- `poll_tick() -> EntryConditionResult | None`: 毎100msで呼出、tick取得→条件評価
- `cancel_monitoring(reason)`: 監視キャンセル
- `reset()`: IDLE復帰、バッファクリア
- `evaluate_conditions()`: 純粋関数、3条件のスコア計算

**エントリー条件評価**（3つのサブスコアの加重和）:
1. **スプレッド条件** (weight=0.4): 現在スプレッド ≤ 閾値で1.0、縮小傾向でボーナス
2. **マイクロモメンタム** (weight=0.35): 直近N tickの価格変動方向がシグナル方向と一致する割合
3. **リトレースメント** (weight=0.25, optional): シグナル方向と逆の一時的戻しを検出→有利な価格でエントリー

`composite_score >= composite_threshold` で実行判定。

## Phase 3: LiveTradingEngine統合

### `src/autotrader/live/config.py` (line 39の後)
- `LiveTradingConfig` に `tick_entry_config: TickEntryConfig` フィールド追加

### `src/autotrader/live/engine.py`

**`__init__`** (line 77付近): `TickEntryOptimizer` インスタンス生成

**`_tick`** (line 611-618を変更): シグナル→即実行の箇所を変更
```python
# 変更前
if self._enable_auto_trade and symbol_enabled:
    if self._conn.connected:
        await self._execute_entry(signal)

# 変更後
if self._enable_auto_trade and symbol_enabled:
    if self._conn.connected:
        if self._should_use_tick_optimizer(cs):
            self._tick_optimizer.start_monitoring(signal, cs)
        else:
            await self._execute_entry(signal)
```

**`_tick`** の末尾（`_manage_positions` の前）に監視ポーリング追加:
```python
if self._tick_optimizer.is_active:
    result = await self._tick_optimizer.poll_tick()
    if result is not None:
        if result.should_execute:
            await self._execute_entry(self._tick_optimizer.pending_signal)
        self._tick_optimizer.reset()
```

**`_main_loop`** (line 481-483): 動的スリープ間隔
```python
if self._tick_optimizer.is_active:
    await asyncio.sleep(self._config.tick_entry_config.poll_interval_sec)  # 100ms
else:
    await asyncio.sleep(self._config.check_interval_sec)  # 1s
```

**新規ヘルパーメソッド**:
- `_should_use_tick_optimizer(cs)`: enabled + mode判定 + conflict_policy処理
- `_start_tick_monitoring(signal, cs)`: 監視開始 + ログ出力

## Phase 4: テスト

### ユニットテスト
- `tests/unit/live/test_tick_entry_optimizer.py`: 状態遷移、条件評価、タイムアウト、衝突ポリシー
- `tests/unit/adapters/mt5/test_connection_ticks.py`: `copy_ticks_from` モックテスト
- 合成tickデータで全ロジックをテスト（MT5不要）

### 統合テスト
- `tests/unit/live/test_engine_tick_optimizer.py`: エンジン統合フローのテスト

## セーフガード

| リスク | 対策 |
|--------|------|
| MT5 API負荷 | `symbol_info_tick` は軽量（単一値）。10回/秒は許容範囲内 |
| シグナル失効 | `max_monitoring_sec=30` でM1足1本以内にタイムアウト |
| イベントループ占有 | `asyncio.sleep(0.1)` で毎回yield、WebSocket等は正常動作 |
| MT5切断 | `_conn.connected` チェック→監視キャンセル |
| スプレッド急拡大 | スコア0になるだけで監視継続。composite_thresholdで自然にブロック |
| デモモード | `demo_mode=True` 時はtick最適化無効（ランダムシグナルに無意味） |

## 変更影響の範囲

| ファイル | 変更種別 | 影響度 |
|---------|---------|--------|
| `adapters/mt5/connection.py` | ABCメソッド追加 + 実装 | 低（追加のみ） |
| `adapters/mt5/data_provider.py` | メソッド追加 | 低（追加のみ） |
| `live/tick_entry_config.py` | **新規** | - |
| `live/tick_entry_optimizer.py` | **新規** | - |
| `live/config.py` | フィールド追加 | 低 |
| `live/engine.py` | `_tick`・`_main_loop` 変更 | **中**（コアロジック） |

**M1シグナルパイプラインは一切変更なし**。`enabled=False` がデフォルトのため、明示的に有効化しない限り既存動作に影響なし。

## 検証方法

1. `pytest tests/unit/live/test_tick_entry_optimizer.py -v` で状態マシンテスト
2. `pytest tests/unit/ -v` で既存テスト350件が全てPASSすることを確認
3. ライブ環境で `TickEntryConfig(enabled=True)` を設定し、ログで以下を確認:
   - 「ティック監視開始」ログ出力
   - tick収集数、条件スコア、実行/タイムアウト判定
   - エントリー実行の正常動作
