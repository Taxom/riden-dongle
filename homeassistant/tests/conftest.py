"""Wire-level PSU/bridge simulator. Tests never contact the user's device."""

import asyncio
import struct
import sys
from contextlib import suppress
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakePSU:
    def __init__(self):
        self.registers = [0] * 300
        self.registers[:6] = [60065, 0, 3111, 123, 0, 28]
        self.registers[8:21] = [2600, 500, 0, 0, 0, 0, 6500, 0, 0, 0, 0, 0, 0]
        self.registers[34:36] = [1, 89]
        self.requests = []
        self.connections = 0
        self.writers = set()
        self.tasks = set()
        self.write_behavior = "normal"
        self.read_behavior = "normal"
        self.fail_address = None
        self.gate = None

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        self.connections += 1
        try:
            while True:
                header = await reader.readexactly(7)
                tid, protocol, length, unit = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                function = pdu[0]
                address, value = struct.unpack(">HH", pdu[1:])
                self.requests.append((function, address, value))
                if self.gate is not None and address == 8 and function == 3:
                    await self.gate.wait()
                if function == 3:
                    if self.fail_address == address:
                        writer.close()
                        return
                    values = self.registers[address : address + value]
                    response = bytes([3, value * 2]) + struct.pack(f">{value}H", *values)
                    mode, self.read_behavior = self.read_behavior, "normal"
                    if mode == "tid":
                        tid += 1
                    if mode == "pid":
                        protocol = 1
                    if mode == "unit":
                        unit += 1
                    if mode == "function":
                        response = bytes([4]) + response[1:]
                    if mode == "bytecount":
                        response = bytes([3, 0]) + response[2:]
                    if mode == "exception":
                        response = bytes([0x83, 0x0B])
                    if mode == "truncate":
                        writer.write(header[:3])
                        await writer.drain()
                        writer.close()
                        return
                elif function == 6:
                    mode, self.write_behavior = self.write_behavior, "normal"
                    if mode not in ("ignore", "reject"):
                        self.registers[address] = value
                    response = pdu
                    if mode == "reject":
                        response = bytes([0x86, 3])
                    if mode == "echo":
                        response = bytes([6]) + struct.pack(">HH", address, value + 1)
                    if mode in ("lost_ack", "ignore"):
                        writer.close()
                        return
                    if mode == "timeout":
                        await asyncio.sleep(0.4)
                    if mode == "overwrite":
                        self.registers[address] = 0
                else:
                    response = bytes([function | 0x80, 1])
                writer.write(
                    struct.pack(">HHHB", tid, protocol, len(response) + 1, unit) + response
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            self.tasks.discard(task)

    async def disconnect(self):
        for writer in list(self.writers):
            writer.close()
        await asyncio.sleep(0)


@pytest_asyncio.fixture
async def psu():
    device = FakePSU()
    server = await asyncio.start_server(device.handle, "127.0.0.1", 0)
    device.server = server
    device.port = server.sockets[0].getsockname()[1]
    yield device
    device.server.close()
    for writer in list(device.writers):
        writer.close()
    for task in list(device.tasks):
        task.cancel()
    await asyncio.gather(*device.tasks, return_exceptions=True)
    await device.server.wait_closed()


@pytest.fixture
def connection_data(psu):
    return {"host": "127.0.0.1", "port": psu.port, "unit": 1, "serial": 3111}
