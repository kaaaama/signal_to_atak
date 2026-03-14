"""add worker note and tak delivery cleanup index

Revision ID: 7c7e21c8b0d1
Revises: ebf96b8c4d6f
Create Date: 2026-03-14 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7c7e21c8b0d1"
down_revision: Union[str, Sequence[str], None] = "ebf96b8c4d6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "processed_messages",
        sa.Column("worker_note", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_tak_delivery_jobs_status_updated_at",
        "tak_delivery_jobs",
        ["status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tak_delivery_jobs_status_updated_at",
        table_name="tak_delivery_jobs",
    )
    op.drop_column("processed_messages", "worker_note")
