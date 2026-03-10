import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional


WORD_RE = re.compile(r"^[A-Za-z]+$")


@dataclass
class ParsedPayload:
    long: Decimal
    lat: Decimal
    target: str


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    parsed: Optional[ParsedPayload] = None


def _parse_decimal(value: str, field_name: str) -> tuple[Optional[Decimal], list[str]]:
    errors: list[str] = []
    value = value.strip()

    if not value:
        errors.append(f"{field_name}: value is missing.")
        return None, errors

    try:
        parsed = Decimal(value)
    except InvalidOperation:
        errors.append(f"{field_name}: '{value}' is not a valid decimal number.")
        return None, errors

    if not parsed.is_finite():
        errors.append(f"{field_name}: '{value}' must be a finite decimal number.")
        return None, errors

    return parsed, errors


def validate_message(text: str) -> ValidationResult:
    """
    Expected format:
        <decimal> <decimal> <word>

    Example:
        2312.123 123123.12312333 word
    """
    if text is None or not text.strip():
        return ValidationResult(
            is_valid=False,
            errors=["Message is empty."],
        )

    parts = [part for part in text.strip().split(" ")]

    if len(parts) != 3:
        return ValidationResult(
            is_valid=False,
            errors=[
                "Expected exactly 3 comma-separated values: "
                "<decimal> <decimal> <word>."
            ],
        )

    long, lat, target = parts
    errors: list[str] = []

    lat, lat_errors = _parse_decimal(lat, "Latitude")
    long, long_errors = _parse_decimal(long, "Longitude")
    errors.extend(lat_errors)
    errors.extend(long_errors)

    if not target:
        errors.append("Target description is missing.")
    elif not WORD_RE.fullmatch(target):
        errors.append(
            f"Target description: '{target}' must contain only letters (A-Z or a-z)."
        )

    if errors:
        return ValidationResult(
            is_valid=False,
            errors=errors,
        )

    return ValidationResult(
        is_valid=True,
        parsed=ParsedPayload(
            long=long,
            lat=lat,
            target=target,
        ),
    )


def format_reply(result: ValidationResult) -> str:
    if not result.is_valid:
        error_block = "\n".join(f"- {error}" for error in result.errors)
        return (
            "Validation failed.\n"
            "Please send data in this format:\n"
            "<decimal> <decimal> <word>\n\n"
            f"Errors:\n{error_block}"
        )

    assert result.parsed is not None

    return (
        "Validation successful.\n"
        f"Longitude: {result.parsed.long}\n"
        f"Latitude: {result.parsed.lat}\n"
        f"Target description: {result.parsed.target}"
    )
