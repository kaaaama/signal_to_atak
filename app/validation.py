from decimal import Decimal

from pydantic import BaseModel, ValidationError, field_validator


class ParsedPayload(BaseModel):
    lon: Decimal
    lat: Decimal
    target: str

    @field_validator("lon")
    @classmethod
    def validate_lon(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Longitude must be a finite decimal number.")
        if not (Decimal("-180") <= value <= Decimal("180")):
            raise ValueError("Longitude must be in range [-180, 180].")
        return value

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Latitude must be a finite decimal number.")
        if not (Decimal("-90") <= value <= Decimal("90")):
            raise ValueError("Latitude must be in range [-90, 90].")
        return value

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("Target description is missing.")
        if len(value) > 120:
            raise ValueError("Target description is too long.")
        return value


def parse_message(text: str) -> ParsedPayload:
    """
    Expected format:
        <longitude> <latitude> <target phrase>

    Examples:
        48.563123 39.8917 tank
        48.563123 39.8917 heavy tank
        48.563123 39.8917 civilian vehicle
        48.563123 39.8917 fixed wing drone
    """
    if text is None or not text.strip():
        raise ValueError("Message is empty.")

    parts = text.strip().split()
    if len(parts) < 3:
        raise ValueError(
            "Expected at least 3 whitespace-separated values: "
            "<longitude> <latitude> <target phrase>."
        )

    lat_raw = parts[0]
    lon_raw = parts[1]
    target = " ".join(parts[2:])

    return ParsedPayload.model_validate(
        {
            "lon": lon_raw,
            "lat": lat_raw,
            "target": target,
        }
    )


def format_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        lines: list[str] = []
        for err in exc.errors():
            field = ".".join(str(x) for x in err["loc"])
            lines.append(f"- {field}: {err['msg']}")
        error_block = "\n".join(lines)
    else:
        error_block = f"- {exc}"

    return (
        "Validation failed.\n"
        "Please send data in this format:\n"
        "<longitude> <latitude> <target phrase>\n\n"
        f"Errors:\n{error_block}"
    )

def format_success_reply(
    payload: ParsedPayload,
    *,
    delivered_to_tak: bool,
    retry_scheduled: bool = False,
) -> str:
    status_line = (
        "Forwarded to TAK/ATAK successfully."
        if delivered_to_tak
        else "Validated, but TAK delivery failed for now. Retry is scheduled."
        if retry_scheduled
        else "Validation successful."
    )

    return (
        f"{status_line}\n"
        f"Latitude: {payload.lat}\n"
        f"Longitude: {payload.lon}\n"
        f"Target description: {payload.target}"
    )