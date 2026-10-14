"""Connection manager / coordinator for an eufy BLE lock.

Two modes, chosen by the ``keep_connected`` option:

* **persistent** (default): hold the BLE connection open through the Bluetooth
  proxy, send heartbeats, and receive live ``STATE_NOTIFY`` pushes.  The lock
  only accepts one central at a time, so the eufy app cannot be used while
  Home Assistant is connected.
* **on demand**: connect only to poll (``poll_interval``) or to actuate, then
  disconnect.  Frees the lock for the app between operations but state changes
  made at the door are only seen at the next poll.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import datetime, timedelta
import logging
from typing import TypeVar

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    ADV_NOTIFY_INTERVAL,
    AVAILABILITY_GRACE,
    CONNECT_TIMEOUT,
    CONNECT_TOTAL_TIMEOUT,
    CONNECTED_DEBOUNCE,
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_POLL_INTERVAL,
    DISCONNECT_TIMEOUT,
    DOMAIN,
    HEARTBEAT_INTERVAL,
    OPERATION_CONNECT_WAIT,
    OPT_KEEP_CONNECTED,
    OPT_POLL_INTERVAL,
    RECONNECT_AFTER_DROP,
    RECONNECT_MAX_BACKOFF,
    RECONNECT_MIN_BACKOFF,
    STATE_SETTLE_TIMEOUT,
)
from .eufy_ble import (
    CHAR_AUX_NOTIFY_UUID,
    CHAR_COMMAND_UUID,
    LockCredentials,
    LockRefusedError,
    LockSession,
    LockStatus,
    SessionError,
    SessionInfo,
)

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")


def _fmt_duration(delta: timedelta) -> str:
    """Render a duration as ``1h02m03s`` / ``5m12s`` / ``42s`` for the log."""
    total = int(delta.total_seconds())
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


class LockUnreachableError(Exception):
    """The lock is not advertising / no connectable proxy sees it."""


class _BleakTransport:
    """Adapt a Bleak client to the session's write interface."""

    def __init__(self, client: BleakClientWithServiceCache) -> None:
        self._client = client

    async def write(self, data: bytes) -> None:
        await self._client.write_gatt_char(CHAR_COMMAND_UUID, data, response=False)


