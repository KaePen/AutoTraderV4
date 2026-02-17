"""データベース接続管理"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Generator

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session

from autotrader.adapters.database.models import Base


@lru_cache
def get_engine(database_url: str = "sqlite:///data/autotrader.db") -> Engine:
    """SQLAlchemyエンジンを取得

    Args:
        database_url: データベースURL

    Returns:
        Engine: SQLAlchemyエンジン
    """
    # SQLiteの場合はcheck_same_threadを無効化
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(
        database_url,
        connect_args=connect_args,
        echo=False,
        pool_pre_ping=True,
    )


def get_session_factory(engine: Engine) -> sessionmaker:
    """セッションファクトリを取得

    Args:
        engine: SQLAlchemyエンジン

    Returns:
        sessionmaker: セッションファクトリ
    """
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


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


def init_db(database_url: str = "sqlite:///data/autotrader.db") -> None:
    """データベースを初期化

    テーブルを作成する。

    Args:
        database_url: データベースURL
    """
    engine = get_engine(database_url)
    Base.metadata.create_all(bind=engine)
