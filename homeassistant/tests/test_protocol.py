import asyncio
from decimal import Decimal

import pytest

from custom_components.rd6006p.model import decode_battery, decode_fast, decode_identity
from custom_components.rd6006p.protocol import (
    BusyError,
    IdentityError,
    RegisterBlock,
    RidenClient,
    RidenError,
    encode_setpoint,
)


@pytest.mark.parametrize("raw", [0, 1, 417, 6010, 65535])
def test_battery_centivolts_independent_of_output_scale(raw):
    battery = decode_battery(RegisterBlock(32, [1, raw, 0, 16, 0, 0, 0, 3238, 0, 12088], 0), False)
    assert battery["battery_voltage"] == raw / 100
    assert battery["charge"] == 3.238
    assert battery["energy"] == 12.088
    output = decode_fast(RegisterBlock(8, [4150, 5000, 4148, 2187, 0, 907, 6574, 0, 0, 0, 1, 0, 0], 0))
    assert output["voltage"] == 4.148
    assert output["current"] == 0.2187


@pytest.mark.parametrize(
    "value,scale,limit,result",
    [
        (2.6, 1000, 3, 2600),
        (0.05, 10000, 0.1, 500),
        (0.0001, 10000, 0.1, 1),
        (0, 10000, 0.1, 0),
        (2.6005, 1000, 3, 2601),
        (Decimal("0.0003"), 10000, 0.1, 3),
    ],
)
def test_decimal_encoding(value, scale, limit, result):
    assert encode_setpoint(value, scale, limit, 60.1) == result


@pytest.mark.parametrize(
    "value", [-0.001, float("nan"), float("inf"), float("-inf"), "oops", True, 3.00001]
)
def test_bad_input(value):
    with pytest.raises(ValueError):
        encode_setpoint(value, 1000, 3, 60.1)


async def test_initial_read_zero_and_persistent_connection(psu):
    client = RidenClient("127.0.0.1", psu.port, expected_serial=3111)
    try:
        first = decode_fast(await client.read_registers(8, 13))
        assert first["voltage_set"] == 2.6 and first["current_set"] == 0.05
        assert first["voltage"] == 0 and first["output"] is False
        assert decode_identity(await client.read_registers(0, 6))["serial"] == 3111
        battery = decode_battery(await client.read_registers(32, 10), True)
        assert battery["external_temperature"] is None
        assert psu.connections == 1
        assert all(fc == 3 and count <= 20 for fc, _, count in psu.requests)
    finally:
        await client.close()


@pytest.mark.parametrize("address,value", [(8, 2601), (9, 501), (18, 1), (18, 0), (8, 0), (9, 0)])
async def test_absolute_write_and_readback(psu, address, value):
    client = RidenClient("127.0.0.1", psu.port)
    try:
        result = await client.write_confirmed(address, value, 3, 0.1)
        assert result.confirmed
        assert psu.requests[-1] == (3, 8, 13)
        assert [r for r in psu.requests if r[0] == 6] == [(6, address, value)]
    finally:
        await client.close()


@pytest.mark.parametrize(
    "behavior,confirmed",
    [
        ("lost_ack", True),
        ("timeout", True),
        ("echo", True),
        ("ignore", False),
        ("reject", False),
        ("overwrite", False),
    ],
)
async def test_lost_ack_never_replays_write(psu, behavior, confirmed):
    psu.write_behavior = behavior
    client = RidenClient("127.0.0.1", psu.port, timeout=0.1)
    try:
        result = await client.write_confirmed(8, 2700, 3, 0.1)
        assert result.confirmed is confirmed
        assert len([r for r in psu.requests if r[0] == 6]) == 1
        assert psu.requests[-1] == (3, 8, 13)
        if behavior in ("lost_ack", "timeout", "echo"):
            assert result.ack_error is not None
    finally:
        await client.close()


@pytest.mark.parametrize(
    "mode", ["tid", "pid", "unit", "function", "bytecount", "exception", "truncate"]
)
async def test_bad_read_is_error_not_zero_and_recovers(psu, mode):
    client = RidenClient("127.0.0.1", psu.port)
    try:
        await client.read_registers(8, 13)
        psu.read_behavior = mode
        with pytest.raises(RidenError):
            await client.read_registers(8, 13)
        assert decode_fast(await client.read_registers(8, 13))["voltage_set"] == 2.6
        assert psu.connections == 2
    finally:
        await client.close()


