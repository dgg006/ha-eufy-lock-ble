"""Frame / TLV / payload encoding against real frames exchanged with a lock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.eufy_lock_ble.eufy_ble import protocol
from custom_components.eufy_lock_ble.eufy_ble.protocol import Cmd, ProtocolError, Tag

# Real frames exchanged with a lock (bodies are ciphertext, irrelevant here).
HEARTBEAT_FRAME = bytes.fromhex(
    "ff09290011ff03c021f59ba9e91f73ac35324b8abb5e93efdb0aaf0af697b710e7a2ce2ead0ca6afdd"
)
STATUS_FRAME = bytes.fromhex(
    "ff09290011ff11c021f59ba9e91f73ac35324b8abb5e93efdb0aaf0af697b710e7a2ce2ead0ca6afcf"
)
ONOFF_RESPONSE = bytes.fromhex("ff091900113208c0c875eccd408387d9e3abfe56b720618596")
NOTIFY_FRAME = bytes.fromhex("ff091900113212c0ddefb205dba5ecd210ba7d4952a14d06fd")


@pytest.mark.parametrize("raw", [HEARTBEAT_FRAME, STATUS_FRAME, ONOFF_RESPONSE, NOTIFY_FRAME])
def test_parse_real_frames(raw: bytes) -> None:
    frame = protocol.parse_frame(raw)
    assert len(raw) == int.from_bytes(raw[2:4], "little")
    assert frame.body == raw[8:-1]
    # re-encoding must reproduce the frame byte for byte
    assert protocol.build_frame(frame.cmd, frame.body, frame.direction) == raw


def test_real_frame_fields() -> None:
    hb = protocol.parse_frame(HEARTBEAT_FRAME)
    assert hb.cmd == Cmd.HEARTBEAT and hb.direction == protocol.DIR_REQUEST and not hb.is_response
    st = protocol.parse_frame(STATUS_FRAME)
    assert st.cmd == Cmd.QUERY_STATUS
    # heartbeat and status carry the same request TLV -> same ciphertext
    assert hb.body == st.body
    resp = protocol.parse_frame(ONOFF_RESPONSE)
    assert resp.cmd == Cmd.ON_OFF and resp.is_response
    assert protocol.parse_frame(NOTIFY_FRAME).cmd == Cmd.STATE_NOTIFY


def test_parse_frame_rejects_garbage() -> None:
    with pytest.raises(ProtocolError, match="short"):
        protocol.parse_frame(b"\xff\x09\x05\x00")
    with pytest.raises(ProtocolError, match="magic"):
        protocol.parse_frame(b"\x00" + HEARTBEAT_FRAME[1:])
    bad_size = bytearray(HEARTBEAT_FRAME)
    bad_size[2] ^= 1
    with pytest.raises(ProtocolError, match="size"):
        protocol.parse_frame(bytes(bad_size))
    bad_xor = bytearray(HEARTBEAT_FRAME)
    bad_xor[-1] ^= 1
    with pytest.raises(ProtocolError, match="checksum"):
        protocol.parse_frame(bytes(bad_xor))


def test_tlv_roundtrip() -> None:
    fields = [(Tag.A1, b"\x00\x00"), (Tag.A2, b"\x01"), (Tag.A4, b"")]
    encoded = protocol.build_tlv(fields)
    assert encoded == bytes.fromhex("a1020000a20101a400")
    assert protocol.parse_tlv(encoded) == {0xA1: b"\x00\x00", 0xA2: b"\x01", 0xA4: b""}
    with pytest.raises(ProtocolError):
        protocol.parse_tlv(b"\xa1\x05\x00")


def test_identity_timestamp_and_payload() -> None:
    now = datetime(2026, 9, 7, 19, 46, 6, tzinfo=timezone(timedelta(hours=-4)))
    assert protocol.identity_timestamp(now) == bytes.fromhex("1a0907132e0684")
    admin = bytes(range(20))
    pub = bytes(range(64))
    ident = protocol.build_identity(admin, pub, now)
    tlv = protocol.parse_tlv(ident)
    assert tlv[Tag.A1] == admin
    assert tlv[Tag.A2] == bytes.fromhex("1a0907132e0684")
    assert tlv[Tag.A3] == pub
    assert tlv[Tag.A4] == bytes.fromhex("40380000")
    assert len(ident) == 2 + 20 + 2 + 7 + 2 + 64 + 2 + 4
    with pytest.raises(ProtocolError):
        protocol.build_identity(b"\x00" * 19, pub, now)


def test_on_off_payload_matches_reference() -> None:
    # reference plaintext: a1 02 0000 | a2 01 00 (unlock) | a3 04 <ts LE> | a4 06 "abc***"
    ts = int.from_bytes(bytes.fromhex("a1b2c3d4"), "little")
    tlv = protocol.build_on_off(0, lock=False, ts=ts, nickname="abc***")
    assert tlv.hex() == "a1020000a20100a304a1b2c3d4a4066162632a2a2a"
    assert protocol.build_on_off(0, lock=True, ts=ts, nickname="")[5] == 0x01


def test_admin_request() -> None:
    admin = bytes(range(20))
    tlv = protocol.build_admin_request(admin, 0x01020304)
    assert tlv == bytes([0xA1, 20]) + admin + bytes([0xA2, 4, 0x04, 0x03, 0x02, 0x01])


def test_verify_response_parsing() -> None:
    key = bytes(range(16))
    plaintext = bytes([0]) + protocol.build_tlv(
        [
            (Tag.A1, key),
            (Tag.A2, bytes.fromhex("0a1b2c3d4e5f")),
            (Tag.A3, b"2.0.4.2\x00\x00\x00"),
            (Tag.A4, b"T8510Q1500000001"),
            (Tag.A5, b"1.2\x00\x00"),
        ]
    )
    info = protocol.parse_verify_response(plaintext)
    assert info.session_key == key
    assert info.firmware == "2.0.4.2"
    assert info.hardware == "1.2"
    assert info.serial == "T8510Q1500000001"
    assert info.bt_mac == bytes.fromhex("0a1b2c3d4e5f")
    with pytest.raises(ProtocolError, match="refused"):
        protocol.parse_verify_response(b"\x42" + plaintext[1:])
    with pytest.raises(ProtocolError, match="session key"):
        protocol.parse_verify_response(b"\x00\xa1\x02\x00\x00")


def test_state_parsing() -> None:
    assert protocol.parse_lock_state(bytes.fromhex("00a1010aa20104")) is True
    assert protocol.parse_lock_state(bytes.fromhex("00a1010aa20103")) is False
    assert protocol.parse_lock_state(bytes.fromhex("00a1010aa20107")) is None
    assert protocol.parse_lock_state(bytes.fromhex("00")) is None
    # A1 is the battery percentage
    status = protocol.parse_status(bytes.fromhex("00a1010aa20104"))
    assert status == protocol.LockStatus(locked=True, battery=10)
    assert protocol.parse_status(bytes.fromhex("00a10164a20103")).battery == 100
    assert protocol.parse_status(bytes.fromhex("00a101ffa20103")).battery is None
    assert protocol.parse_status(bytes.fromhex("00a20103")).battery is None


def test_decode_params_names_and_types() -> None:
    sample = bytes.fromhex(
        "00a10102a20100a3023c00a40100a5021700a6020600a70101a80101a902b400aa0105ab0101ac0100"
    )
    decoded = protocol.decode_params(protocol.parse_params(sample))
    assert decoded == {
        "sound_volume": "medium",
        "auto_lock": False,
        "auto_lock_timer": 60,
        "auto_lock_schedule": False,
        "auto_lock_schedule_start": "23:00",
        "auto_lock_schedule_end": "06:00",
        "one_touch_lock": True,
        "wrong_try_protect": True,
        "wrong_try_lockdown": 180,
        "wrong_try_attempts": 5,
        "scramble_passcode": True,
        "param_ac": 0,
    }


def test_params_parsing() -> None:
    sample = bytes.fromhex(
        "00a10102a20100a3023c00a40100a5021700a6020600a70101a80101a902b400aa0105ab0101ac0100"
    )
    params = protocol.parse_params(sample)
    assert protocol.param_u16(params, 0xA3) == 60
    assert protocol.param_u16(params, 0xA5) == 23
    assert protocol.param_u16(params, 0xA9) == 180
    assert protocol.param_u16(params, 0xA1) == 2
    assert protocol.param_u16(params, 0xAD) is None
