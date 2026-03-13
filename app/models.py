from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Integer, Numeric, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


@dataclass(frozen=True)
class MessageKey:
    source: str
    message_timestamp: int
    raw_text: str


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'done', 'failed')",
            name="ck_processed_messages_status",
        ),
    )

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    message_timestamp: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text, primary_key=True)

    status: Mapped[str] = mapped_column(Text, nullable=False)
    is_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    lon: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)

    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_broadcast_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_replay_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replay_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_replay_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )