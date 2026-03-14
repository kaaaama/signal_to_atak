"""Application assembly and startup."""

from __future__ import annotations

import logging

from signalbot import Config, SignalBot, enable_console_logging

from app.services.background_task_manager import BackgroundTaskManager
from app.cot import CotService
from app.cot_type_catalog import CotTypeCatalogService
from app.db import PostgresStore
from app.dispatcher import MessageDispatcher
from app.settings import Settings
from app.tak_client import TakTlsClient
from app.tak_delivery import TakDeliveryService
from app.services.validate_command import ValidateCommand
from app.validation import ValidationService


class Application:
    """Build and run the Signal-to-TAK application graph."""

    def __init__(self) -> None:
        """Initialize the application container without side effects."""
        self.settings = Settings.from_env()
        self.bot: SignalBot | None = None
        self.pg: PostgresStore | None = None
        self.tak_client: TakTlsClient | None = None
        self.tak_delivery: TakDeliveryService | None = None
        self.validation_service: ValidationService | None = None
        self.catalog_service: CotTypeCatalogService | None = None
        self.cot_service: CotService | None = None
        self.dispatcher: MessageDispatcher | None = None
        self.task_manager: BackgroundTaskManager | None = None
        self.command: ValidateCommand | None = None

    def setup_logging(self) -> None:
        """Configure console logging using the configured log level."""
        enable_console_logging(
            getattr(logging, str(self.settings.log_level).upper(), logging.INFO)
        )

    def build_components(self) -> None:
        """Construct and wire the application's service objects."""
        self.pg = PostgresStore(
            database_url=self.settings.database_url,
            pool_size=self.settings.db_pool_size,
            max_overflow=self.settings.db_max_overflow,
        )
        self.tak_client = TakTlsClient(self.settings)
        self.tak_delivery = TakDeliveryService(
            pg=self.pg,
            tak_client=self.tak_client,
            settings=self.settings,
        )
        self.validation_service = ValidationService()
        self.catalog_service = CotTypeCatalogService()
        self.cot_service = CotService(self.catalog_service)
        self.dispatcher = MessageDispatcher(
            pg=self.pg,
            tak_delivery=self.tak_delivery,
            settings=self.settings,
            validation_service=self.validation_service,
            cot_service=self.cot_service,
        )
        self.task_manager = BackgroundTaskManager(
            self.dispatcher,
            self.tak_delivery,
        )
        self.command = ValidateCommand(
            pg=self.pg,
            dispatcher=self.dispatcher,
            task_manager=self.task_manager,
        )

    def build_bot(self) -> None:
        """Create the SignalBot instance and register the command handler."""
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
        """Build all services and start the Signal bot event loop."""
        self.setup_logging()
        self.build_components()
        self.build_bot()
        self.bot.start()
