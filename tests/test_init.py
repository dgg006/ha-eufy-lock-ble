"""Integration setup, entities and lock/unlock through a simulated BLE link."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import patch

from bleak.exc import BleakError
from homeassistant.components.lock import DOMAIN as LOCK_DOMAIN, LockState
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_LOCK, SERVICE_UNLOCK, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eufy_lock_ble.const import (
    DOMAIN,
    OPT_KEEP_CONNECTED,
    OPT_POLL_INTERVAL,
)

from .fake_lock import FakeLock
from .helpers import ADDRESS, FakeConnector, entry_data, make_ble_device, wait_until

pytestmark = pytest.mark.usefixtures("mock_bluetooth")

LOCK_ENTITY = "lock.back_door"
CONNECTED_ENTITY = "binary_sensor.back_door_connected"


@pytest.fixture
def lock() -> FakeLock:
    # the fake lock's clock must match what the integration uses for the identity
    # timestamp (Home Assistant local time), exactly like a real lock set from the phone.
    return FakeLock(clock=dt_util.now)


@pytest.fixture
async def connector(lock: FakeLock) -> AsyncGenerator[FakeConnector]:
    fake = FakeConnector(lock)
    with (
        patch("custom_components.eufy_lock_ble.coordinator.establish_connection", fake),
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            return_value=make_ble_device(),
        ),
    ):
        yield fake


async def setup_entry(hass: HomeAssistant, lock: FakeLock, **options: object) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=entry_data(lock), options=options, unique_id=ADDRESS, title="Back Door"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_on_demand_mode_lock_unlock(
    hass: HomeAssistant, lock: FakeLock, connector: FakeConnector
) -> None:
    entry = await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: False, OPT_POLL_INTERVAL: 60})
    assert entry.state is ConfigEntryState.LOADED

    state = hass.states.get(LOCK_ENTITY)
    assert state is not None and state.state == LockState.LOCKED
    assert state.attributes["connected"] is False
    # the poll connected once and disconnected again
    assert len(connector.clients) == 1 and not connector.current.is_connected
    assert hass.states.get(CONNECTED_ENTITY).state == STATE_OFF

    await hass.services.async_call(
        LOCK_DOMAIN, SERVICE_UNLOCK, {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True
    )
    assert lock.locked is False
    assert hass.states.get(LOCK_ENTITY).state == LockState.UNLOCKED
    assert len(connector.clients) == 2 and not connector.current.is_connected

    await hass.services.async_call(
        LOCK_DOMAIN, SERVICE_LOCK, {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True
    )
    assert lock.locked is True
    assert hass.states.get(LOCK_ENTITY).state == LockState.LOCKED

    # ON_OFF plaintext carried our short user id, direction byte and nickname
    plaintext = next(p for c, p in lock.received if c == 0x08)
    assert plaintext[:7] == bytes.fromhex("a1020000a20100")

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_device_and_diagnostic_entities(
    hass: HomeAssistant,
    lock: FakeLock,
    connector: FakeConnector,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    entry = await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: False})
    device = device_registry.async_get_device(identifiers={(DOMAIN, lock.serial)})
    assert device is not None
    assert device.model == "Smart Lock Touch"
    assert device.serial_number == lock.serial
    assert device.sw_version == "2.0.4.2"
    assert (dr.CONNECTION_BLUETOOTH, ADDRESS) in device.connections

    battery = hass.states.get("sensor.back_door_battery")
    assert battery is not None and battery.state == "10"

    # every lock setting is an entity of its own
    assert hass.states.get("sensor.back_door_auto_lock_delay").state == "60"
    assert hass.states.get("sensor.back_door_wrong_try_lockdown").state == "180"
    assert hass.states.get("sensor.back_door_wrong_try_attempts").state == "5"
    assert hass.states.get("sensor.back_door_sound_volume").state == "medium"
    assert hass.states.get("sensor.back_door_auto_lock_schedule_start").state == "23:00"
    assert hass.states.get("binary_sensor.back_door_auto_lock").state == STATE_OFF
    assert hass.states.get("binary_sensor.back_door_one_touch_lock").state == STATE_ON
    assert hass.states.get("binary_sensor.back_door_scramble_passcode").state == STATE_ON

    # disabled-by-default diagnostics exist in the registry
    assert entity_registry.async_get("sensor.back_door_parameters") is not None
    assert entity_registry.async_get("sensor.back_door_signal_strength") is not None

    diag = await _diagnostics(hass, entry)
    assert diag["entry"]["data"]["admin_user_id"] == "**REDACTED**"
    assert diag["state"]["is_locked"] is True
    assert diag["state"]["battery"] == 10
    assert diag["state"]["params"]["auto_lock_timer"] == 60
    assert "params_raw" not in diag["state"]


async def _diagnostics(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    from custom_components.eufy_lock_ble.diagnostics import async_get_config_entry_diagnostics

    return await async_get_config_entry_diagnostics(hass, entry)


async def test_persistent_mode_live_updates(
    hass: HomeAssistant, lock: FakeLock, connector: FakeConnector
) -> None:
    entry = await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: True})
    coordinator = entry.runtime_data
    await wait_until(lambda: coordinator.connected)
    await hass.async_block_till_done()
    assert hass.states.get(LOCK_ENTITY).state == LockState.LOCKED
    assert hass.states.get(CONNECTED_ENTITY).state == STATE_ON

    # someone turns the thumb-turn: the lock pushes STATE_NOTIFY (with battery)
    lock.locked = False
    lock.battery = 9
    lock.push_state()
    await hass.async_block_till_done()
    assert hass.states.get(LOCK_ENTITY).state == LockState.UNLOCKED
    assert hass.states.get("sensor.back_door_battery").state == "9"

    # actuate over the live session: no new connection
    await hass.services.async_call(
        LOCK_DOMAIN, SERVICE_LOCK, {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True
    )
    assert lock.locked is True
    assert hass.states.get(LOCK_ENTITY).state == LockState.LOCKED
    assert len(connector.clients) == 1

    # link drops -> reconnects automatically; the lock keeps its state during the grace
    # period and the connectivity sensor does not flap for a short blip
    connector.current.drop_link()
    await hass.async_block_till_done()
    assert not coordinator.connected
    assert hass.states.get(CONNECTED_ENTITY).state == STATE_ON
    assert hass.states.get(CONNECTED_ENTITY).attributes["session_open"] is False
    assert hass.states.get(LOCK_ENTITY).state == LockState.LOCKED
    await wait_until(lambda: len(connector.clients) == 2 and coordinator.connected, timeout=10)
    await hass.async_block_till_done()
    state = hass.states.get(CONNECTED_ENTITY)
    assert state.state == STATE_ON
    assert state.attributes["session_open"] is True
    assert state.attributes["disconnects"] == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not connector.current.is_connected


async def test_refused_actuation_raises(
    hass: HomeAssistant, lock: FakeLock, connector: FakeConnector
) -> None:
    await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: False})
    lock.refuse_actuation = True
    with pytest.raises(HomeAssistantError, match="refused"):
        await hass.services.async_call(
            LOCK_DOMAIN, SERVICE_UNLOCK, {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True
        )
    assert lock.locked is True
    assert hass.states.get(LOCK_ENTITY).state == LockState.LOCKED


async def test_options_update_reloads(
    hass: HomeAssistant, lock: FakeLock, connector: FakeConnector
) -> None:
    entry = await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: False})
    assert entry.runtime_data.update_interval.total_seconds() == 300
    hass.config_entries.async_update_entry(
        entry, options={OPT_KEEP_CONNECTED: False, OPT_POLL_INTERVAL: 120}
    )
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.update_interval.total_seconds() == 120


async def test_setup_survives_unreachable_lock(hass: HomeAssistant, lock: FakeLock) -> None:
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address", return_value=None
    ):
        entry = await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: False})
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(LOCK_ENTITY).state == "unavailable"


async def test_persistent_mode_waits_for_reconnect(
    hass: HomeAssistant, lock: FakeLock, connector: FakeConnector
) -> None:
    """A service call while the link is down waits for the loop to reconnect."""
    entry = await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: True})
    coordinator = entry.runtime_data
    await wait_until(lambda: coordinator.connected)
    # first reconnect attempt fails, the loop backs off, the second succeeds
    connector.fail_next = BleakError("proxy busy")
    connector.current.drop_link()
    await hass.async_block_till_done()
    assert not coordinator.connected
    await hass.services.async_call(
        LOCK_DOMAIN, SERVICE_UNLOCK, {ATTR_ENTITY_ID: LOCK_ENTITY}, blocking=True
    )
    assert lock.locked is False
    assert coordinator.connected
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_hung_disconnect_does_not_wedge_the_loop(
    hass: HomeAssistant, lock: FakeLock, connector: FakeConnector
) -> None:
    """A proxy that never answers disconnect() must not block reconnection."""
    entry = await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: True})
    coordinator = entry.runtime_data
    await wait_until(lambda: coordinator.connected)
    client = connector.current

    async def hang(*_args, **_kwargs):
        await asyncio.sleep(3600)

    client.disconnect = hang  # type: ignore[method-assign]
    with patch("custom_components.eufy_lock_ble.coordinator.DISCONNECT_TIMEOUT", 0.1):
        # the peripheral drops; the loop's clean-up disconnect() hangs but must time out
        client.drop_link()
        await wait_until(lambda: len(connector.clients) >= 2 and coordinator.connected, timeout=15)
    assert connector.current is not client
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_connected_sensor_reports_a_lasting_drop(
    hass: HomeAssistant, lock: FakeLock, connector: FakeConnector
) -> None:
    """The connectivity sensor turns off once the link has stayed down past the debounce."""
    entry = await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: True})
    coordinator = entry.runtime_data
    await wait_until(lambda: coordinator.connected)
    connector.fail_next = BleakError("gone")
    with patch("custom_components.eufy_lock_ble.coordinator.CONNECTED_DEBOUNCE", 0.2):
        connector.current.drop_link()
        await hass.async_block_till_done()
        assert coordinator.link_up
        assert hass.states.get(CONNECTED_ENTITY).state == STATE_ON
        await asyncio.sleep(0.3)
        assert not coordinator.link_up
        # the refresh scheduled at debounce + 1 s pushes the new state to the entity
        await wait_until(lambda: hass.states.get(CONNECTED_ENTITY).state == STATE_OFF)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_grace_period_then_unavailable(
    hass: HomeAssistant, lock: FakeLock, connector: FakeConnector
) -> None:
    entry = await setup_entry(hass, lock, **{OPT_KEEP_CONNECTED: True})
    coordinator = entry.runtime_data
    await wait_until(lambda: coordinator.connected)
    # stop reconnects so the drop is permanent
    connector.fail_next = BleakError("gone")
    with patch("custom_components.eufy_lock_ble.coordinator.AVAILABILITY_GRACE", 0.2):
        connector.current.drop_link()
        await hass.async_block_till_done()
        assert coordinator.available  # within grace
        await asyncio.sleep(0.3)
        assert not coordinator.available
    assert await hass.config_entries.async_unload(entry.entry_id)
