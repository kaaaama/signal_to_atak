from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=func.now(),
    )