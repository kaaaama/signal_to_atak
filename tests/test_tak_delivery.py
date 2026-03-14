from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tak.delivery import TakDeliveryEnvelope, TakDeliveryService


def test_delivery_envelope_round_trip(message_key_factory) -> None:
    key = message_key_factory()

    envelope = TakDeliveryEnvelope.from_message(
        key=key,
        uid="signal-abc123",
        payload=b"<event />",
        phase="immediate",
    )

    assert envelope.key() == key
    assert envelope.payload_xml == "<event />"


@pytest.mark.asyncio
async def test_mark_delivery_success_updates_replay_schedule(
    delivery_service: TakDeliveryService,
    fake_pg,
    message_key_factory,
) -> None:
    key = message_key_factory()

    await delivery_service._mark_delivery_success(
        key=key,
        phase="immediate",
        response_text="Validated and queued for TAK delivery.",
    )

    assert len(fake_pg.delivered_calls) == 1
    assert (
        fake_pg.delivered_calls[0]["response_text"]
        == "Validated and queued for TAK delivery."
    )


@pytest.mark.asyncio
async def test_mark_delivery_failure_marks_message_failed(
    delivery_service: TakDeliveryService,
    fake_pg,
    message_key_factory,
) -> None:
    key = message_key_factory()

    await delivery_service._mark_delivery_failure(
        key=key,
        phase="background-retry",
        error_text="connect failed",
        response_text="Validated and queued for TAK delivery.",
    )

    assert len(fake_pg.failed_calls) == 1
    assert fake_pg.failed_calls[0]["error_text"] == "connect failed"


@pytest.mark.asyncio
async def test_expired_immediate_delivery_is_marked_done(
    delivery_service: TakDeliveryService,
    fake_pg,
    message_key_factory,
    expired_row,
) -> None:
    key = message_key_factory()
    envelope = TakDeliveryEnvelope.from_message(
        key=key,
        uid="signal-abc123",
        payload=b"<event />",
        phase="immediate",
    )

    async def fake_get_processed_message(*, key):
        return expired_row

    fake_pg.get_processed_message = fake_get_processed_message  # type: ignore[method-assign]

    await delivery_service._deliver_envelope(envelope)

    assert len(fake_pg.done_calls) == 1
    assert not fake_pg.delivered_calls
