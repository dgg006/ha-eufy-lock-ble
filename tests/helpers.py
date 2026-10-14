"""Helpers for Home Assistant level tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Any

from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from custom_components.eufy_lock_ble.const import (
    CONF_ADMIN_USER_ID,
    CONF_LOCK_PUBLIC_KEY,
    CONF_NICKNAME,
    CONF_SERIAL,
    CONF_SHORT_USER_ID,
)
from custom_components.eufy_lock_ble.eufy_ble import CHAR_COMMAND_UUID

from .fake_lock import NICK, FakeLock

ADDRESS = "AA:BB:CC:DD:EE:FF"
ADV_NAME = "T8510_00000001"


def make_ble_device(address: str = ADDRESS, name: str = ADV_NAME) -> BLEDevice:
    return BLEDevice(address, name, {})


def make_service_info(address: str = ADDRESS, name: str = ADV_NAME) -> BluetoothServiceInfoBleak:
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=(
            {0x0802: bytes.fromhex("aabbccddeeff00010101")} if name.startswith("T85") else {}
        ),
        service_data={},
        service_uuids=["000002fa-0000-1000-8000-00805f9b34fb"],
        source="local",
        device=make_ble_device(address, name),
        advertisement=None,
        connectable=True,
        time=time.monotonic(),
        tx_power=None,
    )


def entry_data(lock: FakeLock, address: str = ADDRESS) -> dict[str, Any]:
    return {
        CONF_ADDRESS: address,
        CONF_NAME: "Back Door",
        CONF_SERIAL: lock.serial,
        CONF_ADMIN_USER_ID: lock.admin.hex(),
        CONF_LOCK_PUBLIC_KEY: lock.public_key_hex,
        CONF_SHORT_USER_ID: 0,
        CONF_NICKNAME: NICK,
    }


class FakeBleakClient:
    """Just enough of BleakClient to drive a :class:`FakeLock`."""

    def __init__(self, lock: FakeLock, address: str = ADDRESS) -> None:
        self.lock = lock
        self.address = address
        self.is_connected = True
        self.disconnected_callback: Callable[[FakeBleakClient], None] | None = None
        self.writes: list[bytes] = []
        self.fail_writes = False

    async def start_notify(self, uuid: str, callback: Callable[[Any, bytearray], None]) -> None:
        if uuid == CHAR_COMMAND_UUID:
            self.lock.on_notify = lambda data: callback(0, bytearray(data))

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = False) -> None:
        assert uuid == CHAR_COMMAND_UUID
        assert not response
        if not self.is_connected or self.fail_writes:
            from bleak.exc import BleakError

            raise BleakError("not connected")
        self.writes.append(bytes(data))
        self.lock.write(bytes(data))

    async def disconnect(self) -> bool:
        if self.is_connected:
            self.is_connected = False
            if self.disconnected_callback:
                self.disconnected_callback(self)
        return True

    def drop_link(self) -> None:
        """Simulate the peripheral going away."""
        self.is_connected = False
        if self.disconnected_callback:
            self.disconnected_callback(self)


class FakeConnector:
    """Replacement for ``bleak_retry_connector.establish_connection``."""

    def __init__(self, lock: FakeLock) -> None:
        self.lock = lock
        self.clients: list[FakeBleakClient] = []
        self.fail_next: Exception | None = None

    async def __call__(self, client_cls, device, name, disconnected_callback=None, **kwargs):
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err
        client = FakeBleakClient(self.lock, device.address)
        client.disconnected_callback = disconnected_callback
        self.clients.append(client)
        return client

    @property
    def current(self) -> FakeBleakClient:
        return self.clients[-1]


async def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)
