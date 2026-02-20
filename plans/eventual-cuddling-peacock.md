# リファクタリング計画: ライブトレードエンジン整備

## Context

デモモード+自動トレードで以下の問題が発生：
1. 1回目のトレードが即座に決済される（PositionManager に誤った価格登録）
2. シグナル生成が止まる（PM エラーの例外伝播）
3. DBにトレードが記録されない（実装未了）
4. デモモードの実装が過剰（ランダムシグナル生成・全フィルタースキップ等）

ユーザー要件：
- デモモードは「活発にトレードが見える」テストモード
- 内部ロジックに深く根付かせたくない → 閾値変更のみ
- DB記録はエントリー時＋決済時の両方

---

## 修正方針

### Phase 1: デモモードの正規化

**問題**: `_generate_demo_signal()` がランダムBUY/SELLを生成・発注。
フィルター全スキップ・複数ポジション・ランダム等が内部ロジックに浸透。

**修正内容** (`trade_bot.py`):
1. `_generate_demo_signal()` メソッドを削除
2. `_generate_signal_new()` の `if not self.config.demo_mode:` ブロックを削除
   → フィルターを常に全シンボルに等しく適用
3. `demo_mode` の効果を**コンセンサス閾値の変更のみ**に限定する
   - デモ時: `scalping_threshold=1.0, day_trade_threshold=1.5, swing_threshold=1.5`（大幅低下で活発にシグナル発火）
   - これにより内部ロジックを変えずに「活発なトレード」を実現

**修正内容** (`config.py`):
4. `demo_max_positions: int = 3` → 1のまま（複数ポジションは本番と同じ）
5. `demo_consensus_scalping_threshold`, `demo_consensus_swing_threshold` の値を調整

**修正内容** (`engine.py`):
6. `_execute_entry()` の `max_pos` 計算をシンプル化（デモも本番も同じ=1）

---

### Phase 2: DBへのトレード記録

**問題**: MT5発注成功後にDB書き込みがない。

**対象ファイル**:
- `src/autotrader/live/engine.py`
- `src/autotrader/adapters/database/repositories.py`
- `src/autotrader/adapters/database/models.py`

**修正内容**:

1. `engine.py` にDBセッション依存性を追加（`get_db()` を使用）
   - `__init__` にオプション引数 `db_session_factory` を追加、またはグローバル設定から取得

2. エンジン内に `_open_trades: dict[int, str]` (ticket→trade_id) マッピングを保持

3. `_execute_entry()` 成功時にDB書き込み:
```python
trade_id = str(uuid.uuid4())
self._open_trades[result.ticket] = trade_id
# DB書き込み（非同期、失敗してもトレードは継続）
try:
    async with get_db_context() as db:
        repo = TradeRepository(db)
        await repo.create(TradeRecord(
            trade_id=trade_id,
            symbol=signal.symbol,
            signal_type=signal.signal_type.value,
            volume=lot,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=tp_price,
            opened_at=datetime.now(timezone.utc),
            is_open=True,
        ))
except Exception as e:
    logger.error("DB書き込みエラー（エントリー）: %s", e)
```

4. `_execute_action(FULL_CLOSE)` 成功時にDB更新:
```python
trade_id = self._open_trades.pop(position.ticket, None)
if trade_id:
    try:
        async with get_db_context() as db:
            repo = TradeRepository(db)
            await repo.close_trade(
                trade_id=trade_id,
                exit_price=current_price,
                profit_loss=pnl,
                profit_loss_pips=pnl_pips,
                exit_reason=action.reason,
                closed_at=datetime.now(timezone.utc),
            )
    except Exception as e:
        logger.error("DB書き込みエラー（決済）: %s", e)
```

5. `repositories.py` に `close_trade()` メソッドを追加

6. `trade_history` プロパティを実装:
```python
@property
def trade_history(self) -> list[dict]:
    # インメモリの閉じたトレード履歴を返す
    return self._closed_trades  # list[dict] として保持
```

---

### Phase 3: 軽微なクリーンアップ

1. `_manage_positions()` の for ループ内に try/except を追加（シグナル生成保護）
   → **PR #14 で既に実施済み（mainにマージ済み）**

2. `_register_new_position()` の entry_price バグ修正
   → **PR #14 で既に実施済み（mainにマージ済み）**

---

## 対象ファイル

| ファイル | 変更内容 |
|---------|---------|
| `src/autotrader/decision/unified/trade_bot.py` | `_generate_demo_signal()` 削除、フィルターブロック削除 |
| `src/autotrader/decision/unified/config.py` | デモ閾値調整 |
| `src/autotrader/live/engine.py` | DB書き込み追加、`_open_trades` マッピング、`trade_history` 実装 |
| `src/autotrader/adapters/database/repositories.py` | `close_trade()` 追加 |

---

## 検証方法

1. `pytest tests/unit/ -q` → 398件以上 PASS
2. デモモード+自動トレードONでサーバー起動 → シグナルが継続的に生成されることを確認
3. トレード実行後 `GET /api/v1/trades` → DBにエントリーレコードが存在することを確認
4. ポジション決済後 → DB の `is_open=False`, `exit_price` が更新されることを確認
