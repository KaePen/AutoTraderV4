"""データベース接続管理"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session

from autotrader.adapters.database.models import Base

# dictベースキャッシュ（複数DB URL対応）
_engine_cache: dict[str, Engine] = {}


def get_engine(
    database_url: str = "sqlite:///data/autotrader.db",
) -> Engine:
    """SQLAlchemyエンジンを取得

    Args:
        database_url: データベースURL

    Returns:
        Engine: SQLAlchemyエンジン
    """
    if database_url not in _engine_cache:
        connect_args: dict = {}
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False

        _engine_cache[database_url] = create_engine(
            database_url,
            connect_args=connect_args,
            echo=False,
            pool_pre_ping=True,
        )
    return _engine_cache[database_url]


def get_session_factory(engine: Engine) -> sessionmaker:
    """セッションファクトリを取得

    Args:
        engine: SQLAlchemyエンジン

    Returns:
        sessionmaker: セッションファクトリ
    """
    return sessionmaker(
        bind=engine, autocommit=False, autoflush=False,
    )


@contextmanager
def get_session(
    database_url: str = "sqlite:///data/autotrader.db",
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


def init_db(
    database_url: str = "sqlite:///data/autotrader.db",
) -> None:
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
