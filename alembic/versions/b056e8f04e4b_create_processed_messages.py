"""create processed messages

Revision ID: b056e8f04e4b
Revises: 
Create Date: 2026-03-10 18:35:18.007934

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b056e8f04e4b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_messages",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("message_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("is_valid", sa.Boolean(), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'done', 'failed')",
            name="ck_processed_messages_status",
        ),
        sa.PrimaryKeyConstraint(
            "source",
            "message_timestamp",
            "raw_text",
            name="pk_processed_messages",
        ),
    )

    op.create_index(
        "ix_processed_messages_created_at",
        "processed_messages",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_processed_messages_created_at", table_name="processed_messages")
    op.drop_table("processed_messages")