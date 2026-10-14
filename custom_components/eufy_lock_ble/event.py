"""Access and alarm events read from the lock, without replaying startup history."""

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import EufyLockConfigEntry
from .coordinator import EufyLockCoordinator
from .entity import EufyLockEntity
from .eufy_ble.history import EVENT_NAMES, LockRecord
from .history import FRESH_EVENT_SECONDS


async def async_setup_entry(
    hass: HomeAssistant, entry: EufyLockConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add one event entity covering all observed record types."""
    async_add_entities([EufyLockEvent(entry.runtime_data)])


class EufyLockEvent(EufyLockEntity, EventEntity):
    """Each fresh access or alarm, with its original event code and user fields."""

    _attr_translation_key = "activity"
    _attr_event_types = [*EVENT_NAMES.values(), "unknown"]
    _attr_icon = "mdi:history"

    def __init__(self, coordinator: EufyLockCoordinator) -> None:
        super().__init__(coordinator, "activity")

    async def async_added_to_hass(self) -> None:
        """Subscribe only after HA can receive and display the entity events."""
        await super().async_added_to_hass()
        self.coordinator.history_listeners.append(self._on_record)
        self.async_on_remove(lambda: self.coordinator.history_listeners.remove(self._on_record))

    @callback
    def _on_record(self, record: LockRecord) -> None:
        data = record.as_dict()
        now = dt_util.utcnow()
        data["historical"] = now.timestamp() - record.timestamp > FRESH_EVENT_SECONDS
        data["received_at"] = now.isoformat()
        self._trigger_event(record.event_type, data)
        self.async_write_ha_state()
