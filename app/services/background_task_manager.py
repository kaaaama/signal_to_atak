"""Background task lifecycle management for delivery, retry, and replay loops."""

from __future__ import annotations

import asyncio
import logging

from app.dispatcher import MessageDispatcher
from app.tak.delivery import TakDeliveryService


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

    async def shutdown(self) -> None:
        """Cancel background tasks and wait for them to finish."""
        async with self._lock:
            tasks = [
                task
                for task in (
                    self._delivery_task,
                    self._retry_task,
                    self._replay_task,
                )
                if task is not None and not task.done()
            ]

            for task in tasks:
                task.cancel()

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                self.log.info("Stopped background delivery, retry, and replay tasks")

            self._delivery_task = None
            self._retry_task = None
            self._replay_task = None
