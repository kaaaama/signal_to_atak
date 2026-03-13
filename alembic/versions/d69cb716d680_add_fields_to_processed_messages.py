"""add fields to processed messages

Revision ID: d69cb716d680
Revises: b056e8f04e4b
Create Date: 2026-03-13 17:30:14.856941

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd69cb716d680'
down_revision: Union[str, Sequence[str], None] = 'b056e8f04e4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("processed_messages", sa.Column("uid", sa.Text(), nullable=True))
    op.add_column("processed_messages", sa.Column("lon", sa.Numeric(18, 10), nullable=True))
    op.add_column("processed_messages", sa.Column("lat", sa.Numeric(18, 10), nullable=True))
    op.add_column("processed_messages", sa.Column("target", sa.Text(), nullable=True))

    op.add_column(
        "processed_messages",
        sa.Column("active_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "processed_messages",
        sa.Column("last_broadcast_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "processed_messages",
        sa.Column("next_replay_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "processed_messages",
        sa.Column(
            "replay_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "processed_messages",
        sa.Column("last_replay_error", sa.Text(), nullable=True),
    )

    op.create_index(
        "ix_processed_messages_uid",
        "processed_messages",
        ["uid"],
        unique=True,
        postgresql_where=sa.text("uid IS NOT NULL"),
    )

    op.create_index(
        "ix_processed_messages_next_replay_at",
        "processed_messages",
        ["next_replay_at"],
        unique=False,
    )

    op.create_index(
        "ix_processed_messages_active_until",
        "processed_messages",
        ["active_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_processed_messages_active_until", table_name="processed_messages")
    op.drop_index("ix_processed_messages_next_replay_at", table_name="processed_messages")
    op.drop_index("ix_processed_messages_uid", table_name="processed_messages")

    op.drop_column("processed_messages", "last_replay_error")
    op.drop_column("processed_messages", "replay_count")
    op.drop_column("processed_messages", "next_replay_at")
    op.drop_column("processed_messages", "last_broadcast_at")
    op.drop_column("processed_messages", "active_until")
    op.drop_column("processed_messages", "target")
    op.drop_column("processed_messages", "lat")
    op.drop_column("processed_messages", "lon")
    op.drop_column("processed_messages", "uid")
