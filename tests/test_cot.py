def test_build_uid_is_stable_and_prefixed(cot_service, message_key_factory) -> None:
    key = message_key_factory()

    uid1 = cot_service.build_uid(key)
    uid2 = cot_service.build_uid(key)

    assert uid1 == uid2
    assert uid1.startswith("signal-")
    assert len(uid1) == len("signal-") + 20


def test_build_cot_xml_contains_target_and_coordinates(
    cot_service,
    parsed_payload_factory,
) -> None:
    payload = parsed_payload_factory()

    xml = cot_service.build_cot_xml(
        uid="signal-abc123",
        payload=payload,
        stale_seconds=60,
    ).decode("utf-8")

    assert 'uid="signal-abc123"' in xml
    assert 'lat="48.563123"' in xml
    assert 'lon="39.8917"' in xml
    assert 'callsign="tank"' in xml
    assert 'how="h-e"' in xml


def test_fallback_cot_type_uses_o_in_second_segment(cot_service) -> None:
    match = cot_service.catalog_service.resolve_cot_type("totally unknown target label")

    assert match.entry.cot == "a-o-G-U"
