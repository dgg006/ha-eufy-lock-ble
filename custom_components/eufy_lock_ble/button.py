"""Capture controls and physical-test annotations; no marker actuates the lock."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EufyLockConfigEntry
from .coordinator import EufyLockCoordinator
from .entity import EufyLockEntity

CAPTURE_BUTTONS = (
    "capture_start", "capture_stop", "capture_fingerprint", "capture_passcode",
    "capture_physical_key", "capture_thumbturn",
)


async def async_setup_entry(
    hass: HomeAssistant, entry: EufyLockConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add local diagnostic controls, usable when the lock is disconnected."""
    async_add_entities([EufyCaptureButton(entry.runtime_data, key) for key in CAPTURE_BUTTONS])


class EufyCaptureButton(EufyLockEntity, ButtonEntity):
    """Record a diagnostic action, not a physical door command."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:bluetooth"

    def __init__(self, coordinator: EufyLockCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._key = key
        self._attr_translation_key = key

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        if self._key == "capture_start":
            await self.coordinator.async_start_capture()
        elif self._key == "capture_stop":
            await self.coordinator.capture.async_stop()
        elif self.coordinator.capture.active:
            self.coordinator.capture.mark(self._key.removeprefix("capture_"))
        else:
            raise HomeAssistantError("Start the Bluetooth capture before adding a marker")
