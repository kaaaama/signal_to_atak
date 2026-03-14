"""RabbitMQ-backed TAK delivery queue and worker coordination."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

import aio_pika
from aio_pika import DeliveryMode, IncomingMessage, Message
from aio_pika.abc import AbstractRobustChannel, AbstractRobustConnection, AbstractRobustQueue

from app.db import PostgresStore, utc_now
from app.models import MessageKey
from app.settings import Settings
from app.tak.client import TakTlsClient


@dataclass(frozen=True)
class TakDeliveryEnvelope:
    """Serializable delivery message published to RabbitMQ."""

    source: str
    message_timestamp: int
    raw_text: str
    uid: str
    payload_xml: str
    phase: str

    @classmethod
    def from_message(
        cls,
        *,
        key: MessageKey,
        uid: str,
        payload: bytes,
        phase: str,
    ) -> "TakDeliveryEnvelope":
        """Build an envelope from a message key and CoT payload."""
        return cls(
            source=key.source,
            message_timestamp=key.message_timestamp,
            raw_text=key.raw_text,
            uid=uid,
            payload_xml=payload.decode("utf-8"),
            phase=phase,
        )

    def key(self) -> MessageKey:
        """Return the database key represented by this envelope."""
        return MessageKey(
            source=self.source,
            message_timestamp=self.message_timestamp,
            raw_text=self.raw_text,
        )


class TakDeliveryService:
    """Publish TAK delivery work to RabbitMQ and consume it from one worker."""

    def __init__(
        self,
        *,
        pg: PostgresStore,
        tak_client: TakTlsClient,
        settings: Settings,
    ) -> None:
        """Store delivery dependencies and lazy RabbitMQ connection state."""
        self.pg = pg
        self.tak_client = tak_client
        self.settings = settings
        self.log = logging.getLogger("atak.delivery")
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._queue: AbstractRobustQueue | None = None
        self._connect_lock = asyncio.Lock()

    async def send_event(
        self,
        *,
        key: MessageKey,
        uid: str,
        payload: bytes,
        phase: str,
    ) -> None:
        """Publish one TAK delivery request to RabbitMQ and return immediately."""
        queue = await self._ensure_queue()
        envelope = TakDeliveryEnvelope.from_message(
            key=key,
            uid=uid,
            payload=payload,
            phase=phase,
        )
        body = json.dumps(asdict(envelope)).encode("utf-8")
        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            message_id=f"{uid}:{phase}:{key.message_timestamp}",
        )
        channel = await self._ensure_channel()
        await channel.default_exchange.publish(
            message,
            routing_key=queue.name,
        )
        self.log.info(
            "Published TAK delivery message uid=%s phase=%s queue=%s",
            uid,
            phase,
            queue.name,
        )

    async def delivery_worker_forever(self) -> None:
        """Consume RabbitMQ delivery messages and forward them to TAK."""
        while True:
            try:
                queue = await self._ensure_queue()
                self.log.info(
                    "Starting RabbitMQ TAK delivery consumer on queue=%s instance=%s",
                    queue.name,
                    self.settings.instance_id,
                )
                async with queue.iterator() as iterator:
                    async for message in iterator:
                        await self._handle_incoming_message(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("RabbitMQ TAK delivery worker failed")
                await self._close_broker()
                await asyncio.sleep(self.settings.rabbitmq_reconnect_interval_sec)

    async def close(self) -> None:
        """Close RabbitMQ connection resources."""
        await self._close_broker()

    async def _handle_incoming_message(self, message: IncomingMessage) -> None:
        """Deserialize one RabbitMQ message and apply TAK delivery side effects."""
        async with message.process(requeue=True):
            envelope = TakDeliveryEnvelope(**json.loads(message.body.decode("utf-8")))
            await self._deliver_envelope(envelope)

    async def _deliver_envelope(self, envelope: TakDeliveryEnvelope) -> None:
        """Send one envelope to TAK and update message state in PostgreSQL."""
        key = envelope.key()
        row = await self.pg.get_processed_message(key=key)
        if row is None:
            self.log.warning(
                "Skipping TAK delivery for missing processed message uid=%s phase=%s",
                envelope.uid,
                envelope.phase,
            )
            return

        now = utc_now()
        if row.active_until is not None and row.active_until <= now:
            self.log.info(
                "Skipping expired TAK delivery uid=%s phase=%s active_until=%s",
                envelope.uid,
                envelope.phase,
                row.active_until,
            )
            if envelope.phase == "replay":
                await self.pg.clear_expired_replays(now=now)
            else:
                await self.pg.mark_done(
                    key=key,
                    is_valid=True,
                    response_text=row.response_text
                    or "Validated and queued for TAK delivery.",
                )
            return

        try:
            await self.tak_client.connect()
            await self.tak_client.send_on_existing_connection(
                envelope.payload_xml.encode("utf-8")
            )
        except Exception as exc:
            await self.tak_client.close()
            await self._mark_delivery_failure(
                key=key,
                phase=envelope.phase,
                error_text=str(exc),
                response_text=row.response_text,
            )
            self.log.warning(
                "TAK delivery failed uid=%s phase=%s instance=%s: %s",
                envelope.uid,
                envelope.phase,
                self.settings.instance_id,
                exc,
            )
            return

        await self._mark_delivery_success(
            key=key,
            phase=envelope.phase,
            response_text=row.response_text
            or "Validated and queued for TAK delivery.",
        )
        self.log.info(
            "TAK delivery completed uid=%s phase=%s instance=%s",
            envelope.uid,
            envelope.phase,
            self.settings.instance_id,
        )

    async def _mark_delivery_success(
        self,
        *,
        key: MessageKey,
        phase: str,
        response_text: str,
    ) -> None:
        """Apply business-state updates after a successful TAK send."""
        when = utc_now()
        if phase == "replay":
            await self.pg.mark_replay_scheduled(
                key=key,
                when=when,
                replay_interval_sec=self.settings.cot_rebroadcast_interval_sec,
            )
            return

        await self.pg.mark_delivered_and_schedule_replay(
            key=key,
            response_text=response_text,
            when=when,
            replay_interval_sec=self.settings.cot_rebroadcast_interval_sec,
        )

    async def _mark_delivery_failure(
        self,
        *,
        key: MessageKey,
        phase: str,
        error_text: str,
        response_text: str | None,
    ) -> None:
        """Apply business-state updates after a failed TAK send."""
        if phase == "replay":
            await self.pg.mark_replay_failed(
                key=key,
                error_text=error_text,
                retry_after_sec=self.settings.retry_loop_interval_sec,
            )
            return

        await self.pg.mark_failed(
            key=key,
            is_valid=True,
            response_text=response_text,
            error_text=error_text,
        )

    async def _ensure_queue(self) -> AbstractRobustQueue:
        """Ensure the RabbitMQ queue has been declared."""
        if self._queue is not None:
            return self._queue

        async with self._connect_lock:
            if self._queue is not None:
                return self._queue

            channel = await self._ensure_channel_locked()
            self._queue = await channel.declare_queue(
                self.settings.tak_delivery_queue_name,
                durable=True,
            )
            return self._queue

    async def _ensure_channel(self) -> AbstractRobustChannel:
        """Ensure a RabbitMQ channel is available."""
        if self._channel is not None and not self._channel.is_closed:
            return self._channel

        async with self._connect_lock:
            return await self._ensure_channel_locked()

    async def _ensure_channel_locked(self) -> AbstractRobustChannel:
        """Ensure a RabbitMQ channel is available while holding the connect lock."""
        if self._channel is not None and not self._channel.is_closed:
            return self._channel

        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(
                self.settings.rabbitmq_url
            )
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=1)
        return self._channel

    async def _close_broker(self) -> None:
        """Dispose current RabbitMQ objects so a later call can reconnect."""
        channel = self._channel
        connection = self._connection

        self._queue = None
        self._channel = None
        self._connection = None

        if channel is not None and not channel.is_closed:
            await channel.close()
        if connection is not None and not connection.is_closed:
            await connection.close()
