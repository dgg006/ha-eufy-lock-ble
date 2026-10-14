"""Session state machine for an eufy BLE lock, independent of the BLE stack.

A :class:`LockSession` is bound to one BLE connection.  The owner feeds every
notification received on the command characteristic into :meth:`LockSession.feed`
and provides a :class:`Transport` whose ``write`` sends bytes to that same
characteristic (write-without-response).  The session then drives:

1. ``handshake()`` — VERIFY: ECIES-wrap our identity + session public key to the
   lock's static key; the lock answers with the AES session key ``K``.
2. authenticated commands (``query_status``, ``get_params``, ``set_locked``,
   ``heartbeat``) — AES-128-CBC with ``K`` and the serial as IV.
3. unsolicited ``STATE_NOTIFY`` frames, decoded and forwarded to ``on_state``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
import logging
import re
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurvePrivateKey,
    EllipticCurvePublicKey,
)

from . import crypto, protocol
from .history import HistoryPage, build_history_request, parse_history_page
from .protocol import Cmd, Frame, LockStatus, ProtocolError, SessionInfo

_LOGGER = logging.getLogger(__name__)

DEFAULT_RESPONSE_TIMEOUT = 6.0
HEARTBEAT_INTERVAL = 15.0

_HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
_HEX128 = re.compile(r"^[0-9a-fA-F]{128}$")
_HEX130 = re.compile(r"^04[0-9a-fA-F]{128}$")
_SERIAL = re.compile(r"^[A-Za-z0-9]{8,16}$")


class Transport(Protocol):
    """Minimal write interface the session needs."""

    async def write(self, data: bytes) -> None:  # pragma: no cover - protocol
        """Write ``data`` to the command characteristic (write without response)."""


class SessionError(Exception):
    """Base class for session failures."""


class LockTimeoutError(SessionError):
    """The lock did not answer in time."""


class LockRefusedError(SessionError):
    """The lock answered with a non-zero result byte."""

    def __init__(self, cmd: int, result: int) -> None:
        super().__init__(f"lock refused command {cmd:#04x} (result {result:#04x})")
        self.cmd = cmd
        self.result = result


class NotAuthenticatedError(SessionError):
    """A command was issued before a successful handshake."""


class InvalidCredential(ValueError):
    """A credential string is malformed; ``field`` names the offending one."""

    def __init__(self, field_name: str, code: str) -> None:
        super().__init__(f"{field_name}: {code}")
        self.field = field_name
        self.code = code


@dataclass(frozen=True, slots=True)
class LockCredentials:
    """Per-device secrets obtained once from the eufy account."""

    serial: str
    admin_user_id: bytes
    lock_public_key: EllipticCurvePublicKey
    short_user_id: int = 0
    nickname: str = ""
    identity_a4: bytes | None = None

    @classmethod
    def from_strings(
        cls,
        serial: str,
        admin_user_id: str,
        lock_public_key: str,
        short_user_id: int | str = 0,
        nickname: str = "",
    ) -> LockCredentials:
        """Validate user-supplied strings and build credentials.

        Raises :class:`InvalidCredential` with a stable ``code`` suitable for
        config-flow error keys.
        """
        serial = serial.strip()
        if not _SERIAL.match(serial):
            raise InvalidCredential("serial", "invalid_serial")
        admin = admin_user_id.strip()
        if not _HEX40.match(admin):
            raise InvalidCredential("admin_user_id", "invalid_admin_user_id")
        pub = lock_public_key.strip().replace(" ", "").replace(":", "")
        if _HEX130.match(pub):
            pub = pub[2:]
        if not _HEX128.match(pub):
            raise InvalidCredential("lock_public_key", "invalid_public_key")
        try:
            pub_key = crypto.load_public_key(pub)
        except crypto.CryptoError as err:
            raise InvalidCredential("lock_public_key", "invalid_public_key") from err
        try:
            short = int(str(short_user_id).strip() or "0")
        except ValueError as err:
            raise InvalidCredential("short_user_id", "invalid_short_user_id") from err
        if not 0 <= short <= 0xFFFF:
            raise InvalidCredential("short_user_id", "invalid_short_user_id")
        nick = nickname.strip()
        if len(nick.encode("utf-8")) > 32:
            raise InvalidCredential("nickname", "invalid_nickname")
        return cls(
            serial=serial,
            admin_user_id=bytes.fromhex(admin),
            lock_public_key=pub_key,
            short_user_id=short,
            nickname=nick,
        )


@dataclass
class _Pending:
    future: asyncio.Future[bytes]
    started: float = field(default_factory=lambda: 0.0)


class LockSession:
    """One authenticated BLE session with the lock."""

    def __init__(
        self,
        credentials: LockCredentials,
        transport: Transport,
        *,
        now: Callable[[], datetime],
        on_state: Callable[[LockStatus], None] | None = None,
        on_params: Callable[[dict[int, bytes]], None] | None = None,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
    ) -> None:
        """Create a session.

        ``now`` must return the current *local* time as the lock knows it; it is
        used for the VERIFY timestamp which the lock checks for freshness.
        """
        self._creds = credentials
        self._transport = transport
        self._now = now
        self._on_state = on_state
        self._on_params = on_params
        self._timeout = response_timeout
        self._iv = crypto.serial_iv(credentials.serial)
        self._session_priv: EllipticCurvePrivateKey | None = None
        self._key: bytes | None = None
        self._info: SessionInfo | None = None
        self._pending: dict[int, asyncio.Future[bytes]] = {}
        self._lock = asyncio.Lock()
        self._buffer = bytearray()
        self._closed = False

    # ------------------------------------------------------------------ state
    @property
    def authenticated(self) -> bool:
        """True once the lock has handed us a session key."""
        return self._key is not None and not self._closed

    @property
    def info(self) -> SessionInfo | None:
        """Device information returned by the VERIFY response."""
        return self._info

    def close(self) -> None:
        """Invalidate the session (connection lost)."""
        self._closed = True
        self._key = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(SessionError("connection closed"))
        self._pending.clear()
        self._buffer.clear()

    # ---------------------------------------------------------------- receive
    def feed(self, data: bytes) -> None:
        """Feed raw notification bytes; reassembles frames if fragmented."""
        if self._closed:
            return
        self._buffer += bytes(data)
        while True:
            frame_bytes = self._pop_frame()
            if frame_bytes is None:
                return
            try:
                frame = protocol.parse_frame(frame_bytes)
            except ProtocolError as err:
                _LOGGER.debug("Dropping invalid frame (%d bytes): %s", len(frame_bytes), err)
                continue
            self._dispatch(frame)

    def _pop_frame(self) -> bytes | None:
        buf = self._buffer
        # resync on the frame magic
        idx = buf.find(protocol.FRAME_MAGIC)
        if idx < 0:
            buf.clear()
            return None
        if idx:
            del buf[:idx]
        if len(buf) < 4:
            return None
        size = int.from_bytes(buf[2:4], "little")
        if size < protocol.MIN_FRAME_LEN:
            # garbage; skip the magic and resync
            del buf[:2]
            return self._pop_frame()
        if len(buf) < size:
            return None
        out = bytes(buf[:size])
        del buf[:size]
        return out

    def _dispatch(self, frame: Frame) -> None:
        if not frame.is_response:
            _LOGGER.debug("Ignoring non-response frame cmd=%#04x", frame.cmd)
            return
        _LOGGER.debug("<- cmd=%#04x len=%d", frame.cmd, len(frame.body))
        if frame.cmd == Cmd.STATE_NOTIFY:
            self._handle_state_notify(frame.body)
            # a pending QUERY_STATUS is not answered by a notify; fall through
            return
        fut = self._pending.pop(frame.cmd, None)
        if fut is not None and not fut.done():
            fut.set_result(frame.body)
        else:
            _LOGGER.debug("Unsolicited response cmd=%#04x", frame.cmd)

    def _handle_state_notify(self, body: bytes) -> None:
        if self._key is None:
            return
        try:
            status = protocol.parse_status(self._decrypt(body))
        except (crypto.CryptoError, ProtocolError) as err:
            _LOGGER.debug("Bad state notify: %s", err)
            return
        if status.locked is not None and self._on_state is not None:
            self._on_state(status)

    # ------------------------------------------------------------------ crypto
    def _encrypt(self, plaintext: bytes) -> bytes:
        if self._key is None:
            raise NotAuthenticatedError("no session key")
        return crypto.aes_cbc_encrypt(self._key, self._iv, plaintext)

    def _decrypt(self, body: bytes) -> bytes:
        if self._key is None:
            raise NotAuthenticatedError("no session key")
        return crypto.aes_cbc_decrypt(self._key, self._iv, body)

    # ---------------------------------------------------------------- requests
    async def _send(self, cmd: int, body: bytes) -> bytes:
        """Write one framed request and await the matching response body."""
        if self._closed:
            raise SessionError("session closed")
        async with self._lock:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bytes] = loop.create_future()
            self._pending[cmd] = fut
            frame = protocol.build_frame(cmd, body)
            _LOGGER.debug("-> cmd=%#04x len=%d", cmd, len(frame))
            try:
                await self._transport.write(frame)
                return await asyncio.wait_for(fut, self._timeout)
            except TimeoutError as err:
                raise LockTimeoutError(f"no response to command {cmd:#04x}") from err
            finally:
                if self._pending.get(cmd) is fut:
                    del self._pending[cmd]

    async def _command(self, cmd: int, plaintext: bytes) -> bytes:
        """Encrypted request/response; returns the decrypted response plaintext."""
        body = await self._send(cmd, self._encrypt(plaintext))
        try:
            return self._decrypt(body)
        except crypto.CryptoError as err:
            raise SessionError(f"cannot decrypt response to {cmd:#04x}: {err}") from err

    def _admin_request(self) -> bytes:
        return protocol.build_admin_request(self._creds.admin_user_id, int(self._now().timestamp()))

    # ------------------------------------------------------------------ public
    async def handshake(self) -> SessionInfo:
        """Run VERIFY and obtain the session key."""
        self._session_priv = crypto.generate_keypair()
        identity = protocol.build_identity(
            self._creds.admin_user_id,
            crypto.public_key_xy(self._session_priv),
            self._now(),
            self._creds.identity_a4,
        )
        blob = crypto.ecies_encrypt(identity, self._creds.lock_public_key)
        body = await self._send(Cmd.VERIFY, blob)
        try:
            plaintext = crypto.ecies_decrypt(body, self._session_priv)
            info = protocol.parse_verify_response(plaintext)
        except (crypto.CryptoError, ProtocolError) as err:
            raise SessionError(f"handshake rejected: {err}") from err
        self._key = info.session_key
        self._info = info
        _LOGGER.debug("Session established (fw %s, hw %s)", info.firmware, info.hardware)
        return info

    async def query_status(self) -> LockStatus:
        """Read bolt state and battery level."""
        plaintext = await self._command(Cmd.QUERY_STATUS, self._admin_request())
        result, _ = protocol.split_result(plaintext)
        if result != protocol.RESULT_OK:
            raise LockRefusedError(Cmd.QUERY_STATUS, result)
        status = protocol.parse_status(plaintext)
        if status.locked is not None and self._on_state is not None:
            self._on_state(status)
        return status

    async def get_params(self) -> dict[int, bytes]:
        """Fetch the lock parameter block (settings, battery candidates)."""
        plaintext = await self._command(Cmd.GET_PARAMS, self._admin_request())
        result, _ = protocol.split_result(plaintext)
        if result != protocol.RESULT_OK:
            raise LockRefusedError(Cmd.GET_PARAMS, result)
        params = protocol.parse_params(plaintext)
        if self._on_params is not None:
            self._on_params(params)
        return params

    async def get_history_page(self, index: int = 0) -> HistoryPage:
        """Read a page of access records without acknowledging or deleting them."""
        plaintext = await self._command(
            Cmd.RECORD_LOG, build_history_request(index, int(self._now().timestamp()))
        )
        if not plaintext:
            raise SessionError("empty history response")
        if plaintext[0] not in (protocol.RESULT_OK, 0x41):
            raise LockRefusedError(Cmd.RECORD_LOG, plaintext[0])
        try:
            return parse_history_page(plaintext)
        except ProtocolError as err:
            raise SessionError(f"invalid history response: {err}") from err

    async def heartbeat(self) -> None:
        """Keep-alive; the lock answers with a bare result byte."""
        plaintext = await self._command(Cmd.HEARTBEAT, self._admin_request())
        result, _ = protocol.split_result(plaintext)
        if result != protocol.RESULT_OK:
            raise LockRefusedError(Cmd.HEARTBEAT, result)

    async def set_locked(self, lock: bool) -> None:
        """Actuate the bolt. Raises :class:`LockRefusedError` on a non-zero result."""
        tlv = protocol.build_on_off(
            self._creds.short_user_id,
            lock,
            int(self._now().timestamp()),
            self._creds.nickname,
        )
        plaintext = await self._command(Cmd.ON_OFF, tlv)
        result, _ = protocol.split_result(plaintext)
        if result != protocol.RESULT_OK:
            raise LockRefusedError(Cmd.ON_OFF, result)


async def run_heartbeat(
    session: LockSession, stop: asyncio.Event, interval: float = HEARTBEAT_INTERVAL
) -> None:
    """Send heartbeats until ``stop`` is set or the session fails."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), interval)
            return
        except TimeoutError:
            pass
        await session.heartbeat()


WriteFn = Callable[[bytes], Awaitable[None]]


class CallableTransport:
    """Adapt a plain ``async def write(data)`` coroutine function to :class:`Transport`."""

    def __init__(self, write: WriteFn) -> None:
        self._write = write

    async def write(self, data: bytes) -> None:
        await self._write(data)
