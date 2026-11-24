"""Shared polling and confirmed controls for all RD6006P entities."""

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_EXTERNAL_PROBE,
    CONF_MAX_CURRENT,
    CONF_MAX_VOLTAGE,
    CONF_POLL,
    CONF_SERIAL,
    CONF_SLOW_POLL,
    CONF_TIMEOUT,
    CONF_UNIT,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MAX_VOLTAGE,
    DEFAULT_POLL,
    DEFAULT_SLOW_POLL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    MAX_CURRENT,
    MAX_VOLTAGE,
)
from .model import decode_battery, decode_fast, decode_identity
from .protocol import RegisterBlock, RidenClient, RidenError, encode_setpoint

_LOGGER = logging.getLogger(__name__)


class RidenCoordinator(DataUpdateCoordinator[dict]):
    """Poll without overlap, with slower groups and command priority."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        options = entry.options
        self.entry = entry
        self.max_voltage = options.get(CONF_MAX_VOLTAGE, DEFAULT_MAX_VOLTAGE)
        self.max_current = options.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT)
        self.external_probe = options.get(CONF_EXTERNAL_PROBE, False)
        self.slow_interval = options.get(CONF_SLOW_POLL, DEFAULT_SLOW_POLL)
        self.client = RidenClient(
            entry.data["host"],
            entry.data["port"],
            entry.data[CONF_UNIT],
            options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
            entry.data[CONF_SERIAL],
            task_factory=lambda coro: hass.async_create_background_task(coro, "RD6006P Modbus"),
        )
        self._values: dict = {"identity_ok": False, "battery_ok": False}
        self._fast_time = 0.0
        self._slow_time = 0.0
        self.last_command: dict = {}
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=options.get(CONF_POLL, DEFAULT_POLL)),
        )
        # Entity properties may be evaluated before any successful read. This
        # empty snapshot contains no invented measurements or output state.
        self.data = dict(self._values)

    def _accept_fast(self, block: RegisterBlock) -> None:
        # A poll begun before a command must not overwrite the newer readback.
        if block.timestamp >= self._fast_time:
            self._values.update(decode_fast(block))
            self._fast_time = block.timestamp

    async def _async_update_data(self) -> dict:
        try:
            self._accept_fast(await self.client.read_registers(8, 13))
        except RidenError as err:
            raise UpdateFailed(str(err)) from err
        if time.monotonic() - self._slow_time >= self.slow_interval:
            self._slow_time = time.monotonic()
            try:
                block = await self.client.read_registers(0, 6)
            except RidenError:
                self._values["identity_ok"] = False
            else:
                self._values.update(decode_identity(block))
                self._values["identity_ok"] = True
            try:
                block = await self.client.read_registers(32, 10)
            except RidenError:
                self._values["battery_ok"] = False
            else:
                self._values.update(decode_battery(block, self.external_probe))
                self._values["battery_ok"] = True
        return dict(self._values)

    async def async_command(self, address: int, value: float | bool) -> None:
        try:
            if address == 8:
                raw = encode_setpoint(value, 1000, self.max_voltage, MAX_VOLTAGE)
            elif address == 9:
                raw = encode_setpoint(value, 10000, self.max_current, MAX_CURRENT)
            elif address == 18 and type(value) is bool:
                raw = int(value)
            else:
                raise ValueError("Unsupported control")
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        try:
            result = await self.client.write_confirmed(
                address, raw, self.max_voltage, self.max_current
            )
        except (RidenError, ValueError) as err:
            self.last_command = {
                "register": address,
                "requested_raw": raw,
                "result": "unconfirmed",
                "error": str(err),
            }
            # Preserve last known data internally; stop presenting it as fresh.
            self.async_set_update_error(UpdateFailed(str(err)))
            raise HomeAssistantError(
                f"RD6006P: {err}. State is unconfirmed; command was not retried."
            ) from err
        self._accept_fast(result.block)
        self.last_command = {
            "register": address,
            "requested_raw": raw,
            "actual_raw": result.block.values[address - 8],
            "result": "confirmed" if result.confirmed else "different",
            "ack_error": result.ack_error,
        }
        self.async_set_updated_data(dict(self._values))
        if not result.confirmed:
            raise HomeAssistantError(
                f"RD6006P: readback differs (requested {raw}, received "
                f"{result.block.values[address - 8]}). Actual state is displayed; no retry."
            )


type RidenConfigEntry = ConfigEntry[RidenCoordinator]
