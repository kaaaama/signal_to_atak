"""Database models and lightweight message identity types."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Identity,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for SQLAlchemy declarative models."""

    pass


@dataclass(frozen=True)
class MessageKey:
    """Composite identifier for a Signal message tracked in storage."""

    source: str
    message_timestamp: int
    raw_text: str


class ProcessedMessage(Base):
    """Persisted processing record for a Signal message and its CoT lifecycle."""

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


class TakDeliveryJob(Base):
    """Persisted TAK delivery work item that can be claimed across replicas."""

    __tablename__ = "tak_delivery_jobs"

    __table_args__ = (
        CheckConstraint(
            "phase IN ('immediate', 'background-retry', 'replay')",
            name="ck_tak_delivery_jobs_phase",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'done', 'failed')",
            name="ck_tak_delivery_jobs_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        Identity(always=False),
        primary_key=True,
    )

    source: Mapped[str] = mapped_column(Text, nullable=False)
    message_timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    uid: Mapped[str] = mapped_column(Text, nullable=False)
    payload_xml: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

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
