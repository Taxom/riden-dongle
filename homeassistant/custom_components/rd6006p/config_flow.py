"""UI configuration; validation performs reads only."""

from __future__ import annotations

import math

try:
    import probatio as vol
except ImportError:  # Older Home Assistant releases use voluptuous.
    import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import NumberSelector, NumberSelectorConfig, NumberSelectorMode

from .const import (
    CONF_EXTERNAL_PROBE,
    CONF_MAX_CURRENT,
    CONF_MAX_VOLTAGE,
    CONF_POLL,
    CONF_SERIAL,
    CONF_SLOW_POLL,
    CONF_TIMEOUT,
    CONF_UNIT,
    DEFAULT_HOST,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MAX_VOLTAGE,
    DEFAULT_POLL,
    DEFAULT_PORT,
    DEFAULT_SLOW_POLL,
    DEFAULT_TIMEOUT,
    DEFAULT_UNIT,
    DOMAIN,
    MAX_CURRENT,
    MAX_VOLTAGE,
)
from .protocol import IdentityError, RidenClient, RidenError


class FiniteNumberSelector(NumberSelector):
    """A UI-serializable numeric selector which also rejects NaN and infinity."""

    def __call__(self, value):
        number = super().__call__(value)
        if not math.isfinite(number):
            raise vol.Invalid("Enter a finite number")
        return number


def finite_range(minimum, maximum):
    return FiniteNumberSelector(
        NumberSelectorConfig(min=minimum, max=maximum, step="any", mode=NumberSelectorMode.BOX)
    )


def connection_schema(defaults=None):
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required("host", default=defaults.get("host", DEFAULT_HOST)): str,
            vol.Required("port", default=defaults.get("port", DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_UNIT, default=defaults.get(CONF_UNIT, DEFAULT_UNIT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=247)
            ),
        }
    )


def options_schema(defaults):
    return vol.Schema(
        {
            vol.Required(
                CONF_MAX_VOLTAGE, default=defaults.get(CONF_MAX_VOLTAGE, DEFAULT_MAX_VOLTAGE)
            ): finite_range(0.001, MAX_VOLTAGE),
            vol.Required(
                CONF_MAX_CURRENT, default=defaults.get(CONF_MAX_CURRENT, DEFAULT_MAX_CURRENT)
            ): finite_range(0.0001, MAX_CURRENT),
            vol.Required(CONF_POLL, default=defaults.get(CONF_POLL, DEFAULT_POLL)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=60)
            ),
            vol.Required(
                CONF_SLOW_POLL, default=defaults.get(CONF_SLOW_POLL, DEFAULT_SLOW_POLL)
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
            vol.Required(
                CONF_TIMEOUT, default=defaults.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
            ): finite_range(2, 15),
            vol.Required(
                CONF_EXTERNAL_PROBE, default=defaults.get(CONF_EXTERNAL_PROBE, False)
            ): bool,
        }
    )


async def probe(data) -> dict:
    """Check model/identity without writing any register."""
    host = data["host"].strip()
    if not host or any(c.isspace() for c in host) or any(c in host for c in "/?#@"):
        raise ValueError("Enter a host name or IP address, without http://")
    client = RidenClient(host, data["port"], data[CONF_UNIT], DEFAULT_TIMEOUT)
    try:
        await client.read_registers(8, 13)
        return {**data, "host": host, CONF_SERIAL: client.identity["serial"]}
    finally:
        await client.close()


class RidenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return RidenOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = await probe(user_input)
            except IdentityError:
                errors["base"] = "wrong_model"
            except RidenError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["host"] = "invalid_host"
            else:
                await self.async_set_unique_id(f"60065_{data[CONF_SERIAL]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="RD6006P", data=data)
        return self.async_show_form(
            step_id="user", data_schema=connection_schema(user_input), errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            try:
                data = await probe(user_input)
            except IdentityError:
                errors["base"] = "wrong_model"
            except RidenError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["host"] = "invalid_host"
            else:
                if data[CONF_SERIAL] != entry.data[CONF_SERIAL]:
                    errors["base"] = "wrong_serial"
                else:
                    return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=connection_schema(user_input or entry.data),
            errors=errors,
        )


class RidenOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=options_schema(self.config_entry.options)
        )
