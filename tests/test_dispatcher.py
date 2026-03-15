from types import SimpleNamespace

import pytest

@pytest.mark.asyncio
async def test_process_new_message_success_marks_delivery(
    dispatcher,
    fake_pg,
    fake_tak_delivery,
    message_key_factory,
) -> None:
    key = message_key_factory()

    reply = await dispatcher.process_new_message(
        source=key.source,
        message_timestamp=key.message_timestamp,
        raw_text=key.raw_text,
    )

    assert "Validated and queued for TAK delivery." in reply
    assert len(fake_pg.stored_payloads) == 1
    assert len(fake_pg.queued_calls) == 1
    assert not fake_pg.failed_calls
    assert len(fake_tak_delivery.calls) == 1


@pytest.mark.asyncio
async def test_retry_one_queues_background_retry(
    dispatcher,
    fake_pg,
    fake_tak_delivery,
    message_key_factory,
) -> None:
    key = message_key_factory()
    fake_pg.rows[(key.source, key.message_timestamp, key.raw_text)] = SimpleNamespace(
        response_text="Validated and queued for TAK delivery.",
        active_until=None,
    )

    await dispatcher._retry_one(key)

    assert len(fake_pg.queued_calls) == 1
    assert fake_pg.queued_calls[0]["worker_note"] == "Queued for background TAK retry"
    assert len(fake_tak_delivery.calls) == 1
    assert fake_tak_delivery.calls[0]["phase"] == "background-retry"


@pytest.mark.asyncio
async def test_process_new_message_validation_failure_marks_done(
    dispatcher,
    fake_pg,
    fake_tak_delivery,
    faker_instance,
) -> None:
    invalid_message = faker_instance.word()

    reply = await dispatcher.process_new_message(
        source=faker_instance.numerify("+1555#######"),
        message_timestamp=faker_instance.random_int(
            min=1_000_000_000,
            max=9_999_999_999,
        ),
        raw_text=invalid_message,
    )

    assert "Validation failed." in reply
    assert len(fake_pg.done_calls) == 1
    assert not fake_tak_delivery.calls
    assert not fake_pg.stored_payloads


def test_build_uid_uses_message_key_identity(dispatcher, message_key_factory) -> None:
    key = message_key_factory()

    uid = dispatcher.cot_service.build_uid(key)

    assert uid.startswith("signal-")
