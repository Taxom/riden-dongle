"""Measurements, read-only state and battery session counters."""

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)

from .coordinator import RidenConfigEntry
from .entity import RidenEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class RidenSensorDescription(SensorEntityDescription):
    group: str = "fast"


SENSORS = (
    RidenSensorDescription(
        key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
    ),
    RidenSensorDescription(
        key="current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
    ),
    RidenSensorDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
    ),
    RidenSensorDescription(
        key="input_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    RidenSensorDescription(
        key="output_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["cv", "cc", "unknown"],
        icon="mdi:sine-wave",
    ),
    RidenSensorDescription(
        key="protection",
        device_class=SensorDeviceClass.ENUM,
        options=["normal", "ovp", "ocp", "unknown"],
        icon="mdi:shield-check",
    ),
    RidenSensorDescription(
        key="internal_temperature",
        group="identity",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    RidenSensorDescription(
        key="battery_voltage",
        group="battery",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    RidenSensorDescription(
        key="external_temperature",
        group="battery",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    # These resettable session counters deliberately have NO total_increasing.
    RidenSensorDescription(
        key="charge",
        group="battery",
        native_unit_of_measurement="Ah",
        suggested_display_precision=3,
    ),
    RidenSensorDescription(
        key="energy",
        group="battery",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=3,
    ),
    RidenSensorDescription(
        key="preset",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    RidenSensorDescription(
        key="battery_mode",
        group="battery",
        device_class=SensorDeviceClass.ENUM,
        options=["off", "on", "unknown"],
        icon="mdi:battery-charging",
    ),
)


async def async_setup_entry(hass, entry: RidenConfigEntry, async_add_entities) -> None:
    async_add_entities(RidenSensor(entry.runtime_data, description) for description in SENSORS)


class RidenSensor(RidenEntity, SensorEntity):
    entity_description: RidenSensorDescription

    def __init__(self, coordinator, description) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._attr_translation_key = description.key

    @property
    def available(self):
        group = self.entity_description.group
        return super().available and (
            group == "fast" or self.coordinator.data.get(f"{group}_ok", False)
        )

    @property
    def native_value(self):
        if self.key == "battery_mode":
            return {0: "off", 1: "on"}.get(self.coordinator.data.get(self.key), "unknown")
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self.key in ("protection", "output_mode"):
            attrs["raw"] = self.coordinator.data.get(f"{self.key}_raw")
        elif self.key == "battery_mode":
            attrs["raw"] = self.coordinator.data.get(self.key)
        return attrs
