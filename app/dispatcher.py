"""Message orchestration between validation, storage, and TAK delivery."""

import logging
import asyncio
from datetime import timedelta

from app.db import PostgresStore, utc_now
from app.models import MessageKey
from app.settings import Settings
from app.tak.cot import CotService
from app.tak.delivery import TakDeliveryService
from app.services.validation import ParsedPayload, ValidationService


class MessageDispatcher:
    """Coordinate parsing, persistence, delivery retries, and CoT replay."""

    def __init__(
        self,
        *,
        pg: PostgresStore,
        tak_delivery: TakDeliveryService,
        settings: Settings,
        validation_service: ValidationService,
        cot_service: CotService,
    ) -> None:
        self.pg = pg
        self.tak_delivery = tak_delivery
        self.settings = settings
        self.validation_service = validation_service
        self.cot_service = cot_service
        self.log = logging.getLogger("atak.dispatcher")

    async def process_new_message(
        self,
        *,
        source: str,
        message_timestamp: int,
        raw_text: str,
    ) -> str:
        """Validate, persist, deliver, and reply for a newly claimed message.

        The dispatcher first parses the Signal text. Invalid messages are
        marked complete immediately and return a validation reply. Valid
        messages get a stable UID plus stored payload metadata before the
        dispatcher enqueues TAK delivery work in RabbitMQ. Actual TAK send
        success or failure is handled asynchronously by the delivery worker.
        """
        key = MessageKey(
            source=source,
            message_timestamp=message_timestamp,
            raw_text=raw_text,
        )

        try:
            payload = self.validation_service.parse_message(raw_text)
        except Exception as exc:
            reply = self.validation_service.format_validation_error(exc)
            await self.pg.mark_done(
                key=key,
                is_valid=False,
                response_text=reply,
            )
            return reply

        uid = self.cot_service.build_uid(key)

        await self.pg.store_parsed_payload(
            key=key,
            uid=uid,
            payload=payload,
            active_until=utc_now()
            + timedelta(seconds=self.settings.active_cot_lifetime_sec),
        )

        reply = self.validation_service.format_success_reply(
            payload,
            delivered_to_tak=False,
            queued_for_delivery=True,
        )
        await self.pg.mark_delivery_queued(
            key=key,
            response_text=reply,
            worker_note="Queued for immediate TAK delivery",
        )
        cot_xml = self.cot_service.build_cot_xml(
            uid=uid,
            payload=payload,
            stale_seconds=self.settings.cot_stale_seconds,
        )
        await self.tak_delivery.send_event(
            key=key,
            uid=uid,
            payload=cot_xml,
            phase="immediate",
        )
        return reply

    async def retry_forever(self) -> None:
        """Continuously retry messages that previously failed TAK delivery.

        Each loop iteration claims a bounded batch of eligible rows, retries
        them one by one, logs any top-level loop failure, and then sleeps for
        the configured poll interval.
        """
        while True:
            try:
                now = utc_now()
                failed_before = now - timedelta(
                    seconds=self.settings.failed_retry_min_age_sec
                )
                processing_before = now - timedelta(
                    seconds=self.settings.stale_processing_after_sec
                )

                batch = await self.pg.claim_retry_batch(
                    limit=self.settings.retry_batch_size,
                    failed_before=failed_before,
                    processing_before=processing_before,
                    now=now,
                )

                if batch:
                    self.log.info("Claimed %d message(s) for TAK retry", len(batch))

                for key in batch:
                    await self._retry_one(key)

            except Exception:
                self.log.exception("Retry loop iteration failed")

            await asyncio.sleep(self.settings.retry_loop_interval_sec)

    async def _retry_one(self, key: MessageKey) -> None:
        """Retry TAK delivery for a single stored message.

        The payload is reconstructed from the original raw text instead of the
        stored columns so validation rules stay centralized. If the message is
        no longer valid under current parsing rules, the row is finalized as an
        invalid message rather than retried indefinitely.
        """
        try:
            payload = self.validation_service.parse_message(key.raw_text)
        except Exception as exc:
            reply = self.validation_service.format_validation_error(exc)
            await self.pg.mark_done(
                key=key,
                is_valid=False,
                response_text=reply,
            )
            return

        row = await self.pg.get_processed_message(key=key)
        if row is None:
            return

        if row.active_until is not None and row.active_until <= utc_now():
            await self.pg.mark_done(
                key=key,
                is_valid=True,
                response_text=row.response_text
                or self.validation_service.format_success_reply(
                    payload,
                    delivered_to_tak=False,
                    retry_scheduled=True,
                ),
            )
            return

        uid = self.cot_service.build_uid(key)
        await self.pg.mark_delivery_queued(
            key=key,
            response_text=row.response_text
            or self.validation_service.format_success_reply(
                payload,
                delivered_to_tak=False,
                retry_scheduled=True,
            ),
            worker_note="Queued for background TAK retry",
        )
        cot_xml = self.cot_service.build_cot_xml(
            uid=uid,
            payload=payload,
            stale_seconds=self.settings.cot_stale_seconds,
        )
        await self.tak_delivery.send_event(
            key=key,
            uid=uid,
            payload=cot_xml,
            phase="background-retry",
        )

    async def replay_active_events_forever(self) -> None:
        """Continuously rebroadcast active CoT events until they expire.

        The loop first clears rows whose active lifetime is over, then leases a
        batch of replayable rows, sends each event again, and sleeps until the
        next poll cycle. Exceptions at the loop level are logged and do not stop
        the background task.
        """
        while True:
            try:
                now = utc_now()
                await self.pg.clear_expired_replays(now=now)

                batch = await self.pg.claim_replay_batch(
                    limit=self.settings.cot_rebroadcast_batch_size,
                    now=now,
                    claim_lease_sec=max(
                        self.settings.cot_rebroadcast_poll_interval_sec * 2,
                        15.0,
                    ),
                )

                if batch:
                    self.log.info("Claimed %d message(s) for CoT replay", len(batch))

                for key in batch:
                    await self._replay_one(key)

            except Exception:
                self.log.exception("Replay loop iteration failed")

            await asyncio.sleep(self.settings.cot_rebroadcast_poll_interval_sec)

    async def _replay_one(self, key: MessageKey) -> None:
        """Replay one active CoT event from persisted payload data.

        This path reads the normalized payload back from the database, rebuilds
        a fresh CoT XML document with updated timestamps, and reschedules the
        next replay on success. Missing payload pieces are treated as a no-op
        because replay requires a fully materialized, previously validated row.
        """
        row = await self.pg.get_processed_message(key=key)
        if row is None:
            return

        if (
            not row.is_valid
            or row.lon is None
            or row.lat is None
            or row.target is None
            or row.uid is None
        ):
            return

        payload = ParsedPayload(
            lon=row.lon,
            lat=row.lat,
            target=row.target,
        )

        cot_xml = self.cot_service.build_cot_xml(
            uid=row.uid,
            payload=payload,
            stale_seconds=self.settings.cot_stale_seconds,
        )

        try:
            self.log.info(
                "Replaying CoT uid=%s lat=%s lon=%s target=%s",
                row.uid,
                payload.lat,
                payload.lon,
                payload.target,
            )
            await self.tak_delivery.send_event(
                key=key,
                payload=cot_xml,
                phase="replay",
                uid=row.uid,
            )
            self.log.info("Queued active CoT replay for %s", key)
        except Exception as exc:
            await self.pg.mark_replay_failed(
                key=key,
                error_text=str(exc),
                retry_after_sec=self.settings.retry_loop_interval_sec,
            )
            self.log.warning("Replay failed for %s: %s", key, exc)
