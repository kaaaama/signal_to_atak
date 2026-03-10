from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import ProcessedMessage


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

    async def mark_processed(
        self,
        *,
        source: str,
        message_timestamp: int,
        raw_text: str,
        is_valid: bool,
        response_text: str,
    ) -> None:
        stmt = (
            update(ProcessedMessage)
            .where(ProcessedMessage.source == source)
            .where(ProcessedMessage.message_timestamp == message_timestamp)
            .where(ProcessedMessage.raw_text == raw_text)
            .values(
                status="done",
                is_valid=is_valid,
                response_text=response_text,
                error_text=None,
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def mark_failed(
        self,
        *,
        source: str,
        message_timestamp: int,
        raw_text: str,
        error_text: str,
    ) -> None:
        stmt = (
            update(ProcessedMessage)
            .where(ProcessedMessage.source == source)
            .where(ProcessedMessage.message_timestamp == message_timestamp)
            .where(ProcessedMessage.raw_text == raw_text)
            .values(
                status="failed",
                error_text=error_text,
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()