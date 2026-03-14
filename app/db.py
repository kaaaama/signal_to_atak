
"""Async PostgreSQL persistence for processed Signal messages."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import MessageKey, ProcessedMessage, TakDeliveryJob
from app.validation import ParsedPayload


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp.

    The store uses this helper anywhere Python-side timestamps are needed so
    retry and replay scheduling remain explicitly UTC-based.
    """
    return datetime.now(timezone.utc)


class PostgresStore:
    """Persist message processing state and replay scheduling metadata."""

    def __init__(
        self,
        database_url: str,
        pool_size: int = 10,
        max_overflow: int = 20,
    ) -> None:
        """Create the async engine and session factory.

        The engine is configured for long-running service usage with connection
        health checks and a bounded pool sized from application settings.
        """
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
        """Dispose the SQLAlchemy engine and close pooled connections.

        This is the shutdown hook for releasing open database resources.
        """
        await self.engine.dispose()

    async def try_claim_message(
        self,
        *,
        source: str,
        message_timestamp: int,
        raw_text: str,
    ) -> bool:
        """Insert a new processing record if the message has not been seen before.

        The insert uses PostgreSQL ``ON CONFLICT DO NOTHING`` against the
        composite message key. A return value of ``True`` means this worker won
        the claim and should continue processing; ``False`` means another worker
        or an earlier run already recorded the same message.
        """
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
        """Mark a message as fully handled and store the reply sent to Signal.

        This path is used for both successful processing and terminal
        validation failures. The row is moved to ``done`` and any previous
        error text is cleared because no further retry work is needed.
        """
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
        """Mark processing as failed while preserving the latest failure context.

        Callers can optionally update the stored Signal reply and validation
        state at the same time, which lets delivery failures keep the parsed
        payload while attaching the most recent TAK error message.
        """
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
        """Claim stale processing rows and eligible failed rows for retry.

        The query selects rows that are either:
        1. previously failed after successful validation and old enough to retry
        2. stuck in ``processing`` long enough to be treated as abandoned

        ``FOR UPDATE SKIP LOCKED`` ensures multiple workers can run this loop
        without double-claiming the same messages.
        """
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

    async def store_parsed_payload(
        self,
        *,
        key: MessageKey,
        uid: str,
        payload: ParsedPayload,
        active_until: datetime,
    ) -> None:
        """Persist parsed payload data and its active replay lifetime.

        This stores the normalized coordinates, target text, generated UID, and
        the timestamp after which rebroadcasting should stop.
        """
        stmt = (
            update(ProcessedMessage)
            .where(ProcessedMessage.source == key.source)
            .where(ProcessedMessage.message_timestamp == key.message_timestamp)
            .where(ProcessedMessage.raw_text == key.raw_text)
            .values(
                uid=uid,
                lon=payload.lon,
                lat=payload.lat,
                target=payload.target,
                active_until=active_until,
                updated_at=func.now(),
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def mark_delivered_and_schedule_replay(
        self,
        *,
        key: MessageKey,
        response_text: str,
        when: datetime,
        replay_interval_sec: float,
    ) -> None:
        """Mark a CoT event as delivered and schedule its next replay.

        The method records the most recent broadcast time, advances the replay
        counter, clears replay errors, and schedules the next rebroadcast in a
        single update after immediate or retried delivery succeeds.
        """
        stmt = (
            update(ProcessedMessage)
            .where(ProcessedMessage.source == key.source)
            .where(ProcessedMessage.message_timestamp == key.message_timestamp)
            .where(ProcessedMessage.raw_text == key.raw_text)
            .values(
                status="done",
                is_valid=True,
                response_text=response_text,
                error_text=None,
                last_broadcast_at=when,
                next_replay_at=when + timedelta(seconds=replay_interval_sec),
                replay_count=ProcessedMessage.replay_count + 1,
                last_replay_error=None,
                updated_at=func.now(),
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def mark_replay_scheduled(
        self,
        *,
        key: MessageKey,
        when: datetime,
        replay_interval_sec: float,
    ) -> None:
        """Advance replay bookkeeping after a successful rebroadcast.

        Unlike the initial delivery path, this keeps the row in ``done`` state
        and only updates replay-related timing and counters.
        """
        stmt = (
            update(ProcessedMessage)
            .where(ProcessedMessage.source == key.source)
            .where(ProcessedMessage.message_timestamp == key.message_timestamp)
            .where(ProcessedMessage.raw_text == key.raw_text)
            .values(
                last_broadcast_at=when,
                next_replay_at=when + timedelta(seconds=replay_interval_sec),
                replay_count=ProcessedMessage.replay_count + 1,
                last_replay_error=None,
                updated_at=func.now(),
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def mark_replay_failed(
        self,
        *,
        key: MessageKey,
        error_text: str,
        retry_after_sec: float,
    ) -> None:
        """Record a replay failure and defer the next replay attempt.

        Replay failures do not invalidate the original message. Instead the
        method stores the latest replay error and pushes ``next_replay_at``
        forward so the background loop can try again later.
        """
        stmt = (
            update(ProcessedMessage)
            .where(ProcessedMessage.source == key.source)
            .where(ProcessedMessage.message_timestamp == key.message_timestamp)
            .where(ProcessedMessage.raw_text == key.raw_text)
            .values(
                last_replay_error=error_text,
                next_replay_at=utc_now() + timedelta(seconds=retry_after_sec),
                updated_at=func.now(),
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def clear_expired_replays(self, *, now: datetime) -> None:
        """Stop replaying valid events whose active lifetime has ended.

        Expiration is handled by nulling ``next_replay_at`` once
        ``active_until`` has passed. This leaves the historical row intact while
        making it invisible to the replay claim query.
        """
        stmt = (
            update(ProcessedMessage)
            .where(ProcessedMessage.is_valid.is_(True))
            .where(ProcessedMessage.active_until.is_not(None))
            .where(ProcessedMessage.active_until <= now)
            .values(
                next_replay_at=None,
                updated_at=func.now(),
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def claim_replay_batch(
        self,
        *,
        limit: int,
        now: datetime,
        claim_lease_sec: float,
    ) -> list[MessageKey]:
        """Claim active events whose replay deadline has arrived.

        Rows are temporarily leased by moving ``next_replay_at`` into the
        future before the worker starts I/O. That lease prevents another worker
        from picking up the same replay if processing is slow.
        """
        claim_until = now + timedelta(seconds=claim_lease_sec)

        async with self.session_factory() as session:
            async with session.begin():
                stmt = (
                    select(ProcessedMessage)
                    .where(ProcessedMessage.status == "done")
                    .where(ProcessedMessage.is_valid.is_(True))
                    .where(ProcessedMessage.uid.is_not(None))
                    .where(ProcessedMessage.active_until.is_not(None))
                    .where(ProcessedMessage.active_until > now)
                    .where(ProcessedMessage.next_replay_at.is_not(None))
                    .where(ProcessedMessage.next_replay_at <= now)
                    .order_by(ProcessedMessage.next_replay_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )

                rows = (await session.execute(stmt)).scalars().all()
                claimed: list[MessageKey] = []

                for row in rows:
                    row.next_replay_at = claim_until
                    row.updated_at = now
                    claimed.append(
                        MessageKey(
                            source=row.source,
                            message_timestamp=row.message_timestamp,
                            raw_text=row.raw_text,
                        )
                    )

                return claimed

    async def get_processed_message(
        self,
        *,
        key: MessageKey,
    ) -> ProcessedMessage | None:
        """Fetch a processed message row by its composite key.

        Replay and retry code uses this to reconstruct the payload or inspect
        delivery state after a row has already been claimed.
        """
        stmt = (
            select(ProcessedMessage)
            .where(ProcessedMessage.source == key.source)
            .where(ProcessedMessage.message_timestamp == key.message_timestamp)
            .where(ProcessedMessage.raw_text == key.raw_text)
        )

        async with self.session_factory() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def enqueue_delivery_job(
        self,
        *,
        key: MessageKey,
        uid: str,
        payload_xml: str,
        phase: str,
        priority: int,
        available_at: datetime,
    ) -> int:
        """Persist a TAK delivery job that can be claimed by any worker."""
        async with self.session_factory() as session:
            job = TakDeliveryJob(
                source=key.source,
                message_timestamp=key.message_timestamp,
                raw_text=key.raw_text,
                uid=uid,
                payload_xml=payload_xml,
                phase=phase,
                priority=priority,
                status="pending",
                available_at=available_at,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
            await session.commit()
            return job_id

    async def claim_delivery_batch(
        self,
        *,
        limit: int,
        now: datetime,
        claim_lease_sec: float,
        instance_id: str,
    ) -> list[TakDeliveryJob]:
        """Claim pending or expired TAK delivery jobs for one worker."""
        claim_expires_at = now + timedelta(seconds=claim_lease_sec)

        async with self.session_factory() as session:
            async with session.begin():
                stmt = (
                    select(TakDeliveryJob)
                    .where(
                        or_(
                            and_(
                                TakDeliveryJob.status == "pending",
                                TakDeliveryJob.available_at <= now,
                            ),
                            and_(
                                TakDeliveryJob.status == "claimed",
                                TakDeliveryJob.claim_expires_at.is_not(None),
                                TakDeliveryJob.claim_expires_at <= now,
                            ),
                        )
                    )
                    .order_by(
                        TakDeliveryJob.priority.asc(),
                        TakDeliveryJob.available_at.asc(),
                        TakDeliveryJob.created_at.asc(),
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )

                jobs = (await session.execute(stmt)).scalars().all()
                for job in jobs:
                    job.status = "claimed"
                    job.claimed_by = instance_id
                    job.claim_expires_at = claim_expires_at
                    job.updated_at = now

                return jobs

    async def mark_delivery_done(self, *, job_id: int) -> None:
        """Mark a TAK delivery job as completed successfully."""
        stmt = (
            update(TakDeliveryJob)
            .where(TakDeliveryJob.id == job_id)
            .values(
                status="done",
                claim_expires_at=None,
                claimed_by=None,
                last_error=None,
                updated_at=func.now(),
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def mark_delivery_failed(self, *, job_id: int, error_text: str) -> None:
        """Mark a TAK delivery job as failed after a send attempt."""
        stmt = (
            update(TakDeliveryJob)
            .where(TakDeliveryJob.id == job_id)
            .values(
                status="failed",
                claim_expires_at=None,
                claimed_by=None,
                last_error=error_text,
                updated_at=func.now(),
            )
        )

        async with self.session_factory() as session:
            await session.execute(stmt)
            await session.commit()

    async def get_delivery_job(self, *, job_id: int) -> TakDeliveryJob | None:
        """Fetch one TAK delivery job by primary key."""
        stmt = select(TakDeliveryJob).where(TakDeliveryJob.id == job_id)

        async with self.session_factory() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
