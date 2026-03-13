import asyncio
import logging
from datetime import timedelta

from app.cot import CotService
from app.db import PostgresStore, utc_now
from app.models import MessageKey
from app.settings import Settings
from app.tak_client import TakTlsClient
from app.validation import ParsedPayload, ValidationService


class MessageDispatcher:
    def __init__(
        self,
        *,
        pg: PostgresStore,
        tak_client: TakTlsClient,
        settings: Settings,
        validation_service: ValidationService,
        cot_service: CotService,
    ) -> None:
        self.pg = pg
        self.tak_client = tak_client
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

        delivered, last_error = await self._send_with_retries(
            uid=uid,
            payload=payload,
            attempts=self.settings.immediate_retry_attempts,
            base_delay_sec=self.settings.immediate_retry_delay_sec,
            phase="immediate",
        )

        if delivered:
            reply = self.validation_service.format_success_reply(
                payload,
                delivered_to_tak=True,
            )
            await self.pg.mark_delivered_and_schedule_replay(
                key=key,
                response_text=reply,
                when=utc_now(),
                replay_interval_sec=self.settings.cot_rebroadcast_interval_sec,
            )
            return reply

        reply = self.validation_service.format_success_reply(
            payload,
            delivered_to_tak=False,
            retry_scheduled=True,
        )
        await self.pg.mark_failed(
            key=key,
            is_valid=True,
            response_text=reply,
            error_text=last_error or "Unknown TAK delivery error",
        )
        return reply

    async def retry_forever(self) -> None:
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
                )

                if batch:
                    self.log.info("Claimed %d message(s) for TAK retry", len(batch))

                for key in batch:
                    await self._retry_one(key)

            except Exception:
                self.log.exception("Retry loop iteration failed")

            await asyncio.sleep(self.settings.retry_loop_interval_sec)

    async def _retry_one(self, key: MessageKey) -> None:
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

        uid = self.cot_service.build_uid(key)

        delivered, last_error = await self._send_with_retries(
            uid=uid,
            payload=payload,
            attempts=self.settings.immediate_retry_attempts,
            base_delay_sec=self.settings.immediate_retry_delay_sec,
            phase="background-retry",
        )

        if delivered:
            reply = self.validation_service.format_success_reply(
                payload,
                delivered_to_tak=True,
            )
            await self.pg.mark_delivered_and_schedule_replay(
                key=key,
                response_text=reply,
                when=utc_now(),
                replay_interval_sec=self.settings.cot_rebroadcast_interval_sec,
            )
            return

        await self.pg.mark_failed(
            key=key,
            is_valid=True,
            response_text=self.validation_service.format_success_reply(
                payload,
                delivered_to_tak=False,
                retry_scheduled=True,
            ),
            error_text=last_error or "Unknown TAK delivery error",
        )

    async def replay_active_events_forever(self) -> None:
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
            await self.tak_client.send_event(cot_xml)
            await self.pg.mark_replay_scheduled(
                key=key,
                when=utc_now(),
                replay_interval_sec=self.settings.cot_rebroadcast_interval_sec,
            )
            self.log.info("Replayed active CoT event for %s", key)
        except Exception as exc:
            await self.pg.mark_replay_failed(
                key=key,
                error_text=str(exc),
                retry_after_sec=self.settings.retry_loop_interval_sec,
            )
            self.log.warning("Replay failed for %s: %s", key, exc)

    async def _send_with_retries(
        self,
        *,
        uid: str,
        payload: ParsedPayload,
        attempts: int,
        base_delay_sec: float,
        phase: str,
    ) -> tuple[bool, str | None]:
        last_error: str | None = None
        cot_xml = self.cot_service.build_cot_xml(
            uid=uid,
            payload=payload,
            stale_seconds=self.settings.cot_stale_seconds,
        )

        for attempt in range(1, attempts + 1):
            try:
                self.log.info(
                    "Sending CoT uid=%s phase=%s lat=%s lon=%s target=%s",
                    uid,
                    phase,
                    payload.lat,
                    payload.lon,
                    payload.target,
                )
                await self.tak_client.send_event(cot_xml)
                self.log.info(
                    "Delivered to TAK on %s attempt %d/%d for uid=%s",
                    phase,
                    attempt,
                    attempts,
                    uid,
                )
                return True, None
            except Exception as exc:
                last_error = str(exc)
                self.log.warning(
                    "TAK delivery failed on %s attempt %d/%d for uid=%s: %s",
                    phase,
                    attempt,
                    attempts,
                    uid,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(base_delay_sec * attempt)

        return False, last_error
