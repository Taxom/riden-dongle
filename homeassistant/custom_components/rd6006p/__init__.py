"""Local RD6006P control through the riden-dongle Modbus TCP bridge."""

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .coordinator import RidenConfigEntry, RidenCoordinator

PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SWITCH, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: RidenConfigEntry) -> bool:
    coordinator = RidenCoordinator(hass, entry)
    try:
        # A configured PSU may be powered off when HA starts. Keep its entities
        # registered and let normal polling recover, instead of deferring their
        # creation through ConfigEntryNotReady and the setup retry backoff.
        await coordinator.async_refresh()
    except BaseException:
        await coordinator.client.close()
        raise
    entry.runtime_data = coordinator
    # v0.1.0/0.1.1 created these as integration-disabled diagnostics. Enable
    # them for the battery card, while preserving any explicit user disable.
    registry = er.async_get(hass)
    battery_ids = {
        f"{entry.data['serial']}_{key}"
        for key in ("battery_mode", "battery_voltage", "charge", "energy")
    }
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.unique_id in battery_ids
            and entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        ):
            registry.async_update_entity(entity.entity_id, disabled_by=None)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def stop(_event) -> None:
        await coordinator.client.close()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        # Platforms register in parallel. Publish connectivity metadata again
        # after all entity IDs exist, including when the PSU is still offline
        # and repeated failed polls do not notify the coordinator's listeners.
        coordinator.async_update_listeners()
    except BaseException:
        await coordinator.client.close()
        raise
    return True


async def async_reload_entry(hass: HomeAssistant, entry: RidenConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: RidenConfigEntry) -> bool:
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.close()
    return unloaded
