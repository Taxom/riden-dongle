"""Run against real Home Assistant Core, not mocked entity base classes."""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import frame

from custom_components.rd6006p.config_flow import options_schema, probe
from custom_components.rd6006p.coordinator import RidenCoordinator
from custom_components.rd6006p.protocol import RegisterBlock


def make_entry(data, options=None):
    return ConfigEntry(
        domain="rd6006p",
        data=data,
        options=options or {},
        title="RD6006P",
        version=1,
        minor_version=1,
        source="user",
        unique_id="60065_3111",
        discovery_keys={},
        subentries_data=None,
    )


@pytest_asyncio.fixture
async def hass(tmp_path):
    (tmp_path / "custom_components").symlink_to(Path(__file__).parents[1] / "custom_components")
    instance = HomeAssistant(str(tmp_path))
    instance.config.skip_pip = True
    instance.config.language = "en"
    loader.async_setup(instance)
    frame.async_setup(instance)
    instance.config_entries = ConfigEntries(instance, {})
    await instance.config_entries.async_initialize()
    dr.async_setup(instance)
    await dr.async_load(instance)
    await er.async_load(instance)
    yield instance
    for entry in instance.config_entries.async_entries("rd6006p"):
        if getattr(entry, "runtime_data", None):
            await entry.runtime_data.client.close()
    pending = list(instance._tasks | instance._background_tasks)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    instance._cancel_cancellable_timers()
    instance.import_executor.shutdown(wait=False, cancel_futures=True)


async def test_probe_reads_only(psu, connection_data):
    data = await probe(connection_data)
    assert data["serial"] == 3111
    assert all(r[0] == 3 for r in psu.requests)


async def test_setup_entities_controls_and_unload(hass, psu, connection_data):
    entry = make_entry(connection_data)
    await hass.config_entries.async_add(entry)
    assert entry.state is ConfigEntryState.LOADED
    await hass.async_block_till_done()
    states = {
        s.attributes.get("rd6006p_key"): s
        for s in hass.states.async_all()
        if s.attributes.get("rd6006p_serial") == "3111"
    }
    assert states["voltage"].state == "0.0"
    assert states["current_set"].state == "0.05"
    assert states["output"].state == "off"
    assert states["connection"].state == "on"
    assert not any(fc == 6 for fc, _, _ in psu.requests)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": states["voltage_set"].entity_id, "value": 2.7},
        blocking=True,
    )
    assert hass.states.get(states["voltage_set"].entity_id).state == "2.7"
    psu.registers[9] = 700
    await entry.runtime_data.async_refresh()
    assert hass.states.get(states["current_set"].entity_id).state == "0.07"
    before = len([r for r in psu.requests if r[0] == 6])
    client = entry.runtime_data.client
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert len([r for r in psu.requests if r[0] == 6]) == before
    assert not client._pending_commands


