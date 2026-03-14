"""add worker note and tak delivery cleanup index

Revision ID: 7c7e21c8b0d1
Revises: d69cb716d680
Create Date: 2026-03-14 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7c7e21c8b0d1"
down_revision: Union[str, Sequence[str], None] = "d69cb716d680"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "processed_messages",
        sa.Column("worker_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("processed_messages", "worker_note")
