"""Lock platform."""

from __future__ import annotations

import logging
from typing import Any

from bleak.exc import BleakError
from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EufyLockConfigEntry
from .const import DOMAIN
from .coordinator import EufyLockCoordinator, LockUnreachableError
from .entity import EufyLockEntity
from .eufy_ble import LockRefusedError, SessionError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: EufyLockConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the lock entity."""
    async_add_entities([EufyBleLock(entry.runtime_data)])


class EufyBleLock(EufyLockEntity, LockEntity):
    """The bolt."""

    _attr_name = None
    _attr_translation_key = "lock"

    def __init__(self, coordinator: EufyLockCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "")
        self._attr_is_locking = False
        self._attr_is_unlocking = False

    @property
    def is_locked(self) -> bool | None:
        """Return the bolt state."""
        return self.coordinator.is_locked

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose connection details."""
        return {
            "connected": self.coordinator.connected,
            "rssi": self.coordinator.rssi,
            "last_state_change": self.coordinator.last_state_change,
        }

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door."""
        await self._actuate(True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door."""
        await self._actuate(False)

    async def _actuate(self, lock: bool) -> None:
        self._attr_is_locking = lock
        self._attr_is_unlocking = not lock
        self.async_write_ha_state()
        try:
            await self.coordinator.async_set_locked(lock)
        except LockRefusedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="refused",
                translation_placeholders={"result": f"{err.result:#04x}"},
            ) from err
        except LockUnreachableError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="unreachable"
            ) from err
        except (SessionError, BleakError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            self._attr_is_locking = False
            self._attr_is_unlocking = False
            self.async_write_ha_state()
