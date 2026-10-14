"""Diagnostics with secrets redacted."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS, CONF_EMAIL, CONF_NAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import EufyLockConfigEntry
from .const import (
    CONF_ADMIN_USER_ID,
    CONF_LOCK_PUBLIC_KEY,
    CONF_NICKNAME,
    CONF_SERIAL,
    CONF_SHORT_USER_ID,
)
from .eufy_ble.protocol import PARAM_NAMES, decode_params

TO_REDACT = {
    CONF_ADDRESS,
    CONF_ADMIN_USER_ID,
    CONF_EMAIL,
    CONF_LOCK_PUBLIC_KEY,
    CONF_NAME,
    CONF_NICKNAME,
    CONF_PASSWORD,
    CONF_SERIAL,
    CONF_SHORT_USER_ID,
    "access_token",
    "auth_token",
    "token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EufyLockConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    info = coordinator.info
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "state": {
            "is_locked": coordinator.is_locked,
            "battery": coordinator.battery,
            "connected": coordinator.connected,
            "link_up": coordinator.link_up,
            "connected_since": (
                coordinator.connected_since.isoformat() if coordinator.connected_since else None
            ),
            "disconnects": coordinator.disconnects,
            "keep_connected": coordinator.keep_connected,
            "rssi": coordinator.rssi,
            "last_seen": coordinator.last_seen.isoformat() if coordinator.last_seen else None,
            "last_update_success": coordinator.last_update_success,
            "firmware": info.firmware if info else None,
            "hardware": info.hardware if info else None,
            "params": decode_params(
                {tag: raw for tag, raw in coordinator.params.items() if tag in PARAM_NAMES}
            ),
            # Access history contains household names and routines. Keep only
            # operational health here; never attach the cached records or raw bytes.
            "history": {
                "status": coordinator.history_status,
                "baseline_complete": coordinator.history.initialized,
                "cached_records": len(coordinator.history.records),
            },
        },
    }
