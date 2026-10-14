"""A software model of the lock side of the protocol, for tests.

It owns the lock's static P-256 key, accepts VERIFY handshakes, hands out a
session key and answers commands the way firmware 2.0.4.2 does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import os
import struct

from custom_components.eufy_lock_ble.eufy_ble import crypto, protocol
from custom_components.eufy_lock_ble.eufy_ble.protocol import Cmd, Tag

ADMIN_HEX = "7e" * 20
SERIAL = "T8510Q1500000001"
NICK = "abc***"


class FakeLock:
    """Simulated lock; ``on_notify`` receives frames the lock sends."""

    def __init__(
        self,
        *,
        serial: str = SERIAL,
        admin_hex: str = ADMIN_HEX,
        locked: bool = True,
        clock: Callable[[], datetime] | None = None,
        max_clock_skew: int = 120,
    ) -> None:
        self.priv = crypto.generate_keypair()
        self.public_key_hex = crypto.public_key_xy(self.priv).hex()
        self.serial = serial
        self.admin = bytes.fromhex(admin_hex)
        self.locked = locked
        self.battery = 10
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.max_clock_skew = max_clock_skew
        self.session_key: bytes | None = None
        self.on_notify: Callable[[bytes], None] | None = None
        self.received: list[tuple[int, bytes]] = []  # (cmd, plaintext)
        self.notify_delay = 0.0
        self.refuse_actuation = False
        self.history_records: list[bytes] = []
        self.history_result: int | None = None
        self.history_page_size = 4
        self.params = {
            0xA1: b"\x02",
            0xA2: b"\x00",
            0xA3: b"\x3c\x00",
            0xA4: b"\x00",
            0xA5: b"\x17\x00",
            0xA6: b"\x06\x00",
            0xA7: b"\x01",
            0xA8: b"\x01",
            0xA9: b"\xb4\x00",
            0xAA: b"\x05",
            0xAB: b"\x01",
            0xAC: b"\x00",
        }

    # -------------------------------------------------------------- helpers
    def _iv(self) -> bytes:
        return crypto.serial_iv(self.serial)

    def _notify(self, cmd: int, body: bytes) -> None:
        assert self.on_notify is not None
        self.on_notify(protocol.build_frame(cmd, body, protocol.DIR_RESPONSE))

    def _reply(self, cmd: int, plaintext: bytes) -> None:
        assert self.session_key is not None
        self._notify(cmd, crypto.aes_cbc_encrypt(self.session_key, self._iv(), plaintext))

    def state_plaintext(self) -> bytes:
        state = protocol.STATE_LOCKED if self.locked else protocol.STATE_UNLOCKED
        return bytes([protocol.RESULT_OK]) + protocol.build_tlv(
            [(Tag.A1, bytes([self.battery])), (Tag.A2, bytes([state]))]
        )

    def push_state(self) -> None:
        """Unsolicited STATE_NOTIFY (e.g. someone turned the thumb-turn)."""
        self._reply(Cmd.STATE_NOTIFY, self.state_plaintext())

    # ----------------------------------------------------------------- input
    def write(self, data: bytes) -> None:
        """Called with what the central writes on FFE1."""
        frame = protocol.parse_frame(data)
        assert frame.direction == protocol.DIR_REQUEST
        if frame.cmd == Cmd.VERIFY:
            self._handle_verify(frame.body)
            return
        assert self.session_key is not None, "command before handshake"
        plaintext = crypto.aes_cbc_decrypt(self.session_key, self._iv(), frame.body)
        self.received.append((frame.cmd, plaintext))
        handler = {
            Cmd.HEARTBEAT: self._handle_admin_ok,
            Cmd.QUERY_STATUS: self._handle_status,
            Cmd.GET_PARAMS: self._handle_params,
            Cmd.ON_OFF: self._handle_on_off,
            Cmd.RECORD_LOG: self._handle_history,
        }[frame.cmd]
        handler(frame.cmd, plaintext)

    def _handle_verify(self, blob: bytes) -> None:
        identity = protocol.parse_tlv(crypto.ecies_decrypt(blob, self.priv))
        assert identity[Tag.A1] == self.admin, "wrong admin id"
        ts = identity[Tag.A2]
        now = self.clock()
        offset = int(now.utcoffset().total_seconds())
        assert len(ts) == 7 and ts[6] == abs(offset) // 3600 + (128 if offset < 0 else 0)
        sent = datetime(2000 + ts[0], ts[1], ts[2], ts[3], ts[4], ts[5], tzinfo=now.tzinfo)
        self.clock_skew = abs((now - sent).total_seconds())
        assert identity[Tag.A4] == (-offset).to_bytes(4, "little", signed=True)
        session_pub = crypto.load_public_key(identity[Tag.A3])
        self.session_key = os.urandom(16)
        reply = bytes([protocol.RESULT_OK]) + protocol.build_tlv(
            [
                (Tag.A1, self.session_key),
                (Tag.A2, bytes.fromhex("0a1b2c3d4e5f")),
                (Tag.A3, b"2.0.4.2\x00\x00\x00"),
                (Tag.A4, self.serial.encode()),
                (Tag.A5, b"1.2\x00\x00"),
            ]
        )
        self._notify(Cmd.VERIFY, crypto.ecies_encrypt(reply, session_pub))

    def _check_admin(self, plaintext: bytes) -> None:
        tlv = protocol.parse_tlv(plaintext)
        assert tlv[Tag.A1] == self.admin
        assert len(tlv[Tag.A2]) == 4

    def _handle_admin_ok(self, cmd: int, plaintext: bytes) -> None:
        self._check_admin(plaintext)
        self._reply(cmd, bytes([protocol.RESULT_OK]))

    def _handle_status(self, cmd: int, plaintext: bytes) -> None:
        self._check_admin(plaintext)
        self._reply(cmd, self.state_plaintext())

    def _handle_params(self, cmd: int, plaintext: bytes) -> None:
        self._check_admin(plaintext)
        self._reply(
            cmd,
            bytes([protocol.RESULT_OK]) + protocol.build_tlv(sorted(self.params.items())),
        )

    def _handle_history(self, cmd: int, plaintext: bytes) -> None:
        tlv = protocol.parse_tlv(plaintext)
        assert len(tlv[Tag.A1]) == 2 and len(tlv[Tag.A2]) == 4
        if self.history_result is not None:
            self._reply(cmd, bytes([self.history_result]))
            return
        index = int.from_bytes(tlv[Tag.A1], "little")
        page = self.history_records[index:index + self.history_page_size]
        end = index + len(page) >= len(self.history_records)
        self._reply(cmd, bytes([0x41 if end else 0x00]) + b"".join(page))

    def _handle_on_off(self, cmd: int, plaintext: bytes) -> None:
        tlv = protocol.parse_tlv(plaintext)
        assert len(tlv[Tag.A1]) == 2
        assert tlv[Tag.A2] in (b"\x00", b"\x01")
        assert len(tlv[Tag.A3]) == 4
        struct.unpack("<I", tlv[Tag.A3])
        if self.refuse_actuation or self.clock_skew > self.max_clock_skew:
            self._reply(cmd, bytes([protocol.RESULT_REFUSED]))
            return
        self._reply(cmd, bytes([protocol.RESULT_OK]))
        self.locked = tlv[Tag.A2] == b"\x01"
        if self.notify_delay:
            asyncio.get_running_loop().call_later(self.notify_delay, self.push_state)
        else:
            self.push_state()
