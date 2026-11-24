"""Communication and keypad status."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from .coordinator import RidenConfigEntry
from .entity import RidenEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry: RidenConfigEntry, async_add_entities) -> None:
    async_add_entities(
        [RidenBinary(entry.runtime_data, key) for key in ("connection", "keypad_locked")]
    )


class RidenBinary(RidenEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        if key == "connection":
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        else:
            self._attr_icon = "mdi:keyboard"

    @property
    def available(self):
        return True if self.key == "connection" else super().available

    @property
    def is_on(self):
        if self.key == "connection":
            return self.coordinator.last_update_success
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self.key == "connection":
            # Unlike measurement entities, connectivity remains available while
            # offline. The card can discover renamed entity IDs even on reload.
            registry = er.async_get(self.hass)
            attrs["entity_map"] = {
                entry.unique_id.removeprefix(f"{self.serial}_"): entry.entity_id
                for entry in er.async_entries_for_config_entry(
                    registry, self.coordinator.entry.entry_id
                )
                if entry.disabled_by is None and entry.unique_id
            }
        return attrs
