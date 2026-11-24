"""Local diagnostic export; no extra HTTP request or UART traffic."""

from .const import CONF_SERIAL, VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    c = entry.runtime_data
    return {
        "integration_version": VERSION,
        "options": dict(entry.options),
        "available": c.last_update_success,
        "last_command": dict(c.last_command),
        "client_counters": dict(c.client.stats),
        "last_known_values": {k: v for k, v in (c.data or {}).items() if k != CONF_SERIAL},
        "note": "Values are last-known when unavailable. Host and serial omitted. No ESP HTTP diagnostics polled.",
    }
