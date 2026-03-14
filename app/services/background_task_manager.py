"""Background task lifecycle management for delivery, retry, and replay loops."""

from __future__ import annotations

import asyncio
import logging

from app.dispatcher import MessageDispatcher
from app.tak_delivery import TakDeliveryService


class BackgroundTaskManager:
    """Manage long-running delivery, retry, and replay tasks."""

    def __init__(
        self,
        dispatcher: MessageDispatcher,
        tak_delivery: TakDeliveryService,
    ) -> None:
        """Store the shared services and task handles."""
        self.dispatcher = dispatcher
        self.tak_delivery = tak_delivery
        self.log = logging.getLogger("bot.tasks")
        self._delivery_task: asyncio.Task | None = None
        self._retry_task: asyncio.Task | None = None
        self._replay_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def ensure_tasks_running(self) -> None:
        """Start background tasks once and restart them if any exits unexpectedly."""
        async with self._lock:
            if self._delivery_task is None or self._delivery_task.done():
                self._delivery_task = asyncio.create_task(
                    self.tak_delivery.delivery_worker_forever()
                )
                self.log.info("Started background TAK delivery worker")

            if self._retry_task is None or self._retry_task.done():
                self._retry_task = asyncio.create_task(self.dispatcher.retry_forever())
                self.log.info("Started background TAK retry loop")

            if self._replay_task is None or self._replay_task.done():
                self._replay_task = asyncio.create_task(
                    self.dispatcher.replay_active_events_forever()
                )
                self.log.info("Started background active CoT replay loop")
