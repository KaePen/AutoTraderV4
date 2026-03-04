# DB層改善 — エンジン統一・セッション管理・インデックス追加

## Context

PR #362（Supabase統合）マージ後の状態で、DB層に8件の改善点が残っている。
Web層とDB層のエンジン二重化、commit/rollbackパターンの不統一、
async内での同期DBアクセス、インデックス不足など、
本番運用の安定性に直結する問題を一括修正する。

## 変更一覧

### 1. Web層エンジンを `connection.py` に統一 [重大]

**`autotrader/web/dependencies.py`** (行 16-53)

独自の `get_engine()` / `get_session_factory()` を削除し、
`connection.py` の実装に委譲する。

- `@lru_cache` 付き独自 `get_engine()` (行 16-29) → 削除
- `@lru_cache` 付き独自 `get_session_factory()` (行 32-39) → 削除
- `get_db()` (行 42-53) → `connection.py` の `get_session_factory()` を使用
- Web側で `pool_pre_ping=True` が効いていなかった問題も解消

### 2. `get_db()` に commit/rollback 追加 [重大]

**`autotrader/web/dependencies.py`** `get_db()` (行 42-53)

現状 `finally: db.close()` のみ。FastAPI Depends パターンに沿いつつ
auto-commit/rollback を追加。

```python
def get_db() -> Generator[Session, None, None]:
    settings = get_settings()
    factory = get_session_factory(
        get_engine(settings.database_url)
    )
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

### 3. `connection.py` に dispose 関数追加 [中]

**`autotrader/adapters/database/connection.py`**

```python
def dispose_engine(database_url: str) -> None:
    """エンジン接続プールを解放"""
    engine = _engine_cache.pop(database_url, None)
    if engine is not None:
        engine.dispose()

def dispose_all_engines() -> None:
    """全エンジンの接続プールを解放"""
    for engine in _engine_cache.values():
        engine.dispose()
    _engine_cache.clear()
```

### 4. `get_session_factory()` にキャッシュ追加 [中]

**`autotrader/adapters/database/connection.py`** (行 49-60)

現状は呼び出しのたびに新しい `sessionmaker` を生成。
dict ベースキャッシュを追加。

```python
_factory_cache: dict[int, sessionmaker] = {}

def get_session_factory(engine: Engine) -> sessionmaker:
    engine_id = id(engine)
    if engine_id not in _factory_cache:
        _factory_cache[engine_id] = sessionmaker(
            bind=engine, autocommit=False, autoflush=False,
        )
    return _factory_cache[engine_id]
```

`dispose_all_engines()` で `_factory_cache` もクリア。

### 5. `== True` → `.is_(True)` 統一 [中]

**`autotrader/adapters/database/repositories.py`** 行 121

```python
# Before
TradeRecord.is_open == True  # noqa: E712

# After
TradeRecord.is_open.is_(True)
```

### 6. `_write_memory()` ダブルcommit解消 [中]

**`autotrader/adapters/fundamental/memory.py`** 行 575

`session.commit()` の明示呼び出しを削除。
`get_session()` コンテキストマネージャの auto-commit に委ねる。

### 7. TradeRecord にインデックス追加 [中]

**`autotrader/adapters/database/models.py`** 行 58-60

```python
__table_args__ = (
    Index("ix_trades_symbol_opened", "symbol", "opened_at"),
    Index("ix_trades_is_open_symbol", "is_open", "symbol"),
    Index("ix_trades_closed_at", "closed_at"),
)
```

### 8. `_close_ghost_db_records` async/sync分離 [中]

**`autotrader/live/engine.py`** 行 2050-2149

同期DB操作をヘルパー関数に分離し、`asyncio.to_thread()` で
イベントループのブロッキングを回避。

ループ内で `await` と同期DBが交互するため:
- 同期DB読み取り（ゴースト取得）→ `to_thread` で実行
- 各ゴーストの await MT5 → そのまま
- 同期DB更新（個別レコード更新）→ `to_thread` で実行

## 変更対象ファイル

| ファイル | 変更概要 |
|---------|---------|
| `autotrader/web/dependencies.py` | 独自エンジン削除、connection.py に統一、commit/rollback追加 |
| `autotrader/adapters/database/connection.py` | dispose関数追加、factory キャッシュ |
| `autotrader/adapters/database/repositories.py` | `.is_(True)` 統一 |
| `autotrader/adapters/database/models.py` | インデックス2件追加 |
| `autotrader/adapters/fundamental/memory.py` | ダブルcommit解消 |
| `autotrader/live/engine.py` | `_close_ghost_db_records` async/sync分離 |
| Alembicマイグレーション | 新インデックス用revision追加 |

## 検証

1. `ruff check` 対象ファイル全パス
2. `pytest tests/unit/adapters/database/ -v` — DB関連テスト全パス
3. `pytest tests/unit/live/ -x -q` — engine テスト通過
4. `pytest tests/unit/adapters/fundamental/test_memory.py -v` — メモリテスト通過
