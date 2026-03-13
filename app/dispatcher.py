import asyncio
import logging
from datetime import timedelta

from app.cot import build_cot_xml
from app.db import PostgresStore, utc_now
from app.models import MessageKey
from app.settings import Settings
from app.tak_client import TakTlsClient
from app.validation import (
    ParsedPayload,
    format_success_reply,
    format_validation_error,
    parse_message,
)


class MessageDispatcher:
    def __init__(
        self,
        *,
        pg: PostgresStore,
        tak_client: TakTlsClient,
        settings: Settings,
    ) -> None:
        self.pg = pg
        self.tak_client = tak_client
        self.settings = settings
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
            payload = parse_message(raw_text)
        except Exception as exc:
            reply = format_validation_error(exc)
            await self.pg.mark_done(
                key=key,
                is_valid=False,
                response_text=reply,
            )
            return reply

        delivered, last_error = await self._send_with_retries(
            key=key,
            payload=payload,
            attempts=self.settings.immediate_retry_attempts,
            base_delay_sec=self.settings.immediate_retry_delay_sec,
            phase="immediate",
        )

        if delivered:
            reply = format_success_reply(
                payload,
                delivered_to_tak=True,
            )
            await self.pg.mark_done(
                key=key,
                is_valid=True,
                response_text=reply,
            )
            return reply

        reply = format_success_reply(
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
            payload = parse_message(key.raw_text)
        except Exception as exc:
            reply = format_validation_error(exc)
            await self.pg.mark_done(
                key=key,
                is_valid=False,
                response_text=reply,
            )
            return

        delivered, last_error = await self._send_with_retries(
            key=key,
            payload=payload,
            attempts=self.settings.immediate_retry_attempts,
            base_delay_sec=self.settings.immediate_retry_delay_sec,
            phase="background",
        )

        if delivered:
            reply = format_success_reply(
                payload,
                delivered_to_tak=True,
            )
            await self.pg.mark_done(
                key=key,
                is_valid=True,
                response_text=reply,
            )
            return

        await self.pg.mark_failed(
            key=key,
            is_valid=True,
            response_text=format_success_reply(
                payload,
                delivered_to_tak=False,
                retry_scheduled=True,
            ),
            error_text=last_error or "Unknown TAK delivery error",
        )

    async def _send_with_retries(
        self,
        *,
        key: MessageKey,
        payload: ParsedPayload,
        attempts: int,
        base_delay_sec: float,
        phase: str,
    ) -> tuple[bool, str | None]:
        last_error: str | None = None
        cot_xml = build_cot_xml(
            key=key,
            payload=payload,
            stale_seconds=self.settings.cot_stale_seconds,
        )

        for attempt in range(1, attempts + 1):
            try:
                await self.tak_client.send_event(cot_xml)
                self.log.info(
                    "Delivered to TAK on %s attempt %d/%d for %s",
                    phase,
                    attempt,
                    attempts,
                    key,
                )
                return True, None
            except Exception as exc:
                last_error = str(exc)
                self.log.warning(
                    "TAK delivery failed on %s attempt %d/%d for %s: %s",
                    phase,
                    attempt,
                    attempts,
                    key,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(base_delay_sec * attempt)

        return False, last_error