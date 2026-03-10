import asyncio
import logging

from signalbot import Command, Config, Context, SignalBot, enable_console_logging

from app.db import PostgresStore
from app.settings import Settings
from app.validation import format_reply, validate_message


class ValidateCommand(Command):
    def __init__(self, pg: PostgresStore) -> None:
        super().__init__()
        self.pg = pg

    async def handle(self, context: Context) -> None:
        text = context.message.text

        if not isinstance(text, str) or not text.strip():
            return

        source = context.message.source
        message_timestamp = context.message.timestamp
        raw_text = text

        claimed = await self.pg.try_claim_message(
            source=source,
            message_timestamp=message_timestamp,
            raw_text=raw_text,
        )
        if not claimed:
            return

        try:
            result = validate_message(text)
            reply = format_reply(result)

            await context.reply(reply)

            await self.pg.mark_processed(
                source=source,
                message_timestamp=message_timestamp,
                raw_text=raw_text,
                is_valid=result.is_valid,
                response_text=reply,
            )
        except Exception as exc:
            await self.pg.mark_failed(
                source=source,
                message_timestamp=message_timestamp,
                raw_text=raw_text,
                error_text=str(exc),
            )
            raise


def main() -> None:
    settings = Settings.from_env()

    enable_console_logging(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    )

    bot = SignalBot(
        Config(
            signal_service=settings.signal_service,
            phone_number=settings.phone_number,
            storage=None,
            download_attachments=False,
        )
    )

    pg = PostgresStore(
        database_url=settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )

    bot.register(
        ValidateCommand(pg),
        contacts=True
    )

    try:
        bot.start()
    finally:
        asyncio.run(pg.close())


if __name__ == "__main__":
    main()