async def test_config_flow_serial_duplicate_and_options(hass, psu, connection_data):
    form_data = {k: v for k, v in connection_data.items() if k != "serial"}
    result = await hass.config_entries.flow.async_init("rd6006p", context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form_data)
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    entry = result["result"]
    other = await hass.config_entries.flow.async_init(
        "rd6006p", context={"source": "user"}, data=form_data
    )
    assert other["reason"] == "already_configured"
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["type"] == "form"
    options = options_schema({})({})
    options["max_voltage"] = 5
    before = len([r for r in psu.requests if r[0] == 6])
    saved = await hass.config_entries.options.async_configure(form["flow_id"], options)
    assert saved["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.runtime_data.max_voltage == 5
    assert len([r for r in psu.requests if r[0] == 6]) == before
    await hass.config_entries.async_unload(entry.entry_id)


async def test_availability_failure_and_recovery(hass, psu, connection_data):
    entry = make_entry(connection_data)
    await hass.config_entries.async_add(entry)
    c = entry.runtime_data
    entity_ids = {s.attributes.get("rd6006p_key"): s.entity_id for s in hass.states.async_all()}
    psu.fail_address = 8
    await c.async_refresh()
    assert not c.last_update_success
    assert c.data["voltage_set"] == 2.6
    assert hass.states.get(entity_ids["voltage_set"]).state == "unavailable"
    assert hass.states.get(entity_ids["connection"]).state == "off"
    assert (
        hass.states.get(entity_ids["connection"]).attributes["entity_map"]["voltage_set"]
        == entity_ids["voltage_set"]
    )
    psu.fail_address = None
    psu.registers[8] = 0
    await c.async_refresh()
    assert c.last_update_success and c.data["voltage_set"] == 0
    assert not any(fc == 6 for fc, _, _ in psu.requests)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_confirmed_command_mismatch_and_lost_ack(hass, psu, connection_data):
    entry = make_entry(connection_data)
    await hass.config_entries.async_add(entry)
    c = entry.runtime_data
    psu.write_behavior = "lost_ack"
    await c.async_command(8, 2.7)
    assert c.data["voltage_set"] == 2.7 and c.last_command["result"] == "confirmed"
    psu.write_behavior = "overwrite"
    with pytest.raises(HomeAssistantError, match="differs"):
        await c.async_command(8, 2.8)
    assert c.data["voltage_set"] == 0
    assert len([r for r in psu.requests if r[0] == 6]) == 2
    await hass.config_entries.async_unload(entry.entry_id)


async def test_newer_readback_cannot_be_overwritten(hass, psu, connection_data):
    entry = make_entry(connection_data)
    c = RidenCoordinator(hass, entry)
    c._accept_fast(RegisterBlock(8, (2700, 500, 0, 0, 0, 0, 6500, 0, 0, 0, 0, 0, 0), 10))
    c._accept_fast(RegisterBlock(8, (2600, 500, 0, 0, 0, 0, 6500, 0, 0, 0, 0, 0, 0), 9))
    assert c._values["voltage_set"] == 2.7
    await c.client.close()


async def test_lost_ack_and_failed_read_stays_unconfirmed(hass, psu, connection_data):
    entry = make_entry(connection_data)
    await hass.config_entries.async_add(entry)
    c = entry.runtime_data
    psu.write_behavior = "lost_ack"
    psu.fail_address = 8
    with pytest.raises(HomeAssistantError, match="unconfirmed"):
        await c.async_command(8, 2.7)
    assert not c.last_update_success
    assert c.data["voltage_set"] == 2.6
    assert psu.registers[8] == 2700
    psu.fail_address = None
    await c.async_refresh()
    assert c.last_update_success and c.data["voltage_set"] == 2.7
    assert len([r for r in psu.requests if r[0] == 6]) == 1
    await hass.config_entries.async_unload(entry.entry_id)


async def test_slow_group_failure_isolated(hass, psu, connection_data):
    entry = make_entry(connection_data)
    await hass.config_entries.async_add(entry)
    c = entry.runtime_data
    c._slow_time = 0
    psu.fail_address = 32
    await c.async_refresh()
    assert c.last_update_success
    assert c.data["identity_ok"] and not c.data["battery_ok"]
    await hass.config_entries.async_unload(entry.entry_id)


def test_options_reject_nonfinite_and_serialize():
    import probatio
    from homeassistant.helpers.config_validation import custom_serializer

    assert len(probatio.to_field_list(options_schema({}), custom_serializer=custom_serializer)) == 6
    for key in ("max_voltage", "max_current", "timeout"):
        with pytest.raises(probatio.Invalid):
            options_schema({})({key: float("nan")})


async def wait_for_connection(hass, connection_id, expected):
    async with asyncio.timeout(5):
        while hass.states.get(connection_id).state != expected:
            await asyncio.sleep(0.01)


async def test_start_with_power_off_then_poll_recovers_without_writes(hass, psu, connection_data):
    # TCP connection refused, as when the ESP/PSU has no power at HA startup.
    psu.server.close()
    await psu.server.wait_closed()
    entry = make_entry(connection_data, {"poll_interval": 1, "max_voltage": 60.1, "max_current": 6})
    await hass.config_entries.async_add(entry)
    assert entry.state is ConfigEntryState.LOADED
    await hass.async_block_till_done()
    states = {s.attributes.get("rd6006p_key"): s for s in hass.states.async_all()}
    connection_id = states["connection"].entity_id
    assert states["connection"].state == "off"
    mapping = states["connection"].attributes["entity_map"]
    assert hass.states.get(mapping["output"]).state == "unavailable"
    assert hass.states.get(mapping["voltage_set"]).state == "unavailable"
    assert entry.runtime_data.data == {"identity_ok": False, "battery_ok": False}
    assert not psu.requests
    # Reading ON on recovery must not cause a defensive OFF or restored setpoint.
    psu.registers[18] = 1
    psu.server = await asyncio.start_server(psu.handle, "127.0.0.1", psu.port)
    await wait_for_connection(hass, connection_id, "on")
    assert hass.states.get(mapping["output"]).state == "on"
    assert hass.states.get(mapping["voltage_set"]).state == "2.6"
    assert hass.states.get(mapping["current_set"]).state == "0.05"
    assert entry.options["max_voltage"] == 60.1 and entry.options["max_current"] == 6
    assert all(fc == 3 for fc, _, _ in psu.requests)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reload_while_psu_off_keeps_entities_and_recovers(hass, psu, connection_data):
    entry = make_entry(connection_data, {"poll_interval": 1})
    await hass.config_entries.async_add(entry)
    states = {s.attributes.get("rd6006p_key"): s for s in hass.states.async_all()}
    connection_id = states["connection"].entity_id
    mapping = states["connection"].attributes["entity_map"]
    psu.fail_address = 0
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(connection_id).state == "off"
    assert hass.states.get(mapping["output"]).state == "unavailable"
    psu.fail_address = None
    await wait_for_connection(hass, connection_id, "on")
    assert hass.states.get(mapping["output"]).state == "off"
    assert all(fc == 3 for fc, _, _ in psu.requests)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_offline_setup_still_checks_identity_before_recovery(hass, psu, connection_data):
    psu.registers[2] = 3112
    entry = make_entry(connection_data, {"poll_interval": 1})
    await hass.config_entries.async_add(entry)
    assert entry.state is ConfigEntryState.LOADED
    states = {s.attributes.get("rd6006p_key"): s for s in hass.states.async_all()}
    assert states["connection"].state == "off"
    assert not entry.runtime_data.last_update_success
    psu.registers[2] = 3111
    await wait_for_connection(hass, states["connection"].entity_id, "on")
    assert entry.runtime_data.data["voltage_set"] == 2.6
    assert all(fc == 3 for fc, _, _ in psu.requests)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_battery_sensors_and_session_reset(hass, psu, connection_data):
    psu.registers[32:42] = [1, 417, 0, 16, 0, 0, 0, 3238, 0, 12088]
    entry = make_entry(connection_data)
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    ids = {
        key: registry.async_get_entity_id("sensor", "rd6006p", f"3111_{key}")
        for key in ("battery_mode", "battery_voltage", "charge", "energy")
    }
    for key, expected in {"battery_mode": "on", "battery_voltage": "4.17", "charge": "3.238", "energy": "12.088"}.items():
        assert registry.async_get(ids[key]).disabled_by is None
        assert hass.states.get(ids[key]).state == expected
    assert hass.states.get(ids["battery_mode"]).attributes["raw"] == 1
    assert "state_class" not in hass.states.get(ids["charge"]).attributes
    assert "state_class" not in hass.states.get(ids["energy"]).attributes
    psu.registers[32:42] = [0] * 10
    entry.runtime_data._slow_time = 0
    await entry.runtime_data.async_refresh()
    assert hass.states.get(ids["battery_mode"]).state == "off"
    for key in ("battery_voltage", "charge", "energy"):
        assert hass.states.get(ids[key]).state == "0.0"
    psu.fail_address = 32
    entry.runtime_data._slow_time = 0
    await entry.runtime_data.async_refresh()
    assert all(hass.states.get(entity_id).state == "unavailable" for entity_id in ids.values())
    assert entry.runtime_data.last_update_success
    assert all(fc == 3 for fc, _, _ in psu.requests)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_upgrade_enables_only_integration_disabled_battery_entities(hass, psu, connection_data):
    entry = make_entry(connection_data, {"max_voltage": 60.1, "max_current": 6})
    await hass.config_entries.async_add(entry)
    registry = er.async_get(hass)
    ids = {
        key: registry.async_get_entity_id("sensor", "rd6006p", f"3111_{key}")
        for key in ("battery_mode", "battery_voltage", "charge", "energy")
    }
    assert await hass.config_entries.async_unload(entry.entry_id)
    for key in ("battery_mode", "battery_voltage", "charge"):
        registry.async_update_entity(ids[key], disabled_by=er.RegistryEntryDisabler.INTEGRATION)
    registry.async_update_entity(ids["energy"], disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for key in ("battery_mode", "battery_voltage", "charge"):
        assert registry.async_get(ids[key]).disabled_by is None
        assert hass.states.get(ids[key]).state != "unavailable"
    assert registry.async_get(ids["energy"]).disabled_by is er.RegistryEntryDisabler.USER
    assert entry.options == {"max_voltage": 60.1, "max_current": 6}
    assert all(fc == 3 for fc, _, _ in psu.requests)
    await hass.config_entries.async_unload(entry.entry_id)
