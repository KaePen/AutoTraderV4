"""Alembic マイグレーション環境設定

DATABASE_URL 環境変数から接続先を決定し、
Base.metadata（trades, market_memory）を対象にマイグレーションを実行する。
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from autotrader.adapters.database.models import Base

# Alembic Config オブジェクト
config = context.config

# ロギング設定
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DATABASE_URL 環境変数で sqlalchemy.url を上書き
database_url = os.environ.get("DATABASE_URL", "")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# autogenerate 対象メタデータ（Base のみ、LocalBase は除外）
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """オフラインモードでマイグレーション実行

    エンジンを作成せず、SQL文を出力する。
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """オンラインモードでマイグレーション実行

    エンジンを作成して接続し、マイグレーションを適用する。
    """
    connectable = engine_from_config(
        config.get_section(
            config.config_ini_section, {}
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
