"""Binary sensor platform: BLE session connectivity and on/off lock settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EufyLockConfigEntry
from .coordinator import EufyLockCoordinator
from .entity import EufyLockEntity
from .eufy_ble.protocol import decode_params


@dataclass(frozen=True, kw_only=True)
class EufyParamBinaryDescription(BinarySensorEntityDescription):
    """A read-only on/off lock setting from the decoded parameter block."""

    param: str


PARAM_BINARY_SENSORS: tuple[EufyParamBinaryDescription, ...] = tuple(
    EufyParamBinaryDescription(
        key=name,
        param=name,
        translation_key=name,
        entity_category=EntityCategory.DIAGNOSTIC,
    )
    for name in (
        "auto_lock",
        "auto_lock_schedule",
        "one_touch_lock",
        "wrong_try_protect",
        "scramble_passcode",
    )
)


async def async_setup_entry(
    hass: HomeAssistant, entry: EufyLockConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [EufyConnectedSensor(coordinator)]
    entities.extend(EufyParamBinarySensor(coordinator, desc) for desc in PARAM_BINARY_SENSORS)
    async_add_entities(entities)


class EufyConnectedSensor(EufyLockEntity, BinarySensorEntity):
    """On while an authenticated BLE session to the lock is open."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connected"

    def __init__(self, coordinator: EufyLockCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "connected")

    @property
    def available(self) -> bool:
        """Always available: 'off' is meaningful."""
        return True

    @property
    def is_on(self) -> bool:
        """Return the debounced connection state (see ``EufyLockCoordinator.link_up``)."""
        return self.coordinator.link_up

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw session state and drop statistics for diagnosis."""
        coordinator = self.coordinator
        return {
            "session_open": coordinator.connected,
            "connected_since": coordinator.connected_since,
            "disconnects": coordinator.disconnects,
        }


class EufyParamBinarySensor(EufyLockEntity, BinarySensorEntity):
    """One on/off lock setting."""

    entity_description: EufyParamBinaryDescription

    def __init__(
        self, coordinator: EufyLockCoordinator, description: EufyParamBinaryDescription
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Settings keep their last value while the lock is out of reach."""
        return self.entity_description.param in decode_params(self.coordinator.params)

    @property
    def is_on(self) -> bool | None:
        """Return the setting."""
        value = decode_params(self.coordinator.params).get(self.entity_description.param)
        return value if isinstance(value, bool) else None
