"""Absolute output ON/OFF, without optimistic state or write retries."""

from homeassistant.components.switch import SwitchEntity

from .coordinator import RidenConfigEntry
from .entity import RidenEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry: RidenConfigEntry, async_add_entities) -> None:
    async_add_entities([RidenOutput(entry.runtime_data)])


class RidenOutput(RidenEntity, SwitchEntity):
    _attr_translation_key = "output"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "output")

    @property
    def available(self):
        return super().available and self.coordinator.data.get("output") is not None

    @property
    def is_on(self):
        return self.coordinator.data.get("output")

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_command(18, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_command(18, False)
