"""Read-only Anker BLE history records, independent of Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import struct
from typing import Any

from .protocol import ProtocolError, Tag, build_tlv, epoch_le

# The app shares this vocabulary across models; a code here is not a claim that
# every lock supports the corresponding hardware or feature.
EVENT_NAMES = {
    257: "manual_unlock", 258: "automatic_unlock", 259: "passcode_unlock",
    260: "fingerprint_unlock", 261: "app_unlock", 262: "manual_lock",
    263: "one_touch_lock", 264: "app_lock", 265: "automatic_lock",
    266: "passcode_lock", 267: "fingerprint_lock", 268: "temporary_passcode_lock",
    269: "temporary_passcode_unlock", 270: "nfc_unlock", 271: "nfc_lock",
    513: "low_battery", 514: "very_low_battery", 515: "multiple_errors",
    516: "lock_offline", 517: "unlock_mechanical_anomaly", 518: "tamper",
    519: "lock_mechanical_anomaly", 520: "door_left_open", 521: "door_tamper",
    522: "door_state_error", 525: "passage_mode_on", 526: "passage_mode_off",
    527: "privacy_mode_on", 528: "privacy_mode_off", 530: "palm_unlock",
    531: "palm_lock", 532: "one_key_unlock", 533: "one_key_lock",
    534: "single_battery_low", 535: "single_battery_empty", 536: "main_battery_low",
    537: "main_battery_empty", 538: "face_unlock", 539: "face_lock",
    544: "power_saving_on", 545: "power_saving_off", 546: "palm_vein_register",
    547: "palm_vein_identify", 548: "palm_vein_radar_gear", 549: "matter_passcode_unlock",
    550: "matter_app_unlock", 551: "matter_passcode_lock", 552: "matter_app_lock",
    554: "backup_battery_abnormal", 555: "backup_battery_low",
    556: "palm_vein_multiple_failures", 557: "palm_vein_rain_mode_on",
    558: "palm_vein_rain_mode_off",
}


@dataclass(frozen=True, slots=True)
class LockRecord:
    """One fixed-size record; retain unknown bytes for local inspection."""

    timestamp: int
    code: int
    user_id: str
    nickname: str
    raw: bytes

    @property
    def event_type(self) -> str:
        """Stable type, including an explicit fallback for undocumented events."""
        return EVENT_NAMES.get(self.code, "unknown")

    @property
    def action(self) -> str | None:
        """Lock/unlock only when the event definition explicitly says so."""
        if self.event_type.endswith("_unlock"):
            return "unlock"
        if self.event_type.endswith("_lock"):
            return "lock"
        return None

    @property
    def method(self) -> str | None:
        """Do not infer which person used a mechanical key or thumb-turn."""
        action = self.action
        return self.event_type[:-(len(action) + 1)] if action else None

    @property
    def record_id(self) -> str:
        """Deduplicate overlapping pages without depending on padding bytes."""
        value = f"{self.timestamp}:{self.code}:{self.user_id}:{self.nickname}"
        return hashlib.sha256(value.encode()).hexdigest()[:24]

    def as_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        """Local HA attributes; never include these personal data in diagnostics."""
        result = {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "occurred_at": datetime.fromtimestamp(self.timestamp, UTC).isoformat(),
            "event_code": self.code,
            "event_type": self.event_type,
            "action": self.action,
            "method": self.method,
            "user_id": self.user_id or None,
            "user_name": self.nickname or None,
        }
        if include_raw:
            result["raw"] = self.raw.hex()
        return result


@dataclass(frozen=True, slots=True)
class HistoryPage:
    """A result of 0x00 continues; 0x41 marks the final page (possibly nonempty)."""

    records: tuple[LockRecord, ...]
    complete: bool


def build_history_request(index: int, timestamp: int) -> bytes:
    """Use the app's record index and UTC epoch, without any delete/ack command."""
    if not 0 <= index <= 0xFFFF:
        raise ProtocolError("history index out of range")
    return build_tlv([(Tag.A1, struct.pack("<H", index)), (Tag.A2, epoch_le(timestamp))])


def parse_history_page(plaintext: bytes) -> HistoryPage:
    """Decode 40-byte TLV records; reject truncation rather than inventing fields."""
    if not plaintext or plaintext[0] not in (0x00, 0x41):
        raise ProtocolError("invalid history result")
    body = plaintext[1:]
    if len(body) % 40:
        raise ProtocolError("truncated history record")
    records = []
    for start in range(0, len(body), 40):
        raw = body[start:start + 40]
        fields: dict[int, bytes] = {}
        offset = 0
        while offset < 40 and raw[offset] != 0:
            if offset + 2 > 40:
                raise ProtocolError("truncated history TLV header")
            tag, size = raw[offset:offset + 2]
            offset += 2
            if offset + size > 40 or tag in fields:
                raise ProtocolError("invalid history TLV length or repeated tag")
            fields[tag] = raw[offset:offset + size]
            offset += size
        if len(fields.get(Tag.A1, b"")) != 4 or len(fields.get(Tag.A2, b"")) != 2:
            raise ProtocolError("history record missing timestamp or event type")
        records.append(LockRecord(
            timestamp=int.from_bytes(fields[Tag.A1], "little"),
            code=int.from_bytes(fields[Tag.A2], "big"),
            user_id=fields.get(Tag.A3, b"").hex(),
            nickname=fields.get(Tag.A4, b"").split(b"\0", 1)[0].decode("utf-8", "replace").strip(),
            raw=raw,
        ))
    return HistoryPage(tuple(records), plaintext[0] == 0x41)
