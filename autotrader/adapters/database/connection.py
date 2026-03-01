"""データベース接続管理"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from autotrader.adapters.database.models import Base

# dictベースキャッシュ（複数DB URL対応）
_engine_cache: dict[str, Engine] = {}
# セッションファクトリキャッシュ（エンジンID → ファクトリ）
_factory_cache: dict[int, sessionmaker] = {}


def get_engine(database_url: str) -> Engine:
    """SQLAlchemyエンジンを取得

    Args:
        database_url: データベースURL

    Returns:
        Engine: SQLAlchemyエンジン
    """
    if database_url not in _engine_cache:
        connect_args: dict = {}
        kwargs: dict = {
            "echo": False,
            "pool_pre_ping": True,
        }

        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        else:
            # PostgreSQL: 接続プール設定
            kwargs["pool_size"] = 5
            kwargs["max_overflow"] = 10
            kwargs["pool_recycle"] = 1800

        _engine_cache[database_url] = create_engine(
            database_url,
            connect_args=connect_args,
            **kwargs,
        )
    return _engine_cache[database_url]


def get_session_factory(engine: Engine) -> sessionmaker:
    """セッションファクトリを取得（キャッシュ付き）

    同一エンジンに対して毎回 sessionmaker を再生成せず、
    キャッシュ済みファクトリを返す。

    Args:
        engine: SQLAlchemyエンジン

    Returns:
        sessionmaker: セッションファクトリ
    """
    key = id(engine)
    if key not in _factory_cache:
        _factory_cache[key] = sessionmaker(
            bind=engine,
            autocommit=False,
            autoflush=False,
        )
    return _factory_cache[key]


@contextmanager
def get_session(
    database_url: str,
) -> Generator[Session, None, None]:
    """セッションコンテキストマネージャ

    Args:
        database_url: データベースURL

    Yields:
        Session: SQLAlchemyセッション
    """
    engine = get_engine(database_url)
    session_factory = get_session_factory(engine)
    session = session_factory()

    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_local_session() -> Generator[Session, None, None]:
    """ローカルSQLiteセッション（ポジション管理状態用）

    Yields:
        Session: SQLAlchemyセッション
    """
    from autotrader.config.settings import get_settings

    url = get_settings().local_database_url
    engine = get_engine(url)
    factory = get_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(database_url: str) -> None:
    """データベースを初期化

    テーブルを作成する。

    Args:
        database_url: データベースURL
    """
    engine = get_engine(database_url)
    Base.metadata.create_all(bind=engine)


def init_local_db() -> None:
    """ローカルDB（ポジション管理状態）のテーブル初期化"""
    from autotrader.adapters.database.models import LocalBase
    from autotrader.config.settings import get_settings

    url = get_settings().local_database_url
    engine = get_engine(url)
    LocalBase.metadata.create_all(bind=engine)


def dispose_engine(database_url: str) -> None:
    """指定URLのエンジンを破棄しキャッシュから削除

    Args:
        database_url: 破棄するデータベースURL
    """
    engine = _engine_cache.pop(database_url, None)
    if engine is not None:
        # 対応するファクトリキャッシュも削除
        _factory_cache.pop(id(engine), None)
        engine.dispose()


def dispose_all_engines() -> None:
    """全エンジンを破棄しキャッシュをクリア"""
    for engine in _engine_cache.values():
        engine.dispose()
    _engine_cache.clear()
    _factory_cache.clear()
