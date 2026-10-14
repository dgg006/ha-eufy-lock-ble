"""eufy Smart Lock (BLE) — bridgeless control through Home Assistant Bluetooth proxies."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.storage import Store

from .const import (
    CONF_ADMIN_USER_ID,
    CONF_LOCK_PUBLIC_KEY,
    CONF_NICKNAME,
    CONF_SERIAL,
    CONF_SHORT_USER_ID,
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    OPT_KEEP_CONNECTED,
    OPT_POLL_INTERVAL,
    PLATFORMS,
)
from .coordinator import EufyLockCoordinator
from .eufy_ble import LockCredentials
from .eufy_ble.client import InvalidCredential

_LOGGER = logging.getLogger(__name__)

type EufyLockConfigEntry = ConfigEntry[EufyLockCoordinator]


def credentials_from_entry(entry: ConfigEntry) -> LockCredentials:
    """Build validated credentials from a config entry."""
    data = entry.data
    return LockCredentials.from_strings(
        serial=data[CONF_SERIAL],
        admin_user_id=data[CONF_ADMIN_USER_ID],
        lock_public_key=data[CONF_LOCK_PUBLIC_KEY],
        short_user_id=data.get(CONF_SHORT_USER_ID, 0),
        nickname=data.get(CONF_NICKNAME, ""),
    )


async def async_setup_entry(hass: HomeAssistant, entry: EufyLockConfigEntry) -> bool:
    """Set up a lock from a config entry."""
    try:
        credentials = credentials_from_entry(entry)
    except InvalidCredential as err:
        raise ConfigEntryError(f"Invalid stored credential: {err}") from err

    coordinator = EufyLockCoordinator(hass, entry, entry.data[CONF_ADDRESS], credentials)
    entry.runtime_data = coordinator
    await coordinator.async_start()
    if not coordinator.keep_connected:
        # on-demand mode: try one poll now so entities have a state, but don't
        # fail setup if the lock is momentarily out of range.
        await coordinator.async_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: EufyLockConfigEntry) -> None:
    """Apply display-name edits immediately; reload only connection settings."""
    coordinator = entry.runtime_data
    if (
        entry.options.get(OPT_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED) != coordinator.keep_connected
        or entry.options.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        != coordinator.update_interval.total_seconds()
    ):
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        coordinator.async_update_listeners()


async def async_unload_entry(hass: HomeAssistant, entry: EufyLockConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the private access-history cache when the lock is removed."""
    await Store(hass, 1, f"{DOMAIN}_history_{entry.entry_id}").async_remove()
