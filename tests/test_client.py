"""LockSession end-to-end against the simulated lock (no Home Assistant needed)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from custom_components.eufy_lock_ble.eufy_ble import (
    LockCredentials,
    LockRefusedError,
    LockSession,
    LockTimeoutError,
    SessionError,
)
from custom_components.eufy_lock_ble.eufy_ble.client import (
    CallableTransport,
    InvalidCredential,
    NotAuthenticatedError,
)

from .fake_lock import ADMIN_HEX, NICK, SERIAL, FakeLock


def make_credentials(lock: FakeLock, **overrides: object) -> LockCredentials:
    kwargs: dict[str, object] = dict(
        serial=lock.serial,
        admin_user_id=lock.admin.hex(),
        lock_public_key=lock.public_key_hex,
        short_user_id=0,
        nickname=NICK,
    )
    kwargs.update(overrides)
    return LockCredentials.from_strings(**kwargs)  # type: ignore[arg-type]


def wire(lock: FakeLock, session_factory) -> LockSession:
    """Connect a session and a fake lock through an in-memory pipe."""

    async def write(data: bytes) -> None:
        lock.write(data)

    session = session_factory(CallableTransport(write))
    lock.on_notify = session.feed
    return session


@pytest.fixture
def lock() -> FakeLock:
    return FakeLock()


async def test_handshake_and_status(lock: FakeLock) -> None:
    states: list[bool] = []
    session = wire(
        lock,
        lambda t: LockSession(make_credentials(lock), t, now=lambda: datetime.now().astimezone(), on_state=states.append),
    )
    assert not session.authenticated
    with pytest.raises(NotAuthenticatedError):
        await session.query_status()

    info = await session.handshake()
    assert session.authenticated
    assert info.session_key == lock.session_key
    assert info.firmware == "2.0.4.2"
    assert info.serial == lock.serial

    status = await session.query_status()
    assert status.locked is True and status.battery == 10
    assert [s.locked for s in states] == [True]
    lock.locked = False
    lock.battery = 9
    status = await session.query_status()
    assert status.locked is False and status.battery == 9
    assert [(s.locked, s.battery) for s in states] == [(True, 10), (False, 9)]


async def test_unlock_lock_cycle(lock: FakeLock) -> None:
    states: list[bool] = []
    session = wire(
        lock,
        lambda t: LockSession(make_credentials(lock), t, now=lambda: datetime.now().astimezone(), on_state=states.append),
    )
    await session.handshake()
    await session.set_locked(False)
    assert lock.locked is False
    assert [s.locked for s in states] == [False]  # the STATE_NOTIFY push was decoded
    await session.set_locked(True)
    assert lock.locked is True
    assert [s.locked for s in states] == [False, True]
    cmd, plaintext = lock.received[-1]
    assert cmd == 0x08
    assert plaintext[:7] == bytes.fromhex("a1020000a20101")
    assert plaintext.endswith(bytes([0xA4, len(NICK)]) + NICK.encode())


async def test_stale_timestamp_is_refused(lock: FakeLock) -> None:
    """A session is granted but actuation returns 0x42 when the identity clock is stale."""
    stale = lambda: datetime.now().astimezone() - timedelta(minutes=10)  # noqa: E731
    session = wire(lock, lambda t: LockSession(make_credentials(lock), t, now=stale))
    await session.handshake()
    assert session.authenticated
    with pytest.raises(LockRefusedError) as excinfo:
        await session.set_locked(False)
    assert excinfo.value.result == 0x42
    assert lock.locked is True


async def test_heartbeat_and_params(lock: FakeLock) -> None:
    received: list[dict[int, bytes]] = []
    session = wire(
        lock,
        lambda t: LockSession(
            make_credentials(lock), t, now=lambda: datetime.now().astimezone(), on_params=received.append
        ),
    )
    await session.handshake()
    await session.heartbeat()
    params = await session.get_params()
    assert params == lock.params
    assert received == [lock.params]
    # heartbeat and status requests share the same TLV layout
    hb = next(p for c, p in lock.received if c == 0x03)
    st_cmd, _ = lock.received[-1]
    assert hb[:22] == bytes([0xA1, 20]) + lock.admin
    assert st_cmd == 0x21


async def test_wrong_public_key_rejected(lock: FakeLock) -> None:
    other = FakeLock()
    creds = make_credentials(lock, lock_public_key=other.public_key_hex)
    session = wire(lock, lambda t: LockSession(creds, t, now=lambda: datetime.now().astimezone(), response_timeout=0.2))
    # the fake lock cannot decrypt our identity -> it raises inside write(); a real
    # lock stays silent or answers garbage. Either way no session key.
    with pytest.raises((SessionError, Exception)):
        await session.handshake()
    assert not session.authenticated


async def test_timeout(lock: FakeLock) -> None:
    async def black_hole(_: bytes) -> None:
        return None

    session = LockSession(
        make_credentials(lock),
        CallableTransport(black_hole),
        now=lambda: datetime.now().astimezone(),
        response_timeout=0.05,
    )
    with pytest.raises(LockTimeoutError):
        await session.handshake()


async def test_fragmented_notifications(lock: FakeLock) -> None:
    """Frames split across several notifications are reassembled."""
    session = wire(lock, lambda t: LockSession(make_credentials(lock), t, now=lambda: datetime.now().astimezone()))
    chunks: list[bytes] = []
    real_feed = session.feed

    def fragmenting_feed(data: bytes) -> None:
        chunks.append(data)
        for i in range(0, len(data), 7):
            real_feed(data[i : i + 7])

    lock.on_notify = fragmenting_feed
    await session.handshake()
    assert (await session.query_status()).locked is True
    assert len(chunks) == 2


async def test_garbage_between_frames(lock: FakeLock) -> None:
    session = wire(lock, lambda t: LockSession(make_credentials(lock), t, now=lambda: datetime.now().astimezone()))
    await session.handshake()
    session.feed(b"\x00\x01\x02")  # noise
    session.feed(b"\xff\x09\x03\x00")  # bogus tiny size -> resync
    assert (await session.query_status()).locked is True


async def test_close_fails_pending(lock: FakeLock) -> None:
    async def slow(_: bytes) -> None:
        await asyncio.sleep(0)

    session = LockSession(
        make_credentials(lock), CallableTransport(slow), now=lambda: datetime.now().astimezone(), response_timeout=5
    )
    task = asyncio.create_task(session.handshake())
    await asyncio.sleep(0.01)
    session.close()
    with pytest.raises(SessionError):
        await task
    with pytest.raises(SessionError):
        await session.handshake()


def test_credentials_validation() -> None:
    good = FakeLock()
    creds = LockCredentials.from_strings(
        serial=" " + SERIAL + " ",
        admin_user_id=ADMIN_HEX.upper(),
        lock_public_key="04" + good.public_key_hex,
        short_user_id="7",
        nickname=" abc*** ",
    )
    assert creds.serial == SERIAL
    assert creds.admin_user_id == bytes.fromhex(ADMIN_HEX)
    assert creds.short_user_id == 7
    assert creds.nickname == "abc***"

    def bad(**kw: object) -> str:
        base = dict(serial=SERIAL, admin_user_id=ADMIN_HEX, lock_public_key=good.public_key_hex)
        base.update(kw)
        with pytest.raises(InvalidCredential) as exc:
            LockCredentials.from_strings(**base)  # type: ignore[arg-type]
        return f"{exc.value.field}:{exc.value.code}"

    assert bad(serial="T85") == "serial:invalid_serial"
    assert bad(admin_user_id="zz" * 20) == "admin_user_id:invalid_admin_user_id"
    assert bad(admin_user_id="ab" * 19) == "admin_user_id:invalid_admin_user_id"
    assert bad(lock_public_key="00" * 64) == "lock_public_key:invalid_public_key"
    assert bad(lock_public_key="ab" * 10) == "lock_public_key:invalid_public_key"
    assert bad(short_user_id="x") == "short_user_id:invalid_short_user_id"
    assert bad(short_user_id=70000) == "short_user_id:invalid_short_user_id"
    assert bad(nickname="n" * 40) == "nickname:invalid_nickname"
