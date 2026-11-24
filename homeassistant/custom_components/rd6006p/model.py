"""RD6006P register decoding, independent of Home Assistant."""

from .protocol import RegisterBlock


def decode_fast(block: RegisterBlock) -> dict:
    r = dict(enumerate(block.values, start=block.address))
    return {
        "voltage_set": r[8] / 1000,
        "current_set": r[9] / 10000,
        "voltage": r[10] / 1000,
        "current": r[11] / 10000,
        "power": ((r[12] << 16) | r[13]) / 1000,
        "input_voltage": r[14] / 100,
        "keypad_locked": r[15] != 0,
        "protection": {0: "normal", 1: "ovp", 2: "ocp"}.get(r[16], "unknown"),
        "protection_raw": r[16],
        "output_mode": {0: "cv", 1: "cc"}.get(r[17], "unknown"),
        "output_mode_raw": r[17],
        "output": {0: False, 1: True}.get(r[18]),
        "output_raw": r[18],
        "preset": r[19],
        "current_range_raw": r[20],
    }


def decode_identity(block: RegisterBlock) -> dict:
    r = block.values
    return {
        "model": r[0],
        "serial": r[1] << 16 | r[2],
        "firmware": r[3],
        "internal_temperature": (-1 if r[4] else 1) * r[5],
    }


def decode_battery(block: RegisterBlock, external_probe: bool) -> dict:
    r = block.values
    temperature = (-1 if r[2] else 1) * r[3]
    # There is no documented, reliable probe-connected flag. Hide the optional
    # probe by default. If enabled, suppress only the observed -89 sentinel.
    return {
        "battery_mode": r[0],
        # Register 33 uses 0.01 V even on RD6006P with 0.001 V output readings.
        "battery_voltage": r[1] / 100,
        "external_temperature": temperature if external_probe and temperature != -89 else None,
        "external_temperature_raw": temperature,
        "charge": ((r[6] << 16) | r[7]) / 1000,
        "energy": ((r[8] << 16) | r[9]) / 1000,
    }
