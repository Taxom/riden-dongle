"""Editable setpoints, always backed by a fresh device read."""

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential

from .coordinator import RidenConfigEntry
from .entity import RidenEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry: RidenConfigEntry, async_add_entities) -> None:
    async_add_entities(
        [
            RidenNumber(entry.runtime_data, "voltage_set", 8),
            RidenNumber(entry.runtime_data, "current_set", 9),
        ]
    )


class RidenNumber(RidenEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0

    def __init__(self, coordinator, key, address) -> None:
        super().__init__(coordinator, key)
        self.address = address
        self._attr_translation_key = key
        voltage = address == 8
        self._attr_native_step = 0.001 if voltage else 0.0001
        self._attr_native_max_value = (
            coordinator.max_voltage if voltage else coordinator.max_current
        )
        self._attr_native_unit_of_measurement = (
            UnitOfElectricPotential.VOLT if voltage else UnitOfElectricCurrent.AMPERE
        )
        self._attr_device_class = (
            NumberDeviceClass.VOLTAGE if voltage else NumberDeviceClass.CURRENT
        )

    @property
    def native_value(self):
        return self.coordinator.data.get(self.key)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_command(self.address, value)
