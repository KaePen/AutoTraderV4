"""drop_market_memory_add_indexes

Revision ID: a2b3c4d5e6f7
Revises: fa1c4e2f6b5d
Create Date: 2026-03-01 20:00:00.000000

market_memory テーブルを削除し、trades テーブルにインデックスを追加する。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "fa1c4e2f6b5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """market_memory テーブル削除 + trades インデックス追加"""
    # market_memory テーブルのインデックスを削除
    op.drop_index(
        "ix_market_memory_symbol_type_valid",
        table_name="market_memory",
    )
    op.drop_index(
        op.f("ix_market_memory_valid_until"),
        table_name="market_memory",
    )
    op.drop_index(
        op.f("ix_market_memory_symbol"),
        table_name="market_memory",
    )
    op.drop_index(
        op.f("ix_market_memory_memory_id"),
        table_name="market_memory",
    )
    # テーブル削除
    op.drop_table("market_memory")

    # trades テーブルにインデックス追加
    op.create_index(
        "ix_trades_is_open_symbol",
        "trades",
        ["is_open", "symbol"],
    )
    op.create_index(
        "ix_trades_closed_at",
        "trades",
        ["closed_at"],
    )


def downgrade() -> None:
    """market_memory テーブル再作成 + trades インデックス削除"""
    # trades インデックス削除
    op.drop_index(
        "ix_trades_closed_at",
        table_name="trades",
    )
    op.drop_index(
        "ix_trades_is_open_symbol",
        table_name="trades",
    )

    # market_memory テーブル再作成
    op.create_table(
        "market_memory",
        sa.Column(
            "id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "memory_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "symbol",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "memory_type",
            sa.String(length=30),
            nullable=False,
        ),
        sa.Column(
            "direction_score",
            sa.Float(),
            nullable=False,
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
        ),
        sa.Column(
            "summary",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "source_event",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "valid_until",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "llm_reasoning",
            sa.Text(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_market_memory_memory_id"),
        "market_memory",
        ["memory_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_market_memory_symbol"),
        "market_memory",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        "ix_market_memory_symbol_type_valid",
        "market_memory",
        ["symbol", "memory_type", "valid_until"],
        unique=False,
    )
    op.create_index(
        op.f("ix_market_memory_valid_until"),
        "market_memory",
        ["valid_until"],
        unique=False,
    )
