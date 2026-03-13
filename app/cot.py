import hashlib
from datetime import datetime, timedelta, timezone

from lxml import etree

from app.cot_type_catalog import resolve_cot_type
from app.models import MessageKey
from app.validation import ParsedPayload


def cot_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_uid(key: MessageKey) -> str:
    digest = hashlib.sha256(
        f"{key.source}|{key.message_timestamp}|{key.raw_text}".encode("utf-8")
    ).hexdigest()[:20]
    return f"signal-{digest}"


def build_cot_xml(
    *,
    uid: str,
    payload: ParsedPayload,
    stale_seconds: int,
) -> bytes:
    now = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=stale_seconds)

    cot_match = resolve_cot_type(payload.target)

    event = etree.Element("event")
    event.set("version", "2.0")
    event.set("uid", uid)
    event.set("type", cot_match.entry.cot)
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

    remarks = etree.SubElement(detail, "remarks")
    remarks.text = (
        f"source_target={payload.target}; "
        f"matched_desc={cot_match.entry.desc}; "
        f"matched_full={cot_match.entry.full}; "
        f"score={cot_match.score}"
    )

    return etree.tostring(
        event,
        xml_declaration=True,
        encoding="UTF-8",
    )