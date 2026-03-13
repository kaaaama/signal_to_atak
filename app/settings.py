from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    phone_number: str = Field(validation_alias="PHONE_NUMBER")
    signal_service: str = Field(validation_alias="SIGNAL_SERVICE")
    database_url: str = Field(validation_alias="DATABASE_URL")

    db_pool_size: int = Field(default=5, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=10, validation_alias="DB_MAX_OVERFLOW")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    tak_host: str = Field(validation_alias="TAK_HOST")
    tak_port: int = Field(default=8089, validation_alias="TAK_PORT")
    tak_server_hostname: str = Field(validation_alias="TAK_SERVER_HOSTNAME")
    tak_ca_file: Path = Field(validation_alias="TAK_CA_FILE")
    tak_client_cert_file: Path = Field(validation_alias="TAK_CLIENT_CERT_FILE")
    tak_client_key_file: Path = Field(validation_alias="TAK_CLIENT_KEY_FILE")
    tak_client_key_password: str | None = Field(
        default=None,
        validation_alias="TAK_CLIENT_KEY_PASSWORD",
    )

    tak_connect_timeout_sec: float = Field(
        default=5.0,
        validation_alias="TAK_CONNECT_TIMEOUT_SEC",
    )
    tak_write_timeout_sec: float = Field(
        default=5.0,
        validation_alias="TAK_WRITE_TIMEOUT_SEC",
    )

    cot_stale_seconds: int = Field(default=60, validation_alias="COT_STALE_SECONDS")

    immediate_retry_attempts: int = Field(
        default=3,
        validation_alias="IMMEDIATE_RETRY_ATTEMPTS",
    )
    immediate_retry_delay_sec: float = Field(
        default=1.0,
        validation_alias="IMMEDIATE_RETRY_DELAY_SEC",
    )

    retry_loop_interval_sec: float = Field(
        default=30.0,
        validation_alias="RETRY_LOOP_INTERVAL_SEC",
    )
    retry_batch_size: int = Field(default=100, validation_alias="RETRY_BATCH_SIZE")
    failed_retry_min_age_sec: int = Field(
        default=60,
        validation_alias="FAILED_RETRY_MIN_AGE_SEC",
    )
    stale_processing_after_sec: int = Field(
        default=300,
        validation_alias="STALE_PROCESSING_AFTER_SEC",
    )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()