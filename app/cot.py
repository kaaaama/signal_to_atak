from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from lxml import etree

from app.models import MessageKey
from app.validation import ParsedPayload


def cot_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_uid(key: MessageKey, payload: ParsedPayload) -> str:
    digest = hashlib.sha256(
        f"{key.source}|{key.message_timestamp}|{key.raw_text}|{payload.target}".encode("utf-8")
    ).hexdigest()[:20]
    return f"signal-{payload.target}-{digest}"


def build_cot_xml(
    *,
    key: MessageKey,
    payload: ParsedPayload,
    stale_seconds: int,
) -> bytes:
    now = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=stale_seconds)

    event = etree.Element("event")
    event.set("version", "2.0")
    event.set("uid", build_uid(key, payload))
    # Need to clarify expected targets to map types with user input.
    # If anything is possible - we can use LLM to match user input with correct type. Current type - for tanks.
    event.set("type", "a-.-G-E-V-A-T")
    event.set("how", "m-g")
    event.set("time", cot_time(now))
    event.set("start", cot_time(now))
    event.set("stale", cot_time(stale))

    point = etree.SubElement(event, "point")
    point.set("lat", str(payload.lat))
    point.set("lon", str(payload.lon))
    point.set("hae", "0")
    point.set("ce", "10")
    point.set("le", "10")

    detail = etree.SubElement(event, "detail")
    contact = etree.SubElement(detail, "contact")
    contact.set("callsign", payload.target)

    return etree.tostring(
        event,
        xml_declaration=True,
        encoding="UTF-8",
    )