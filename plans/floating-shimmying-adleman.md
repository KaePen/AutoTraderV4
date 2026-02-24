# ポジション管理状態の永続化（デュアルDB構成）

## Context

PositionManagerの内部状態（SL追跡値、部分利確フラグ、トレーリング状態等）はメモリのみで管理されており、プログラム再起動時に全て失われる。これにより：
- 引き上げたSLが`original_sl`にリセット（利益保護の喪失）
- 部分利確フラグ消失で同じR値到達時に二重決済
- TP無効化フラグ消失でRunner運用のロットがTP全決済
- トレーリング基準値リセットでストップ位置が不適切に

**目標**: 再起動後もポジション管理の全状態を復元し、管理戦略を中断なく継続する。

## DB構成

| 用途 | DB | 理由 |
|------|-----|------|
| トレード履歴（TradeRecord等） | **Supabase (PostgreSQL)** 既存 | 低頻度I/O、永続性・耐障害性重視 |
| ポジション管理状態（新規） | **ローカル SQLite** 新規 | 毎秒更新の高頻度I/O、コストゼロ |

## 設計方針

- **デュアルDB**: Supabase（トレード履歴）+ SQLite（ポジション管理状態）を併用
- **MT5が真の情報源**: `current_sl`, `remaining_volume` はMT5から取得（重複保存しない）
- **SQLite永続化はフラグと追跡値のみ**: MT5が保持しない管理状態を保存
- **レイヤー分離**: PositionManager（decision/層）はDB非依存。`export_state()`/`import_state()` でdict受け渡し
- **バックテスト影響なし**: 新機能はlive/層のみで呼び出し

## 変更ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `autotrader/config/settings.py` | `local_database_url` 設定追加 |
| `autotrader/adapters/database/connection.py` | `get_engine()` を dict ベースキャッシュに修正 + `get_local_session()` / `init_local_db()` 追加 |
| `autotrader/adapters/database/models.py` | `LocalBase` + `PositionStateRecord` モデル追加 |
| `autotrader/adapters/database/repositories.py` | `PositionStateRepository` クラス追加 |
| `autotrader/decision/unified/position_manager.py` | `export_state()`, `import_state()` 追加 + `unregister_position()` バグ修正 |
| `autotrader/live/engine.py` | `_sync_positions()` 改善 + 永続化メソッド4つ + 呼び出し統合 |
| `autotrader/web/main.py` | 起動時に `init_local_db()` 呼び出し追加 |
| `tests/unit/decision/test_position_state_persistence.py` | export/import ユニットテスト |
| `tests/unit/adapters/database/test_position_state_repo.py` | Repository CRUDテスト |

---

## Phase 1: DB基盤の整備

### 1-1. `autotrader/config/settings.py` (L156付近)

```python
# 既存
database_url: str = "sqlite:///data/autotrader.db"  # Supabaseは.envで上書き

# 追加
local_database_url: str = "sqlite:///data/local_state.db"  # ローカル専用
```

### 1-2. `autotrader/adapters/database/connection.py`

**問題**: 現在の `get_engine()` は `@lru_cache` で引数無視のキャッシュ。複数URLで同じエンジンが返る。

**修正**: dict ベースキャッシュに変更。

```python
# 修正: @lru_cache → dict キャッシュ
_engine_cache: dict[str, Engine] = {}

def get_engine(database_url: str = "sqlite:///data/autotrader.db") -> Engine:
    if database_url not in _engine_cache:
        connect_args = {}
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine_cache[database_url] = create_engine(
            database_url, connect_args=connect_args,
            echo=False, pool_pre_ping=True,
        )
    return _engine_cache[database_url]
```

**追加**:
```python
@contextmanager
def get_local_session():
    """ローカルSQLiteセッション（ポジション管理状態用）"""
    from autotrader.config.settings import get_settings
    url = get_settings().local_database_url
    engine = get_engine(url)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def init_local_db() -> None:
    """ローカルDBのテーブル初期化"""
    from autotrader.config.settings import get_settings
    url = get_settings().local_database_url
    engine = get_engine(url)
    LocalBase.metadata.create_all(bind=engine)
```

### 1-3. `autotrader/adapters/database/models.py`

`LocalBase` を新設し、ポジション管理状態テーブルを分離。Supabaseのスキーマを汚さない。

