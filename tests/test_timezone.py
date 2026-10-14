"""Identity bytes match independently captured Android timezone encodings."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.eufy_lock_ble.eufy_ble import protocol


@pytest.mark.parametrize(
    ("offset_minutes", "trailer", "offset_hex"),
    [
        (-180, 0x83, "302a0000"),
        (-240, 0x84, "40380000"),
        (0, 0x00, "00000000"),
        (330, 0x05, "a8b2ffff"),
        (765, 0x0C, "b44cffff"),
        (-210, 0x83, "38310000"),
    ],
)
def test_android_identity_vectors(offset_minutes: int, trailer: int, offset_hex: str) -> None:
    now = datetime(2026, 10, 14, 12, 34, 56, tzinfo=timezone(timedelta(minutes=offset_minutes)))
    fields = protocol.parse_tlv(protocol.build_identity(bytes(range(20)), bytes(range(64)), now))
    assert fields[protocol.Tag.A2] == bytes([26, 10, 14, 12, 34, 56, trailer])
    assert fields[protocol.Tag.A4].hex() == offset_hex


def test_dst_recomputed_at_each_handshake() -> None:
    zone = ZoneInfo("America/New_York")
    for month, trailer, a4 in [(1, 0x85, "50460000"), (7, 0x84, "40380000")]:
        now = datetime(2026, month, 15, 12, tzinfo=zone)
        fields = protocol.parse_tlv(protocol.build_identity(bytes(20), bytes(64), now))
        assert fields[protocol.Tag.A2][-1] == trailer
        assert fields[protocol.Tag.A4].hex() == a4


def test_naive_time_is_not_guessed_from_the_host() -> None:
    with pytest.raises(protocol.ProtocolError, match="timezone"):
        protocol.build_identity(bytes(20), bytes(64), datetime(2026, 10, 14))


def test_explicit_protocol_override_is_preserved() -> None:
    now = datetime(2026, 10, 14, tzinfo=UTC)
    fields = protocol.parse_tlv(protocol.build_identity(bytes(20), bytes(64), now, b"test"))
    assert fields[protocol.Tag.A4] == b"test"


def test_epoch_does_not_shift_with_timezone() -> None:
    instant = datetime(2026, 10, 14, 15, 34, 56, tzinfo=UTC)
    local = instant.astimezone(ZoneInfo("America/Montevideo"))
    assert protocol.build_admin_request(bytes(20), int(local.timestamp())) == (
        protocol.build_admin_request(bytes(20), int(instant.timestamp()))
    )
