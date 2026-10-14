"""Shared entity base."""

from __future__ import annotations

from homeassistant.const import CONF_NAME
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODELS, model_id_from_serial
from .coordinator import EufyLockCoordinator


class EufyLockEntity(CoordinatorEntity[EufyLockCoordinator]):
    """Base entity bound to one lock."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EufyLockCoordinator, key: str) -> None:
        """Initialise with a stable unique id derived from the serial."""
        super().__init__(coordinator)
        serial = coordinator.credentials.serial
        model_id = model_id_from_serial(serial)
        self._attr_unique_id = f"{serial}_{key}" if key else serial
        info = coordinator.info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODELS.get(model_id, model_id),
            model_id=model_id,
            name=coordinator.config_entry.data.get(CONF_NAME) or coordinator.config_entry.title,
            serial_number=serial,
            sw_version=info.firmware if info else None,
            hw_version=info.hardware if info else None,
        )

    @property
    def available(self) -> bool:
        """Follow the coordinator's notion of reachability."""
        return self.coordinator.available
