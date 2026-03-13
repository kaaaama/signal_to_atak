from __future__ import annotations

import asyncio
import logging

from signalbot import Command, Config, Context, SignalBot, enable_console_logging

from app.db import PostgresStore
from app.dispatcher import MessageDispatcher
from app.settings import Settings
from app.tak_client import TakTlsClient


class ValidateCommand(Command):
    def __init__(self, *, pg: PostgresStore, dispatcher: MessageDispatcher) -> None:
        super().__init__()
        self.pg = pg
        self.dispatcher = dispatcher
        self.log = logging.getLogger("bot.validate")
        self._retry_task: asyncio.Task | None = None

    async def _ensure_retry_task(self) -> None:
        if self._retry_task is None or self._retry_task.done():
            self._retry_task = asyncio.create_task(self.dispatcher.retry_forever())
            self.log.info("Started background TAK retry loop")

    async def handle(self, context: Context) -> None:
        await self._ensure_retry_task()

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


def main() -> None:
    settings = Settings.from_env()

    enable_console_logging(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    )

    bot = SignalBot(
        Config(
            signal_service=settings.signal_service,
            phone_number=settings.phone_number,
            storage={"type": "in-memory"},
            download_attachments=False,
        )
    )

    pg = PostgresStore(
        database_url=settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    tak_client = TakTlsClient(settings)
    dispatcher = MessageDispatcher(
        pg=pg,
        tak_client=tak_client,
        settings=settings,
    )

    bot.register(
        ValidateCommand(pg=pg, dispatcher=dispatcher),
        contacts=True,
    )

    try:
        bot.start()
    finally:
        asyncio.run(pg.close())


if __name__ == "__main__":
    main()