class EufyLockCoordinator(DataUpdateCoordinator[None]):
    """Owns the BLE connection and the last known lock state."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        address: str,
        credentials: LockCredentials,
    ) -> None:
        """Initialise the coordinator."""
        self.address = address
        self.credentials = credentials
        self.keep_connected: bool = entry.options.get(OPT_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED)
        poll = int(entry.options.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{address}",
            update_interval=timedelta(seconds=poll),
        )
        # lock state
        self.is_locked: bool | None = None
        self.battery: int | None = None
        self.params: dict[int, bytes] = {}
        self.info: SessionInfo | None = None
        self.rssi: int | None = None
        self.last_seen: datetime | None = None
        self.last_state_change: datetime | None = None
        self.connected_since: datetime | None = None
        self.disconnects = 0

        # connection state
        self._ble_device: BLEDevice | None = None
        self._client: BleakClientWithServiceCache | None = None
        self._session: LockSession | None = None
        self._disconnected = asyncio.Event()
        self._disconnected.set()
        self._connected_evt = asyncio.Event()
        self._disconnected_at: datetime | None = None
        self._refresh_timers: list[Callable[[], None]] = []
        self._lock_seen = False
        self._attempt = 0
        self._last_adv_notify = dt_util.utc_from_timestamp(0)
        self._conn_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._connect_task: asyncio.Task[None] | None = None
        self._unsub_bt: list[Callable[[], None]] = []
        self._state_watchers: list[Callable[[bool], None]] = []

    # ------------------------------------------------------------ properties
    @property
    def connected(self) -> bool:
        """True while an authenticated session is up."""
        return self._session is not None and self._session.authenticated

    @property
    def link_up(self) -> bool:
        """Connectivity as shown to the user.

        BLE peripherals drop the link now and then and we are back within seconds; that
        is not worth a logbook entry. In persistent mode a drop is only reported once the
        link has stayed down for CONNECTED_DEBOUNCE.
        """
        if self.connected:
            return True
        if not self.keep_connected or self._disconnected_at is None:
            return False
        return (dt_util.utcnow() - self._disconnected_at).total_seconds() < CONNECTED_DEBOUNCE

    @property
    def available(self) -> bool:
        """Entities are available once we have a state and the lock is reachable.

        In persistent mode a short grace period covers reconnects after a BLE drop
        so a 15 s blip does not flap every entity to unavailable.
        """
        if self.is_locked is None:
            return False
        if self.connected:
            return True
        if self.keep_connected:
            return (
                self._disconnected_at is not None
                and (dt_util.utcnow() - self._disconnected_at).total_seconds() < AVAILABILITY_GRACE
            )
        return self.last_update_success

    @callback
    def _async_mark_disconnected(self) -> None:
        """Record the drop, refresh entities now and again when debounce and grace expire."""
        if self._disconnected_at is None:
            now = dt_util.utcnow()
            self._disconnected_at = now
            self.disconnects += 1
            if self.connected_since is not None:
                _LOGGER.info(
                    "Lost the connection to %s after %s (drop #%d); reconnecting",
                    "[redacted]",
                    _fmt_duration(now - self.connected_since),
                    self.disconnects,
                )
            self.connected_since = None
            # the callback must be a @callback: HA would otherwise run a plain function
            # in an executor thread, where writing entity state is refused
            self._cancel_refresh_timers()
            self._refresh_timers = [
                async_call_later(self.hass, delay + 1, self._async_refresh_listeners)
                for delay in (CONNECTED_DEBOUNCE, AVAILABILITY_GRACE)
            ]
        self.async_update_listeners()

    @callback
    def _async_refresh_listeners(self, _now: datetime) -> None:
        """Re-evaluate link_up/available once a debounce or grace window has elapsed."""
        self.async_update_listeners()

    def _cancel_refresh_timers(self) -> None:
        for cancel in self._refresh_timers:
            cancel()
        self._refresh_timers = []

    # ------------------------------------------------------------- lifecycle
    async def async_start(self) -> None:
        """Register Bluetooth callbacks and start the connection loop."""
        self._unsub_bt.append(
            bluetooth.async_register_callback(
                self.hass,
                self._async_on_advertisement,
                BluetoothCallbackMatcher(address=self.address, connectable=True),
                BluetoothScanningMode.ACTIVE,
            )
        )
        self._unsub_bt.append(
            bluetooth.async_track_unavailable(
                self.hass, self._async_on_unavailable, self.address, connectable=True
            )
        )
        self._ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if self.keep_connected:
            self._connect_task = self.config_entry.async_create_background_task(
                self.hass, self._connect_loop(), name=f"{DOMAIN} connection {self.address}"
            )

    async def async_stop(self) -> None:
        """Tear everything down."""
        self._stop.set()
        self._wake.set()
        self._cancel_refresh_timers()
        for unsub in self._unsub_bt:
            unsub()
        self._unsub_bt.clear()
        if self._connect_task is not None:
            self._connect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._connect_task
            self._connect_task = None
        await self._async_disconnect()

    # ---------------------------------------------------- bluetooth callbacks
    @callback
    def _async_on_advertisement(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        self._ble_device = service_info.device
        self.rssi = service_info.rssi
        now = dt_util.utcnow()
        was_unseen = self.last_seen is None
        self.last_seen = now
        if not self._lock_seen:
            # the lock is back in range: end any wait for it, but leave the failure
            # back-off alone so a lock that refuses us is not hammered every second
            self._lock_seen = True
            if self.keep_connected and not self.connected:
                self._wake.set()
        # advertisements arrive every second or so; only wake entities when the
        # lock (re)appears or every ADV_NOTIFY_INTERVAL so RSSI stays roughly current
        if was_unseen or (now - self._last_adv_notify).total_seconds() >= ADV_NOTIFY_INTERVAL:
            self._last_adv_notify = now
            self.async_update_listeners()

    @callback
    def _async_on_unavailable(self, service_info: BluetoothServiceInfoBleak) -> None:
        _LOGGER.debug("%s is no longer seen by any connectable scanner", "[redacted]")
        self.rssi = None
        self._lock_seen = False
        self.async_update_listeners()

    # ----------------------------------------------------------- connection
    def _resolve_device(self) -> BLEDevice:
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            device = self._ble_device
        if device is None:
            raise LockUnreachableError(
                "Lock not seen by any connectable Bluetooth adapter/proxy"
            )
        return device

    async def _async_connect(self) -> LockSession:
        """Open a BLE connection and complete the handshake (caller holds _conn_lock)."""
        if self._session is not None and self._session.authenticated:
            return self._session
        await self._async_disconnect()
        device = self._resolve_device()
        self._attempt += 1
        _LOGGER.info("Connecting to %s (attempt %d)", "[redacted]", self._attempt)
        self._disconnected.clear()
        try:
            async with asyncio.timeout(CONNECT_TOTAL_TIMEOUT):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self.config_entry.title or self.address,
                    disconnected_callback=self._on_bleak_disconnect,
                    use_services_cache=True,
                    ble_device_callback=lambda: self._resolve_device(),
                    timeout=CONNECT_TIMEOUT,
                )
                self._client = client
                session = LockSession(
                    self.credentials,
                    _BleakTransport(client),
                    now=dt_util.now,
                    on_state=self._on_lock_state,
                    on_params=self._on_params,
                )
                try:
                    await client.start_notify(
                        CHAR_COMMAND_UUID, lambda _h, d: session.feed(bytes(d))
                    )
                    try:
                        await client.start_notify(
                            CHAR_AUX_NOTIFY_UUID, lambda _h, d: session.feed(bytes(d))
                        )
                    except BleakError as err:  # optional characteristic
                        _LOGGER.debug("B003 notify unavailable: %s", str(err) if isinstance(err, SessionError) else type(err).__name__)
                    info = await session.handshake()
                except BaseException:
                    session.close()
                    raise
        except BaseException:
            await self._async_disconnect()
            raise
        self._session = session
        self.info = info
        self._attempt = 0
        self._disconnected_at = None
        self.connected_since = dt_util.utcnow()
        self._connected_evt.set()
        _LOGGER.info("Connected to eufy lock %s (fw %s)", "[redacted]", info.firmware)
        self.async_update_listeners()
        return session

    def _on_bleak_disconnect(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("BLE disconnected from %s", "[redacted]")
        if self._session is not None:
            self._session.close()
        self._connected_evt.clear()
        self._disconnected.set()
        self.hass.loop.call_soon_threadsafe(self._async_mark_disconnected)

    async def _async_disconnect(self) -> None:
        session, client = self._session, self._client
        was_connected = session is not None and session.authenticated
        self._session, self._client = None, None
        if session is not None:
            session.close()
        if client is not None:
            try:
                async with asyncio.timeout(DISCONNECT_TIMEOUT):
                    await client.disconnect()
            except (BleakError, TimeoutError) as err:
                _LOGGER.debug("Error while disconnecting from %s: %s", "[redacted]", str(err) if isinstance(err, SessionError) else type(err).__name__)
        self._connected_evt.clear()
        self._disconnected.set()
        if was_connected and self.keep_connected:
            self._async_mark_disconnected()

    async def _connect_loop(self) -> None:
        """Run persistent mode: stay connected, heartbeat, reconnect with backoff."""
        backoff = RECONNECT_MIN_BACKOFF
        while not self._stop.is_set():
            delay = backoff
            try:
                async with self._conn_lock:
                    session = await self._async_connect()
                    await session.query_status()
                    try:
                        await session.get_params()
                    except SessionError as err:
                        _LOGGER.debug("get_params failed: %s", str(err) if isinstance(err, SessionError) else type(err).__name__)
                self.async_set_updated_data(None)
                backoff = RECONNECT_MIN_BACKOFF
                await self._heartbeat_until_disconnect(session)
                # a healthy session was closed by the peer: not a failure, reconnect at once
                delay = RECONNECT_AFTER_DROP
            except asyncio.CancelledError:
                raise
            except LockUnreachableError as err:
                _LOGGER.info(
                    "%s; waiting for an advertisement (up to %.0fs)", str(err) if isinstance(err, SessionError) else type(err).__name__, RECONNECT_MAX_BACKOFF
                )
                self._lock_seen = False
                self._wake.clear()
                await self._wait_wake(RECONNECT_MAX_BACKOFF)
                continue
            except (BleakError, BleakNotFoundError, SessionError, TimeoutError) as err:
                _LOGGER.warning(
                    "Connection to %s failed: %s; retry in %.0fs", "[redacted]", str(err) if isinstance(err, SessionError) else type(err).__name__, backoff
                )
                backoff = min(backoff * 2, RECONNECT_MAX_BACKOFF)
            except Exception:
                _LOGGER.exception("Unexpected error in connection loop for %s", "[redacted]")
                backoff = min(backoff * 2, RECONNECT_MAX_BACKOFF)
            await self._async_disconnect()
            if self._stop.is_set():
                return
            self._wake.clear()
            await self._wait_wake(delay)

    async def _wait_wake(self, timeout: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout)

    async def _heartbeat_until_disconnect(self, session: LockSession) -> None:
        while not self._stop.is_set() and session.authenticated:
            try:
                await asyncio.wait_for(self._disconnected.wait(), HEARTBEAT_INTERVAL)
                return
            except TimeoutError:
                pass
            async with self._conn_lock:
                if not session.authenticated:
                    return
                await session.heartbeat()

    # -------------------------------------------------------------- session ops
    async def _with_session(self, op: Callable[[LockSession], Awaitable[_T]]) -> _T:
        """Run ``op`` on the live session, or on a temporary one in on-demand mode."""
        if self.keep_connected and not self.connected:
            # kick the connection loop out of its back-off and give it a moment
            self._wake.set()
            try:
                await asyncio.wait_for(self._connected_evt.wait(), OPERATION_CONNECT_WAIT)
            except TimeoutError as err:
                raise LockUnreachableError("not connected to the lock") from err
        async with self._conn_lock:
            if self.keep_connected:
                if not self.connected or self._session is None:
                    raise LockUnreachableError("not connected to the lock")
                return await op(self._session)
            session = await self._async_connect()
            try:
                return await op(session)
            finally:
                await self._async_disconnect()

    async def _async_update_data(self) -> None:
        """Periodic refresh: query bolt state and parameters."""

        async def _refresh(session: LockSession) -> None:
            await session.query_status()
            try:
                await session.get_params()
            except SessionError as err:
                _LOGGER.debug("get_params failed: %s", str(err) if isinstance(err, SessionError) else type(err).__name__)

        try:
            await self._with_session(_refresh)
        except (LockUnreachableError, BleakError, SessionError, TimeoutError) as err:
            raise UpdateFailed(f"Cannot read lock state: {err}") from err

    async def async_set_locked(self, lock: bool) -> None:
        """Lock or unlock, then wait for the confirming state change."""
        settled = asyncio.Event()
        target = lock

        def _watch(state: bool) -> None:
            if state == target:
                settled.set()

        async def _actuate(session: LockSession) -> None:
            self._state_watchers.append(_watch)
            try:
                await session.set_locked(lock)
                try:
                    await asyncio.wait_for(settled.wait(), STATE_SETTLE_TIMEOUT)
                except TimeoutError:
                    _LOGGER.debug("No state notify after actuation; querying")
                    await session.query_status()
            finally:
                self._state_watchers.remove(_watch)

        await self._with_session(_actuate)

    # ----------------------------------------------------------- callbacks
    def _on_lock_state(self, status: LockStatus) -> None:
        locked = status.locked
        if locked is None:
            return
        if locked != self.is_locked:
            self.last_state_change = dt_util.utcnow()
        self.is_locked = locked
        if status.battery is not None:
            self.battery = status.battery
        for watcher in list(self._state_watchers):
            watcher(locked)
        self.async_set_updated_data(None)

    def _on_params(self, params: dict[int, bytes]) -> None:
        self.params = params
        self.async_update_listeners()


async def async_probe_lock(
    hass: HomeAssistant, address: str, credentials: LockCredentials
) -> tuple[SessionInfo, bool | None]:
    """Config-flow helper: connect once, handshake, read the state, disconnect."""
    device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        raise LockUnreachableError(f"{address} is not in range of a connectable adapter/proxy")
    client = await establish_connection(
        BleakClientWithServiceCache,
        device,
        address,
        use_services_cache=True,
        timeout=CONNECT_TIMEOUT,
    )
    session = LockSession(credentials, _BleakTransport(client), now=dt_util.now)
    try:
        await client.start_notify(CHAR_COMMAND_UUID, lambda _h, d: session.feed(bytes(d)))
        info = await session.handshake()
        status = await session.query_status()
        return info, status.locked
    finally:
        session.close()
        with contextlib.suppress(BleakError):
            await client.disconnect()


__all__ = [
    "EufyLockCoordinator",
    "LockRefusedError",
    "LockUnreachableError",
    "async_probe_lock",
]
