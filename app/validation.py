from decimal import Decimal

from pydantic import BaseModel, ValidationError, field_validator


class ParsedPayload(BaseModel):
    """Parsed and validated payload from a Signal message.

    Contains longitude, latitude, and target description.
    """
    lon: Decimal
    lat: Decimal
    target: str

    @field_validator("lon")
    @classmethod
    def validate_lon(cls, value: Decimal) -> Decimal:
        """Validate longitude is within [-180, 180] and finite."""
        if not value.is_finite():
            raise ValueError("Longitude must be a finite decimal number.")
        if not (Decimal("-180") <= value <= Decimal("180")):
            raise ValueError("Longitude must be in range [-180, 180].")
        return value

    @field_validator("lat")
    @classmethod
    def validate_lat(cls, value: Decimal) -> Decimal:
        """Validate latitude is within [-90, 90] and finite."""
        if not value.is_finite():
            raise ValueError("Latitude must be a finite decimal number.")
        if not (Decimal("-90") <= value <= Decimal("90")):
            raise ValueError("Latitude must be in range [-90, 90].")
        return value

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        """Validate target description is present and not too long."""
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("Target description is missing.")
        if len(value) > 120:
            raise ValueError("Target description is too long.")
        return value


class ValidationService:
    """Service for parsing and formatting Signal messages."""

    def parse_message(self, text: str) -> ParsedPayload:
        """Parse and validate a Signal message text.

        Expected format: <longitude> <latitude> <target phrase>

        Args:
            text: The message text to parse.

        Returns:
            ParsedPayload with validated data.

        Raises:
            ValueError: If parsing or validation fails.
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

    def format_validation_error(self, exc: Exception) -> str:
        """Format a validation error into a user-friendly string.

        Args:
            exc: The exception to format.

        Returns:
            Formatted error message.
        """
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
        self,
        payload: ParsedPayload,
        *,
        delivered_to_tak: bool,
        retry_scheduled: bool = False,
    ) -> str:
        """Format a success reply with payload details and delivery status.

        Args:
            payload: The parsed payload.
            delivered_to_tak: Whether delivery to TAK succeeded.
            retry_scheduled: Whether a retry is scheduled.

        Returns:
            Formatted success message.
        """
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
