from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import MessageKey, ProcessedMessage


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresStore:
    def __init__(
        self,
        database_url: str,
        pool_size: int = 10,
        max_overflow: int = 20,
    ) -> None:
        self.engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def close(self) -> None:
        await self.engine.dispose()

    async def try_claim_message(
        self,
        *,
        source: str,
        message_timestamp: int,
        raw_text: str,
    ) -> bool:
        stmt = (
            insert(ProcessedMessage)
            .values(
                source=source,
                message_timestamp=message_timestamp,
                raw_text=raw_text,
                status="processing",
                is_valid=None,
                response_text=None,
                error_text=None,
            )
            .on_conflict_do_nothing(
                index_elements=["source", "message_timestamp", "raw_text"]
            )
        )

        async with self.session_factory() as session:
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount == 1

    async def mark_done(
        self,
        *,
        key: MessageKey,
        is_valid: bool,
        response_text: str,
    ) -> None:
        stmt = (
            update(ProcessedMessage)
            .where(ProcessedMessage.source == key.source)
            .where(ProcessedMessage.message_timestamp == key.message_timestamp)
            .where(ProcessedMessage.raw_text == key.raw_text)
            .values(
                status="done",
                is_valid=is_valid,
                response_text=response_text,
                error_text=None,
                updated_at=func.now(),
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def mark_failed(
        self,
        *,
        key: MessageKey,
        error_text: str,
        response_text: str | None = None,
        is_valid: bool | None = None,
    ) -> None:
        values: dict[str, object] = {
            "status": "failed",
            "error_text": error_text,
            "updated_at": func.now(),
        }

        if response_text is not None:
            values["response_text"] = response_text
        if is_valid is not None:
            values["is_valid"] = is_valid

        stmt = (
            update(ProcessedMessage)
            .where(ProcessedMessage.source == key.source)
            .where(ProcessedMessage.message_timestamp == key.message_timestamp)
            .where(ProcessedMessage.raw_text == key.raw_text)
            .values(**values)
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def claim_retry_batch(
        self,
        *,
        limit: int,
        failed_before: datetime,
        processing_before: datetime,
    ) -> list[MessageKey]:
        async with self.session_factory() as session:
            async with session.begin():
                stmt = (
                    select(ProcessedMessage)
                    .where(
                        or_(
                            and_(
                                ProcessedMessage.status == "failed",
                                ProcessedMessage.is_valid.is_(True),
                                ProcessedMessage.updated_at <= failed_before,
                            ),
                            and_(
                                ProcessedMessage.status == "processing",
                                ProcessedMessage.updated_at <= processing_before,
                            ),
                        )
                    )
                    .order_by(
                        ProcessedMessage.updated_at.asc(),
                        ProcessedMessage.created_at.asc(),
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )

                rows = (await session.execute(stmt)).scalars().all()
                claimed: list[MessageKey] = []

                for row in rows:
                    row.status = "processing"
                    row.response_text = "Claimed by background retry worker"
                    row.error_text = None
                    row.updated_at = utc_now()

                    claimed.append(
                        MessageKey(
                            source=row.source,
                            message_timestamp=row.message_timestamp,
                            raw_text=row.raw_text,
                        )
                    )

                return claimed