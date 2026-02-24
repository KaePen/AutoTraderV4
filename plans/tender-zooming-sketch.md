# fix: エンジン起動時のDBゴーストレコード掃除と決済書き込みの堅牢化

## Context

MT5で既に決済済みのポジション（USDJPY BUY 3件）が Supabase DB で `is_open=true` のまま残存している。
原因: エンジン停止中にMT5側で決済（SL/TP/手動）が発生した場合、再起動時の `_sync_positions()` は
MT5に存在するポジションのみDBから復元し、MT5に存在しないゴーストレコードは無視する設計になっている。

### 根本原因

1. **起動時のゴースト掃除がない**: `_sync_positions()` がDBの `is_open=true` レコードをMT5と照合しない
2. **`_write_close_to_db()` で `pop()` が先行**: DB書き込み失敗時にtrade_idマッピングが消失し再試行不可
3. **リトライ機構なし**: DB書き込み失敗はログ出力のみ

## 変更内容

### 1. `_sync_positions()` にゴーストレコード掃除を追加

**ファイル**: `autotrader/live/engine.py` の `_sync_positions()` (L1855-1944)

MT5ポジション同期後、DBの `is_open=true` レコード全体を走査し、
MT5に存在しないticketのレコードを `is_open=false` に更新する。

```python
# _sync_positions() の末尾（_cleanup_stale_states の後）に追加
self._close_ghost_db_records(active_tickets)
```

新メソッド `_close_ghost_db_records(active_tickets: set[int])`:
- DBから当該シンボルの `is_open=true` レコードを全取得
- MT5の現在ticketセットと比較
- MT5に存在しないレコードの `is_open` を `False` に更新
- `exit_reason="GHOST_CLEANUP"`, `closed_at=now(UTC)` を設定

**ポジションなし時も掃除**: 現在の `_sync_positions()` は MT5にポジションがない場合 early return している。
ゴースト掃除はこの early return の前に実行する（MT5に0件 = DB上の全 `is_open=true` がゴースト）。

### 2. `_write_close_to_db()` の pop タイミング修正

**ファイル**: `autotrader/live/engine.py` L1455-1457

現在: `pop()` → DB書き込み（失敗するとtrade_id消失）
修正: `get()` → DB書き込み成功後に `pop()`

```python
# Before
trade_id = self._open_trades.pop(ticket, None)
if not trade_id:
    return
try:
    ...DB書き込み...
except Exception as e:
    logger.error(...)

# After
trade_id = self._open_trades.get(ticket)
if not trade_id:
    return
try:
    ...DB書き込み...
    self._open_trades.pop(ticket, None)  # 成功後にpop
except Exception as e:
    logger.error(...)
    # trade_idは_open_tradesに残るため次回tickで再試行される
```

### 3. 既存ゴーストデータの修正スクリプト

現在DBに残っている USDJPY BUY 3件のゴーストレコードを修正する一回限りのスクリプト。
MT5に存在しない `is_open=true` レコードを特定し `is_open=false` に更新する。

## 修正対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `autotrader/live/engine.py` | `_close_ghost_db_records()` 追加、`_sync_positions()` に組み込み、`_write_close_to_db()` の pop 修正 |
| `scripts/fix_ghost_positions.py` | 一回限りの修正スクリプト（実行後 `scripts/archive/` に移動） |

## テスト

既存テスト: `tests/unit/live/test_engine*.py` が影響範囲
- `_close_ghost_db_records()` のユニットテスト追加
- `_write_close_to_db()` のDB失敗時にtrade_idが保持されることを確認するテスト追加

## 検証手順

1. 修正スクリプトで現在のゴーストレコード3件が `is_open=false` に更新されることを確認
2. Supabase DB確認: `SELECT * FROM trades WHERE is_open = true` がMT5の実ポジションと一致
3. エンジン再起動時のログに `ゴーストレコード掃除` が出力されることを確認
