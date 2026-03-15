"""Signal command handler for inbound messages."""

import logging

from signalbot import Command, Context

from app.services.background_task_manager import BackgroundTaskManager
from app.db import PostgresStore
from app.dispatcher import MessageDispatcher


class SignalCommand(Command):
    """Claim, process, and reply to incoming Signal messages."""

    def __init__(
        self,
        *,
        pg: PostgresStore,
        dispatcher: MessageDispatcher,
        task_manager: BackgroundTaskManager,
    ) -> None:
        """Store dependencies needed to process inbound Signal messages."""
        super().__init__()
        self.pg = pg
        self.dispatcher = dispatcher
        self.task_manager = task_manager
        self.log = logging.getLogger("bot.validate")

    async def handle(self, context: Context) -> None:
        """Claim the message, dispatch processing, and reply over Signal."""
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
