"""Validation service tests."""

def test_parse_message_success(validation_service) -> None:
    payload = validation_service.parse_message("48.563123 39.8917 tank")

    assert str(payload.lat) == "48.563123"
    assert str(payload.lon) == "39.8917"
    assert payload.target == "tank"


def test_parse_message_rejects_invalid_latitude(validation_service) -> None:
    message = ""

    try:
        validation_service.parse_message("123.0 39.8917 tank")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected validation failure")

    assert "Latitude must be in range [-90, 90]." in message


def test_format_validation_error_is_user_friendly(validation_service) -> None:
    reply = ""

    try:
        validation_service.parse_message("48.563123")
    except ValueError as exc:
        reply = validation_service.format_validation_error(exc)
    else:
        raise AssertionError("Expected validation failure")

    assert "Validation failed." in reply
    assert "<latitude> <longitude> <target phrase>" in reply
    assert "Expected at least 3 whitespace-separated values" in reply