async def test_external_changes(psu):
    client = RidenClient("127.0.0.1", psu.port)
    try:
        await client.read_registers(8, 13)
        psu.registers[8:12] = [2800, 700, 2799, 699]
        psu.registers[16:19] = [2, 1, 1]
        data = decode_fast(await client.read_registers(8, 13))
        assert (data["voltage_set"], data["current_set"], data["voltage"], data["current"]) == (
            2.8,
            0.07,
            2.799,
            0.0699,
        )
        assert (data["protection"], data["output_mode"], data["output"]) == ("ocp", "cc", True)
    finally:
        await client.close()


async def test_identity_change_blocks_writes(psu):
    client = RidenClient("127.0.0.1", psu.port, expected_serial=3111)
    try:
        await client.read_registers(8, 13)
        await client._disconnect()
        psu.registers[2] = 3112
        with pytest.raises(IdentityError):
            await client.write_confirmed(8, 2700, 3, 0.1)
        assert not any(fc == 6 for fc, _, _ in psu.requests)
    finally:
        await client.close()


async def test_on_guard_off_always_allowed(psu):
    psu.registers[8] = 12000
    client = RidenClient("127.0.0.1", psu.port)
    try:
        with pytest.raises(RidenError, match="limits"):
            await client.write_confirmed(18, 1, 3, 0.1)
        assert not any(fc == 6 for fc, _, _ in psu.requests)
        assert (await client.write_confirmed(18, 0, 3, 0.1)).confirmed
    finally:
        await client.close()


@pytest.mark.parametrize(
    "address,value", [(55, 0), (66, 0), (80, 0), (48, 0), (256, 0), (8, 3001), (9, 1001), (18, 2)]
)
async def test_write_whitelist(psu, address, value):
    client = RidenClient("127.0.0.1", psu.port)
    try:
        with pytest.raises(ValueError):
            await client.write_confirmed(address, value, 3, 0.1)
        assert not psu.requests
    finally:
        await client.close()


async def test_command_priority_and_no_same_control_backlog(psu):
    client = RidenClient("127.0.0.1", psu.port)
    try:
        await client.read_registers(0, 6)
        psu.gate = asyncio.Event()
        first = asyncio.create_task(client.read_registers(8, 13))
        await asyncio.sleep(0.01)
        slow = asyncio.create_task(client.read_registers(32, 10))
        command = asyncio.create_task(client.write_confirmed(8, 2700, 3, 0.1))
        await asyncio.sleep(0.01)
        with pytest.raises(BusyError):
            await client.write_confirmed(8, 2800, 3, 0.1)
        psu.gate.set()
        await asyncio.gather(first, slow, command)
        assert psu.requests.index((6, 8, 2700)) < psu.requests.index((3, 32, 10))
        assert psu.connections == 1
    finally:
        await client.close()


def test_unknown_states_and_composite_words():
    block = RegisterBlock(8, (0, 0, 0, 0, 1, 2, 0, 0, 9, 7, 8, 0, 0), 1)
    data = decode_fast(block)
    assert data["power"] == 65.538
    assert data["output_mode"] == "unknown" and data["protection"] == "unknown"
    assert data["output"] is None
    battery = RegisterBlock(32, (0, 2600, 1, 5, 0, 0, 1, 2, 3, 4), 1)
    assert decode_battery(battery, True)["external_temperature"] == -5
    assert decode_battery(battery, False)["external_temperature"] is None


async def test_shutdown_discards_queued_commands(psu):
    client = RidenClient("127.0.0.1", psu.port)
    await client.read_registers(0, 6)
    psu.gate = asyncio.Event()
    reading = asyncio.create_task(client.read_registers(8, 13))
    await asyncio.sleep(0.01)
    command = asyncio.create_task(client.write_confirmed(8, 2700, 3, 0.1))
    await asyncio.sleep(0.01)
    await client.close()
    results = await asyncio.gather(reading, command, return_exceptions=True)
    assert all(isinstance(result, RidenError) for result in results)
    assert not any(fc == 6 for fc, _, _ in psu.requests)
    assert not client._pending_commands


async def test_two_external_readers_with_ha_control(psu):
    ha = RidenClient("127.0.0.1", psu.port)
    readers = [RidenClient("127.0.0.1", psu.port) for _ in range(2)]
    try:

        async def watch(client):
            for _ in range(15):
                data = decode_fast(await client.read_registers(8, 13))
                assert data["voltage_set"] in (2.6, 2.7)
                await asyncio.sleep(0.001)

        result, *_ = await asyncio.gather(
            ha.write_confirmed(8, 2700, 3, 0.1), *(watch(client) for client in readers)
        )
        assert result.confirmed and psu.connections == 3
        for reader in readers:
            assert (await reader.read_registers(8, 1)).values == (2700,)
    finally:
        await asyncio.gather(*(c.close() for c in [ha, *readers]))
