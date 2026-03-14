"""Application entry point for the Signal-to-TAK bridge."""

from __future__ import annotations

import asyncio
import logging

from signalbot import Command, Config, Context, SignalBot, enable_console_logging

from app.db import PostgresStore
from app.dispatcher import MessageDispatcher
from app.settings import Settings
from app.tak_client import TakTlsClient
from app.validation import ValidationService
from app.cot import CotService
from app.cot_type_catalog import CotTypeCatalogService


class BackgroundTaskManager:
    """Manages background retry and replay tasks."""

    def __init__(self, dispatcher: MessageDispatcher) -> None:
        self.dispatcher = dispatcher
        self.log = logging.getLogger("bot.tasks")
        self._retry_task: asyncio.Task | None = None
        self._replay_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def ensure_tasks_running(self) -> None:
        """Ensure background tasks are running.

        The method is safe to call on every incoming message. It uses a lock so
        concurrent handlers do not start duplicate retry or replay tasks, and
        it recreates tasks if either loop exited unexpectedly.
        """
        async with self._lock:
            if self._retry_task is None or self._retry_task.done():
                self._retry_task = asyncio.create_task(self.dispatcher.retry_forever())
                self.log.info("Started background TAK retry loop")

            if self._replay_task is None or self._replay_task.done():
                self._replay_task = asyncio.create_task(
                    self.dispatcher.replay_active_events_forever()
                )
                self.log.info("Started background active CoT replay loop")


class ValidateCommand(Command):
    """Claim, process, and reply to incoming Signal messages."""

    def __init__(
        self,
        *,
        pg: PostgresStore,
        dispatcher: MessageDispatcher,
        task_manager: BackgroundTaskManager,
    ) -> None:
        super().__init__()
        self.pg = pg
        self.dispatcher = dispatcher
        self.task_manager = task_manager
        self.log = logging.getLogger("bot.validate")

    async def handle(self, context: Context) -> None:
        """Handle an incoming Signal message.

        This is the integration point called by ``signalbot`` for each inbound
        message. It makes sure background workers are alive, ignores empty
        messages, claims the message in PostgreSQL to avoid duplicate handling,
        delegates the actual processing to the dispatcher, and then sends the
        generated reply back over Signal.

        Args:
            context: SignalBot context containing the message.
        """
        await self.task_manager.ensure_tasks_running()

        text = context.message.text

        if not isinstance(text, str) or not text.strip():
            return

        source = context.message.source
        message_timestamp = context.message.timestamp
        raw_text = text.strip()

        claimed = await self.pg.try_claim_message(
            source=source,
            message_timestamp=message_timestamp,
            raw_text=raw_text,
        )
        if not claimed:
            return

        reply = await self.dispatcher.process_new_message(
            source=source,
            message_timestamp=message_timestamp,
            raw_text=raw_text,
        )

        try:
            await context.reply(reply)
        except Exception:
            self.log.exception(
                "Failed to send Signal reply for %s / %s",
                source,
                message_timestamp,
            )


class Application:
    """Main application service for the Signal to TAK bot."""

    def __init__(self) -> None:
        """Initialize the application container without side effects."""
        self.settings = Settings.from_env()
        self.bot: SignalBot | None = None
        self.pg: PostgresStore | None = None
        self.tak_client: TakTlsClient | None = None
        self.dispatcher: MessageDispatcher | None = None
        self.task_manager: BackgroundTaskManager | None = None
        self.command: ValidateCommand | None = None

    def setup_logging(self) -> None:
        """Set up application logging.

        Logging level selection is driven by the environment-backed settings so
        the container can switch verbosity without code changes.
        """
        enable_console_logging(
            getattr(logging, self.settings.log_level.upper(), logging.INFO)
        )

    def build_components(self) -> None:
        """Build and wire application components.

        This creates the database store, TAK client, validation and CoT
        services, dispatcher, background task manager, and Signal command in the
        order required by their dependencies.
        """
        self.pg = PostgresStore(
            database_url=self.settings.database_url,
            pool_size=self.settings.db_pool_size,
            max_overflow=self.settings.db_max_overflow,
        )
        self.tak_client = TakTlsClient(self.settings)
        self.validation_service = ValidationService()
        self.catalog_service = CotTypeCatalogService()
        self.cot_service = CotService(self.catalog_service)
        self.dispatcher = MessageDispatcher(
            pg=self.pg,
            tak_client=self.tak_client,
            settings=self.settings,
            validation_service=self.validation_service,
            cot_service=self.cot_service,
        )
        self.task_manager = BackgroundTaskManager(self.dispatcher)
        self.command = ValidateCommand(
            pg=self.pg,
            dispatcher=self.dispatcher,
            task_manager=self.task_manager,
        )

    def build_bot(self) -> None:
        """Build the SignalBot instance and register the command handler.

        The bot uses in-memory signalbot storage because durable message state
        is already tracked in PostgreSQL by this application.
        """
        self.bot = SignalBot(
            Config(
                signal_service=self.settings.signal_service,
                phone_number=self.settings.phone_number,
                storage={"type": "in-memory"},
                download_attachments=False,
            )
        )
        self.bot.register(self.command, contacts=True)

    def run(self) -> None:
        """Configure logging, assemble components, and start the bot.

        This is the high-level bootstrap sequence used both by the CLI entry
        point and by any embedding code that wants to run the full service.
        """
        self.setup_logging()
        self.build_components()
        self.build_bot()

        self.bot.start()


def main() -> None:
    """Run the application from the module entry point.

    Keeping the entry point as a thin wrapper makes the startup sequence easy
    to reuse in tests or alternative launchers.
    """
    app = Application()
    app.run()


if __name__ == "__main__":
    main()
