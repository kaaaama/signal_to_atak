from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from faker import Faker

from app.db import utc_now
from app.dispatcher import MessageDispatcher
from app.models import MessageKey
from app.services.validation import ParsedPayload, ValidationService
from app.tak.cot import CotService
from app.tak.cot_type_catalog import CotTypeCatalogService
from app.tak.delivery import TakDeliveryService


@dataclass
class FakePg:
    stored_payloads: list[dict[str, Any]] = field(default_factory=list)
    done_calls: list[dict[str, Any]] = field(default_factory=list)
    failed_calls: list[dict[str, Any]] = field(default_factory=list)
    delivered_calls: list[dict[str, Any]] = field(default_factory=list)
    queued_calls: list[dict[str, Any]] = field(default_factory=list)
    replay_calls: list[dict[str, Any]] = field(default_factory=list)
    rows: dict[tuple[str, int, str], SimpleNamespace] = field(default_factory=dict)
    claimed_retry_batch: list[MessageKey] = field(default_factory=list)
    claimed_replay_batch: list[MessageKey] = field(default_factory=list)
    cleared_now: Any = None

    async def store_parsed_payload(self, **kwargs: Any) -> None:
        self.stored_payloads.append(kwargs)

    async def mark_done(self, **kwargs: Any) -> None:
        self.done_calls.append(kwargs)

    async def mark_failed(self, **kwargs: Any) -> None:
        self.failed_calls.append(kwargs)

    async def mark_delivered_and_schedule_replay(self, **kwargs: Any) -> None:
        self.delivered_calls.append(kwargs)

    async def mark_delivery_queued(self, **kwargs: Any) -> None:
        self.queued_calls.append(kwargs)
        key = kwargs["key"]
        self.rows[(key.source, key.message_timestamp, key.raw_text)] = SimpleNamespace(
            response_text=kwargs["response_text"],
            active_until=None,
        )

    async def mark_replay_scheduled(self, **kwargs: Any) -> None:
        self.replay_calls.append(kwargs)

    async def mark_replay_failed(self, **kwargs: Any) -> None:
        self.failed_calls.append(kwargs)

    async def get_processed_message(
        self, *, key: MessageKey
    ) -> SimpleNamespace | None:
        return self.rows.get((key.source, key.message_timestamp, key.raw_text))

    async def clear_expired_replays(self, *, now: Any) -> None:
        self.cleared_now = now

    async def claim_retry_batch(self, **_: Any) -> list[MessageKey]:
        return list(self.claimed_retry_batch)

    async def claim_replay_batch(self, **_: Any) -> list[MessageKey]:
        return list(self.claimed_replay_batch)


@dataclass
class FakeTakDelivery:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def send_event(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class FakeTakClient:
    async def connect(self) -> None:
        return None

    async def send_on_existing_connection(self, payload: bytes) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
def faker_instance() -> Faker:
    faker = Faker()
    Faker.seed(12345)
    return faker


@pytest.fixture
def message_key_factory(
    faker_instance: Faker,
) -> Callable[..., MessageKey]:
    def factory(**overrides: Any) -> MessageKey:
        target = overrides.pop("target", "tank")
        lat = overrides.pop("lat", "48.563123")
        lon = overrides.pop("lon", "39.8917")
        return MessageKey(
            source=overrides.pop("source", faker_instance.numerify("+1555#######")),
            message_timestamp=overrides.pop(
                "message_timestamp",
                faker_instance.random_int(min=1_000_000_000, max=9_999_999_999),
            ),
            raw_text=overrides.pop("raw_text", f"{lat} {lon} {target}"),
        )

    return factory


@pytest.fixture
def parsed_payload_factory() -> Callable[..., ParsedPayload]:
    def factory(**overrides: Any) -> ParsedPayload:
        return ParsedPayload(
            lat=overrides.pop("lat", Decimal("48.563123")),
            lon=overrides.pop("lon", Decimal("39.8917")),
            target=overrides.pop("target", "tank"),
        )

    return factory


@pytest.fixture
def fake_pg() -> FakePg:
    return FakePg()


@pytest.fixture
def fake_tak_delivery() -> FakeTakDelivery:
    return FakeTakDelivery()


@pytest.fixture
def fake_tak_client() -> FakeTakClient:
    return FakeTakClient()


@pytest.fixture
def validation_service() -> ValidationService:
    return ValidationService()


@pytest.fixture
def cot_service() -> CotService:
    return CotService(CotTypeCatalogService())


@pytest.fixture
def dispatcher_settings() -> SimpleNamespace:
    return SimpleNamespace(
        active_cot_lifetime_sec=86400,
        cot_rebroadcast_interval_sec=20,
        retry_loop_interval_sec=30,
        cot_stale_seconds=60,
        failed_retry_min_age_sec=60,
        stale_processing_after_sec=300,
        retry_batch_size=100,
        cot_rebroadcast_batch_size=100,
        cot_rebroadcast_poll_interval_sec=5,
    )


@pytest.fixture
def delivery_settings() -> SimpleNamespace:
    return SimpleNamespace(
        instance_id="test-node",
        rabbitmq_url="amqp://guest:guest@rabbitmq:5672/",
        tak_delivery_queue_name="tak_delivery",
        rabbitmq_reconnect_interval_sec=1,
        cot_rebroadcast_interval_sec=20,
        retry_loop_interval_sec=30,
    )


@pytest.fixture
def dispatcher(
    fake_pg: FakePg,
    fake_tak_delivery: FakeTakDelivery,
    dispatcher_settings: SimpleNamespace,
    validation_service: ValidationService,
    cot_service: CotService,
) -> MessageDispatcher:
    return MessageDispatcher(
        pg=fake_pg,
        tak_delivery=fake_tak_delivery,
        settings=dispatcher_settings,
        validation_service=validation_service,
        cot_service=cot_service,
    )


@pytest.fixture
def delivery_service(
    fake_pg: FakePg,
    fake_tak_client: FakeTakClient,
    delivery_settings: SimpleNamespace,
) -> TakDeliveryService:
    return TakDeliveryService(
        pg=fake_pg,
        tak_client=fake_tak_client,
        settings=delivery_settings,
    )


@pytest.fixture
def expired_row() -> SimpleNamespace:
    return SimpleNamespace(
        response_text="Validated and queued for TAK delivery.",
        active_until=utc_now() - timedelta(seconds=1),
    )
