import os
from dataclasses import dataclass


@dataclass
class Settings:
    phone_number: str
    signal_service: str
    database_url: str
    db_pool_size: int = 5
    db_max_overflow: int = 30
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            phone_number=os.environ["PHONE_NUMBER"],
            signal_service=os.environ["SIGNAL_SERVICE"],
            database_url=os.environ["DATABASE_URL"],
            db_pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            db_max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "30")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )