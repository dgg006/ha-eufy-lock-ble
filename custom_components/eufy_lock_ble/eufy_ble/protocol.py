"""Wire format of eufy BLE locks: frames, TLVs and command payloads.

Every frame on the FFE1 characteristic looks like::

    FF 09 | size (u16 LE) | 0x11 | dir | cmd | 0xC0 | body | xor8

``size`` counts the whole frame including the ``FF 09`` header and the
trailing XOR byte.  ``dir`` is ``0xFF`` central->lock and ``0x32`` lock->central.
``xor8`` is the XOR of every preceding byte.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
import struct
from typing import Any

FRAME_MAGIC = b"\xff\x09"
FRAME_VERSION = 0x11
FRAME_FLAG = 0xC0
DIR_REQUEST = 0xFF
DIR_RESPONSE = 0x32
MIN_FRAME_LEN = 9

# GATT
SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_COMMAND_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHAR_AUX_NOTIFY_UUID = "0000b003-0000-1000-8000-00805f9b34fb"

# Result byte at the start of decrypted responses.
RESULT_OK = 0x00
RESULT_REFUSED = 0x42

# Bolt state encoding shared by QUERY_STATUS and STATE_NOTIFY (TLV tag A2).
STATE_UNLOCKED = 0x03
STATE_LOCKED = 0x04


class Cmd(IntEnum):
    """Command byte of a frame."""

    VERIFY = 0x02
    HEARTBEAT = 0x03
    ON_OFF = 0x08
    QUERY_STATUS = 0x11
    STATE_NOTIFY = 0x12
    RECORD_LOG = 0x18
    GET_PARAMS = 0x21


class Tag(IntEnum):
    """TLV tags (``a``..``l`` in the vendor SDK)."""

    A1 = 0xA1
    A2 = 0xA2
    A3 = 0xA3
    A4 = 0xA4
    A5 = 0xA5
    A6 = 0xA6
    A7 = 0xA7
    A8 = 0xA8
    A9 = 0xA9
    AA = 0xAA
    AB = 0xAB
    AC = 0xAC


class ProtocolError(ValueError):
    """Malformed frame or payload."""


def xor8(data: bytes) -> int:
    """XOR of all bytes."""
    result = 0
    for value in data:
        result ^= value
    return result


@dataclass(frozen=True, slots=True)
class Frame:
    """A parsed frame."""

    cmd: int
    direction: int
    body: bytes

    @property
    def is_response(self) -> bool:
        return self.direction == DIR_RESPONSE


def build_frame(cmd: int, body: bytes, direction: int = DIR_REQUEST) -> bytes:
    """Frame ``body`` for ``cmd``."""
    inner = bytes([FRAME_VERSION, direction, cmd, FRAME_FLAG]) + body
    size = len(FRAME_MAGIC) + 2 + len(inner) + 1
    if size > 0xFFFF:
        raise ProtocolError("frame too large")
    head = FRAME_MAGIC + struct.pack("<H", size) + inner
    return head + bytes([xor8(head)])


def parse_frame(data: bytes) -> Frame:
    """Parse and validate a frame; raises :class:`ProtocolError` if invalid."""
    data = bytes(data)
    if len(data) < MIN_FRAME_LEN:
        raise ProtocolError(f"frame too short ({len(data)} bytes)")
    if data[:2] != FRAME_MAGIC:
        raise ProtocolError("bad frame magic")
    size = struct.unpack_from("<H", data, 2)[0]
    if size != len(data):
        raise ProtocolError(f"size field {size} != frame length {len(data)}")
    if data[4] != FRAME_VERSION or data[7] != FRAME_FLAG:
        raise ProtocolError("unexpected frame version/flag")
    if xor8(data[:-1]) != data[-1]:
        raise ProtocolError("checksum mismatch")
    return Frame(cmd=data[6], direction=data[5], body=data[8:-1])


def build_tlv(fields: list[tuple[int, bytes]]) -> bytes:
    """Serialise ``[(tag, value), ...]`` as ``tag len value`` triples."""
    out = bytearray()
    for tag, value in fields:
        if len(value) > 0xFF:
            raise ProtocolError(f"TLV value for tag {tag:#x} too long")
        out += bytes([tag, len(value)]) + value
    return bytes(out)


def parse_tlv(data: bytes) -> dict[int, bytes]:
    """Parse ``tag len value`` triples into a dict (later duplicates win)."""
    out: dict[int, bytes] = {}
    i = 0
    while i + 2 <= len(data):
        tag, length = data[i], data[i + 1]
        if i + 2 + length > len(data):
            raise ProtocolError("truncated TLV")
        out[tag] = data[i + 2 : i + 2 + length]
        i += 2 + length
    return out


def split_result(plaintext: bytes) -> tuple[int, dict[int, bytes]]:
    """Split a decrypted response into ``(result_byte, tlv_fields)``."""
    if not plaintext:
        raise ProtocolError("empty response")
    return plaintext[0], parse_tlv(plaintext[1:])


def identity_timezone_seconds(now: datetime) -> int:
    """Return the local UTC offset used by the official Android app."""
    offset = now.utcoffset()
    if offset is None:
        raise ProtocolError("identity time must include a timezone")
    return int(offset.total_seconds())


def identity_timestamp(now: datetime) -> bytes:
    """Build local calendar fields and the app's signed-hour timezone byte."""
    seconds = identity_timezone_seconds(now)
    zone_byte = abs(seconds) // 3600 + (0x80 if seconds < 0 else 0)
    return bytes([
        (now.year - 2000) & 0xFF, now.month, now.day,
        now.hour, now.minute, now.second, zone_byte,
    ])


def epoch_le(ts: int) -> bytes:
    """u32 little-endian epoch seconds."""
    return struct.pack("<I", int(ts) & 0xFFFFFFFF)


def build_identity(
    admin_user_id: bytes,
    session_pub_xy: bytes,
    now: datetime,
    a4: bytes | None = None,
) -> bytes:
    """Plaintext of the VERIFY handshake (encrypted with ECIES to the lock)."""
    if len(admin_user_id) != 20:
        raise ProtocolError("admin_user_id must be 20 raw bytes")
    if len(session_pub_xy) != 64:
        raise ProtocolError("session public key must be raw X||Y (64 bytes)")
    if a4 is None:
        a4 = struct.pack("<i", -identity_timezone_seconds(now))
    return build_tlv(
        [
            (Tag.A1, admin_user_id),
            (Tag.A2, identity_timestamp(now)),
            (Tag.A3, session_pub_xy),
            (Tag.A4, a4),
        ]
    )


def build_admin_request(admin_user_id: bytes, ts: int) -> bytes:
    """TLV used by QUERY_STATUS, GET_PARAMS and HEARTBEAT requests."""
    if len(admin_user_id) != 20:
        raise ProtocolError("admin_user_id must be 20 raw bytes")
    return build_tlv([(Tag.A1, admin_user_id), (Tag.A2, epoch_le(ts))])


def build_on_off(short_user_id: int, lock: bool, ts: int, nickname: str) -> bytes:
    """TLV of the ON_OFF (lock/unlock) command."""
    return build_tlv(
        [
            (Tag.A1, struct.pack(">H", short_user_id & 0xFFFF)),
            (Tag.A2, b"\x01" if lock else b"\x00"),
            (Tag.A3, epoch_le(ts)),
            (Tag.A4, nickname.encode("utf-8")),
        ]
    )


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Fields returned by the lock in the VERIFY response."""

    session_key: bytes
    bt_mac: bytes | None
    firmware: str | None
    serial: str | None
    hardware: str | None


def _cstr(value: bytes | None) -> str | None:
    if value is None:
        return None
    return value.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def parse_verify_response(plaintext: bytes) -> SessionInfo:
    """Parse the decrypted VERIFY response ``00 A1 10 <K> A2 06 <mac> A3 <fw> A4 <sn> A5 <hw>``."""
    result, tlv = split_result(plaintext)
    if result != RESULT_OK:
        raise ProtocolError(f"lock refused identity (result {result:#04x})")
    key = tlv.get(Tag.A1)
    if key is None or len(key) != 16:
        raise ProtocolError("no 16-byte session key in VERIFY response")
    return SessionInfo(
        session_key=key,
        bt_mac=tlv.get(Tag.A2),
        firmware=_cstr(tlv.get(Tag.A3)),
        serial=_cstr(tlv.get(Tag.A4)),
        hardware=_cstr(tlv.get(Tag.A5)),
    )


@dataclass(frozen=True, slots=True)
class LockStatus:
    """Decoded QUERY_STATUS / STATE_NOTIFY payload."""

    locked: bool | None
    battery: int | None


def parse_status(plaintext: bytes) -> LockStatus:
    """Decode ``00 A1 01 <battery %> A2 01 <state>`` from QUERY_STATUS / STATE_NOTIFY.

    ``A1`` is the battery level in percent, ``A2`` the bolt state (``04`` locked,
    ``03`` unlocked).  Unknown values decode to ``None``.
    """
    _result, tlv = split_result(plaintext)
    state = tlv.get(Tag.A2)
    locked: bool | None = None
    if state == bytes([STATE_LOCKED]):
        locked = True
    elif state == bytes([STATE_UNLOCKED]):
        locked = False
    raw_batt = tlv.get(Tag.A1)
    battery = raw_batt[0] if raw_batt and len(raw_batt) == 1 and raw_batt[0] <= 100 else None
    return LockStatus(locked=locked, battery=battery)


def parse_lock_state(plaintext: bytes) -> bool | None:
    """Bolt state only; see :func:`parse_status`."""
    return parse_status(plaintext).locked


def parse_params(plaintext: bytes) -> dict[int, bytes]:
    """Decode a GET_PARAMS response into ``{tag: raw_value}``."""
    _result, tlv = split_result(plaintext)
    return tlv


def param_u16(params: dict[int, bytes], tag: int) -> int | None:
    """Read a little-endian integer parameter (1 or 2 bytes)."""
    raw = params.get(tag)
    if raw is None or not 1 <= len(raw) <= 2:
        return None
    return int.from_bytes(raw, "little")


# GET_PARAMS tag meanings (same layout the eufy bridge relays to the cloud client).
PARAM_NAMES: dict[int, str] = {
    0xA1: "sound_volume",
    0xA2: "auto_lock",
    0xA3: "auto_lock_timer",
    0xA4: "auto_lock_schedule",
    0xA5: "auto_lock_schedule_start",
    0xA6: "auto_lock_schedule_end",
    0xA7: "one_touch_lock",
    0xA8: "wrong_try_protect",
    0xA9: "wrong_try_lockdown",
    0xAA: "wrong_try_attempts",
    0xAB: "scramble_passcode",
    0xAC: "param_ac",
}
_BOOL_TAGS = {0xA2, 0xA4, 0xA7, 0xA8, 0xAB}
_TIME_TAGS = {0xA5, 0xA6}
SOUND_LEVELS = {0: "silent", 1: "low", 2: "medium", 3: "high"}


def decode_params(params: dict[int, bytes]) -> dict[str, Any]:
    """Turn the raw parameter block into named, typed values."""
    out: dict[str, Any] = {}
    for tag, raw in sorted(params.items()):
        name = PARAM_NAMES.get(tag, f"param_{tag:02x}")
        if tag in _TIME_TAGS and len(raw) == 2:
            out[name] = f"{raw[0]:02d}:{raw[1]:02d}"
        elif tag in _BOOL_TAGS and len(raw) == 1:
            out[name] = bool(raw[0])
        elif tag == 0xA1 and len(raw) == 1:
            out[name] = SOUND_LEVELS.get(raw[0], raw[0])
        else:
            value = param_u16(params, tag)
            out[name] = value if value is not None else raw.hex()
    return out
