from __future__ import annotations

import re
from decimal import Decimal

from pydantic import BaseModel, ValidationError, field_validator


TARGET_RE = re.compile(r"^[A-Za-z]+$")


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
        value = value.strip()
        if not value:
            raise ValueError("Target description is missing.")
        if not TARGET_RE.fullmatch(value):
            raise ValueError(
                "Target description must contain only letters (A-Z or a-z)."
            )
        return value


def parse_message(text: str) -> ParsedPayload:
    """
    Expected format:
        <longitude> <latitude> <word>

    Example:
        35.000000 48.450000 alpha
    """
    if text is None or not text.strip():
        raise ValueError("Message is empty.")

    parts = text.strip().split()
    if len(parts) != 3:
        raise ValueError(
            "Expected exactly 3 whitespace-separated values: "
            "<longitude> <latitude> <word>."
        )

    lat_raw, lon_raw, target = parts

    return ParsedPayload.model_validate(
        {
            "lat": lat_raw,
            "lon": lon_raw,
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
        "<longitude> <latitude> <word>\n\n"
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