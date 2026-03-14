"""DB-backed TAK delivery queue and worker coordination."""

from __future__ import annotations

import asyncio
import logging

from app.db import PostgresStore, utc_now
from app.models import MessageKey, TakDeliveryJob
from app.settings import Settings
from app.tak_client import TakSendError, TakTlsClient


_PHASE_PRIORITY: dict[str, int] = {
    "immediate": 0,
    "background-retry": 1,
    "replay": 2,
}


class TakDeliveryService:
    """Queue TAK delivery jobs in PostgreSQL and process them from any replica."""

    def __init__(
        self,
        *,
        pg: PostgresStore,
        tak_client: TakTlsClient,
        settings: Settings,
    ) -> None:
        """Store the DB queue dependencies and worker configuration."""
        self.pg = pg
        self.tak_client = tak_client
        self.settings = settings
        self.log = logging.getLogger("atak.delivery")

    async def enqueue_send(
        self,
        *,
        key: MessageKey,
        uid: str,
        payload: bytes,
        phase: str,
    ) -> int:
        """Insert a delivery job and return its database identifier."""
        payload_xml = payload.decode("utf-8")
        return await self.pg.enqueue_delivery_job(
            key=key,
            uid=uid,
            payload_xml=payload_xml,
            phase=phase,
            priority=_PHASE_PRIORITY.get(phase, 1),
            available_at=utc_now(),
        )

    async def wait_for_job(self, *, job_id: int) -> None:
        """Poll the database until a delivery job completes or fails."""
        while True:
            job = await self.pg.get_delivery_job(job_id=job_id)
            if job is None:
                raise TakSendError(f"TAK delivery job {job_id} disappeared")
            if job.status == "done":
                return
            if job.status == "failed":
                raise TakSendError(job.last_error or "Unknown TAK delivery error")
            await asyncio.sleep(self.settings.tak_delivery_wait_poll_interval_sec)

    async def send_event(
        self,
        *,
        key: MessageKey,
        uid: str,
        payload: bytes,
        phase: str,
    ) -> None:
        """Enqueue one delivery job and wait for any worker to finish it."""
        job_id = await self.enqueue_send(
            key=key,
            uid=uid,
            payload=payload,
            phase=phase,
        )
        self.log.info(
            "Queued TAK delivery job_id=%s uid=%s phase=%s",
            job_id,
            uid,
            phase,
        )
        await self.wait_for_job(job_id=job_id)

    async def delivery_worker_forever(self) -> None:
        """Claim queued delivery jobs from PostgreSQL and send them to TAK."""
        while True:
            try:
                batch = await self.pg.claim_delivery_batch(
                    limit=self.settings.tak_delivery_batch_size,
                    now=utc_now(),
                    claim_lease_sec=self.settings.tak_delivery_claim_lease_sec,
                    instance_id=self.settings.instance_id,
                )

                if batch:
                    self.log.info(
                        "Claimed %d TAK delivery job(s) on %s",
                        len(batch),
                        self.settings.instance_id,
                    )

                for job in batch:
                    await self._deliver_job(job)

            except Exception:
                self.log.exception("TAK delivery worker iteration failed")

            await asyncio.sleep(self.settings.tak_delivery_poll_interval_sec)

    async def _deliver_job(self, job: TakDeliveryJob) -> None:
        """Send one claimed delivery job over the persistent TAK connection."""
        try:
            await self.tak_client.connect()
            await self.tak_client.send_on_existing_connection(
                job.payload_xml.encode("utf-8")
            )
        except Exception as exc:
            await self.tak_client.close()
            await self.pg.mark_delivery_failed(job_id=job.id, error_text=str(exc))
            self.log.warning(
                "TAK delivery job failed id=%s uid=%s phase=%s instance=%s: %s",
                job.id,
                job.uid,
                job.phase,
                self.settings.instance_id,
                exc,
            )
            return

        await self.pg.mark_delivery_done(job_id=job.id)
        self.log.info(
            "TAK delivery job completed id=%s uid=%s phase=%s instance=%s",
            job.id,
            job.uid,
            job.phase,
            self.settings.instance_id,
        )