```python
LocalBase = declarative_base()

class PositionStateRecord(LocalBase):
    __tablename__ = "position_management_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(String(36), unique=True, nullable=False, index=True)

    # 追跡値
    highest_price = Column(Float, nullable=False, default=0.0)
    lowest_price = Column(Float, nullable=False, default=0.0)
    highest_r = Column(Float, nullable=False, default=0.0)
    bars_held = Column(Integer, nullable=False, default=0)
    trailing_activated = Column(Boolean, nullable=False, default=False)

    # 管理フラグ（7つ）
    partial_closed_1r = Column(Boolean, nullable=False, default=False)
    partial_closed_2r = Column(Boolean, nullable=False, default=False)
    tp_disabled = Column(Boolean, nullable=False, default=False)
    early_be_applied = Column(Boolean, nullable=False, default=False)
    insurance_sl_applied = Column(Boolean, nullable=False, default=False)
    insurance_partial_applied = Column(Boolean, nullable=False, default=False)
    half_r_partial_applied = Column(Boolean, nullable=False, default=False)

    updated_at = Column(DateTime(timezone=True), default=..., onupdate=...)
```

### 1-4. `autotrader/adapters/database/repositories.py`

```python
class PositionStateRepository:
    def __init__(self, session: Session) -> None: ...
    def upsert(self, state: dict) -> None        # INSERT or UPDATE
    def get_by_position_id(self, position_id: str) -> PositionStateRecord | None
    def get_all_open(self) -> list[PositionStateRecord]
    def delete(self, position_id: str) -> None    # クローズ時
```

### 1-5. `autotrader/web/main.py` (起動時)

`init_db()` 呼び出しの隣に `init_local_db()` 追加。

---

## Phase 2: PositionManager拡張

### 2-1. `export_state()` / `import_state()` 追加

`autotrader/decision/unified/position_manager.py`

- `export_state(position_id)` → dict: ManagedPositionの追跡値 + 全7フラグ set判定結果をdict出力
- `import_state(position_id, state)` → None: 既存ManagedPositionの追跡値を上書き + フラグsetに追加
- DB非依存（pure Python dict操作のみ）

### 2-2. `unregister_position()` バグ修正

行304-315: `_insurance_sl_applied.discard()` と `_insurance_partial_applied.discard()` が欠落。追加する。

---

## Phase 3: engine.py 永続化統合

### 3-1. 新メソッド追加

全て `get_local_session()` を使用（Supabaseセッションとは独立）。

| メソッド | 役割 |
|---------|------|
| `_load_position_states()` | ローカルDB全管理状態を `dict[position_id, state_dict]` で取得 |
| `_save_position_state(position_id)` | PM.export_state → ローカルDB upsert |
| `_delete_position_state(position_id)` | ローカルDB delete（クローズ時） |
| `_cleanup_stale_states(active_ids)` | MT5に存在しないローカルDB状態を削除 |

### 3-2. `_sync_positions()` 改善（行1675-1732）

既存フローの末尾に追加：
1. `_load_position_states()` でローカルDB状態を一括取得
2. 各ポジションの `managed.current_sl = pos.stop_loss`（MT5最新値で補正）
3. 各ポジションの `managed.remaining_volume = pos.volume`
4. `self._pm.import_state(pos_id, saved_states[pos_id])` でフラグ復元
5. `_cleanup_stale_states()` で陳腐化レコード削除

### 3-3. 永続化呼び出し箇所

| 呼び出し箇所 | 操作 | タイミング |
|-------------|------|-----------|
| `_manage_positions()` ループ末尾 | `_save_position_state()` | 毎tick・各ポジション評価後（HOLD含む） |
| `_execute_action()` FULL_CLOSE分岐内 | `_delete_position_state()` | unregister前 |
| `_handle_external_close()` 末尾 | `_delete_position_state()` | 外部決済検出時 |
| `_register_new_position()` 末尾 | `_save_position_state()` | 新規登録時 |

**毎tick保存の妥当性**: ポジション数は通常1-3個。ローカルSQLite UPSERTは <1ms。`highest_price`/`bars_held`等の追跡値が毎tick変化するため、アクション時のみの保存では不十分。Supabaseでは高コストだが、SQLiteならコストゼロ。

---

## Phase 4: テスト

### 4-1. PositionManager export/import テスト
- register → evaluate（1R到達で部分利確発生）→ export → 新PMで register + import → evaluate が同じ結果を返す
- 全7フラグの round-trip 検証

### 4-2. PositionStateRepository CRUDテスト
- upsert (insert) → get で一致確認
- upsert (update) → フラグ変更反映確認
- delete → get が None 確認

---

## 検証方法

1. テスト実行: `python -m pytest tests/unit/decision/test_position_state_persistence.py tests/unit/adapters/database/test_position_state_repo.py -v`
2. DB確認: `sqlite3 data/local_state.db ".schema position_management_state"` でテーブル存在確認
3. 動作確認: デモモードでポジション保有中にエンジン再起動 → ログで「管理状態復元: ticket=XXX」確認
