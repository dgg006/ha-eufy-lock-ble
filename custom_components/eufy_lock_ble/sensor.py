"""Sensor platform: battery, RSSI and the lock's parameter block."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EufyLockConfigEntry
from .coordinator import EufyLockCoordinator
from .entity import EufyLockEntity
from .eufy_ble.protocol import SOUND_LEVELS, decode_params


@dataclass(frozen=True, kw_only=True)
class EufyParamSensorDescription(SensorEntityDescription):
    """A read-only lock setting taken from the decoded parameter block."""

    param: str


PARAM_SENSORS: tuple[EufyParamSensorDescription, ...] = (
    EufyParamSensorDescription(
        key="auto_lock_timer",
        param="auto_lock_timer",
        translation_key="auto_lock_timer",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EufyParamSensorDescription(
        key="auto_lock_schedule_start",
        param="auto_lock_schedule_start",
        translation_key="auto_lock_schedule_start",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EufyParamSensorDescription(
        key="auto_lock_schedule_end",
        param="auto_lock_schedule_end",
        translation_key="auto_lock_schedule_end",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EufyParamSensorDescription(
        key="wrong_try_lockdown",
        param="wrong_try_lockdown",
        translation_key="wrong_try_lockdown",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EufyParamSensorDescription(
        key="wrong_try_attempts",
        param="wrong_try_attempts",
        translation_key="wrong_try_attempts",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EufyParamSensorDescription(
        key="sound_volume",
        param="sound_volume",
        translation_key="sound_volume",
        device_class=SensorDeviceClass.ENUM,
        options=list(SOUND_LEVELS.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: EufyLockConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        EufyBatterySensor(coordinator),
        EufyRssiSensor(coordinator),
        EufyParamsSensor(coordinator),
    ]
    entities.extend(EufyParamSensor(coordinator, desc) for desc in PARAM_SENSORS)
    async_add_entities(entities)


class EufyParamSensor(EufyLockEntity, SensorEntity):
    """One decoded lock setting."""

    entity_description: EufyParamSensorDescription

    def __init__(
        self, coordinator: EufyLockCoordinator, description: EufyParamSensorDescription
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Settings keep their last value while the lock is out of reach."""
        return self.entity_description.param in decode_params(self.coordinator.params)

    @property
    def native_value(self) -> Any:
        """Return the decoded value."""
        value = decode_params(self.coordinator.params).get(self.entity_description.param)
        if self.entity_description.device_class == SensorDeviceClass.ENUM and value not in (
            self.entity_description.options or []
        ):
            return None
        return value


class EufyBatterySensor(EufyLockEntity, SensorEntity):
    """Battery level reported by the lock in every status frame."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: EufyLockCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "battery")

    @property
    def available(self) -> bool:
        """Keep the last reading while the lock is out of reach."""
        return self.coordinator.battery is not None

    @property
    def native_value(self) -> int | None:
        """Return the battery percentage."""
        return self.coordinator.battery


class EufyRssiSensor(EufyLockEntity, SensorEntity):
    """Signal strength as seen by the closest scanner."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "rssi"

    def __init__(self, coordinator: EufyLockCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "rssi")

    @property
    def available(self) -> bool:
        """RSSI is available whenever the lock has been seen."""
        return self.coordinator.rssi is not None

    @property
    def native_value(self) -> int | None:
        """Return the RSSI."""
        return self.coordinator.rssi


class EufyParamsSensor(EufyLockEntity, SensorEntity):
    """The lock's settings block (auto-lock, wrong-try protection, sound, ...)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "parameters"

    def __init__(self, coordinator: EufyLockCoordinator) -> None:
        """Initialise."""
        super().__init__(coordinator, "parameters")

    @property
    def native_value(self) -> int | None:
        """Number of parameter fields received."""
        return len(self.coordinator.params) or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Named, decoded settings plus the raw hex of every tag."""
        attrs: dict[str, Any] = dict(decode_params(self.coordinator.params))
        attrs["raw"] = {
            f"{tag:02x}": raw.hex() for tag, raw in sorted(self.coordinator.params.items())
        }
        return attrs
