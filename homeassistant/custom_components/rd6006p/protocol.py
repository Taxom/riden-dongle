"""Small, strictly serialized Modbus TCP client for this device.

Only FC3 and FC6 are implemented. No write retry exists. A command and its
readback occupy one queue job; commands pass queued telemetry jobs. The bridge
already retries UART reads, so this client leaves failed reads for the next poll.
"""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import count
from typing import Any

from .const import MAX_CURRENT, MAX_VOLTAGE, MODEL


class RidenError(Exception):
    """An unavailable, malformed or rejected transaction."""


class IdentityError(RidenError):
    """The endpoint is not the configured RD6006P."""


class BusyError(RidenError):
    """Another command for the same register is still pending."""


class ModbusError(RidenError):
    """A well-formed Modbus exception response."""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"Modbus exception 0x{code:02x}")


def encode_setpoint(value: Any, scale: int, limit: float, model_limit: float) -> int:
    """Validate before rounding; use decimal half-up rather than float truncation."""
    if isinstance(value, bool):
        raise ValueError("A numeric value is required")
    try:
        number, ceiling = Decimal(str(value)), Decimal(str(limit))
        maximum = min(ceiling, Decimal(str(model_limit)))
        if not number.is_finite() or not ceiling.is_finite() or maximum < 0:
            raise ValueError("A finite nonnegative number is required")
        if number < 0 or number > maximum:
            raise ValueError(f"Value must be between 0 and {maximum}")
        raw = int((number * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if not 0 <= raw <= 65535 or Decimal(raw) / scale > maximum:
            raise ValueError("Rounded value exceeds the limit")
        return raw
    except (InvalidOperation, TypeError, OverflowError) as err:
        raise ValueError("Invalid numeric value") from err


@dataclass(frozen=True)
class RegisterBlock:
    """Read values and the monotonic completion time of that read."""

    address: int
    values: tuple[int, ...]
    timestamp: float


@dataclass(frozen=True)
class CommandResult:
    """Actual readback, even when it differs from the requested state."""

    block: RegisterBlock
    address: int
    requested: int
    ack_error: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.block.values[self.address - self.block.address] == self.requested


@dataclass
class _Job:
    operation: Callable[[], Coroutine[Any, Any, Any]]
    future: asyncio.Future


class RidenClient:
    """One queue, one socket and at most one in-flight Modbus transaction."""

    def __init__(
        self,
        host: str,
        port: int = 502,
        unit: int = 1,
        timeout: float = 5,
        expected_serial: int | None = None,
        task_factory: Callable | None = None,
    ) -> None:
        self.host, self.port, self.unit = host, port, unit
        self.timeout, self.expected_serial = timeout, expected_serial
        self.identity: dict[str, int] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=8)
        self._sequence = count()
        self._transaction = 0
        self._worker: asyncio.Task | None = None
        self._task_factory = task_factory or asyncio.create_task
        self._closed = False
        self._pending_commands: set[int] = set()
        self._last_command = 0.0
        self.stats = {
            "connections": 0,
            "requests": 0,
            "errors": 0,
            "writes": 0,
            "recovered_write_acks": 0,
        }

    async def _submit(self, operation: Callable, priority: int = 10) -> Any:
        if self._closed:
            raise RidenError("Client is closed")
        future = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait((priority, next(self._sequence), _Job(operation, future)))
        except asyncio.QueueFull as err:
            raise BusyError("Request queue is full") from err
        if self._worker is None:
            self._worker = self._task_factory(self._run())
        return await future

    async def _run(self) -> None:
        while True:
            _, _, job = await self._queue.get()
            try:
                if job.future.cancelled():
                    continue
                try:
                    result = await job.operation()
                except asyncio.CancelledError:
                    if not job.future.done():
                        job.future.set_exception(RidenError("Client closed during request"))
                    raise
                except Exception as err:
                    if not job.future.done():
                        job.future.set_exception(err)
                else:
                    if not job.future.done():
                        job.future.set_result(result)
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        """Discard pending work. Shutdown never sends OFF or restored setpoints."""
        self._closed = True
        if self._worker:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
        while not self._queue.empty():
            _, _, job = self._queue.get_nowait()
            if not job.future.done():
                job.future.set_exception(RidenError("Client closed before request"))
            self._queue.task_done()
        await self._disconnect()

    async def _disconnect(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer:
            writer.close()
            with suppress(OSError, TimeoutError):
                async with asyncio.timeout(1):
                    await writer.wait_closed()

    async def _connect(self) -> None:
        if self._writer is not None and not self._writer.is_closing():
            return
        async with asyncio.timeout(self.timeout):
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        self.stats["connections"] += 1
        # Verify identity again after every reconnect, BEFORE any command.
        reply = await self._packet(3, struct.pack(">HH", 0, 4))
        values = self._decode_read(reply, 4)
        serial = values[1] << 16 | values[2]
        if values[0] != MODEL:
            raise IdentityError(f"Expected model {MODEL}, received {values[0]}")
        if self.expected_serial is not None and serial != self.expected_serial:
            raise IdentityError("The serial number at this address has changed")
        self.identity = {"model": values[0], "serial": serial, "firmware": values[3]}

    async def _packet(self, function: int, payload: bytes) -> bytes:
        assert self._writer is not None and self._reader is not None
        self._transaction = (self._transaction + 1) & 0xFFFF
        tid = self._transaction
        pdu = bytes([function]) + payload
        self.stats["requests"] += 1
        async with asyncio.timeout(self.timeout):
            self._writer.write(struct.pack(">HHHB", tid, 0, len(pdu) + 1, self.unit) + pdu)
            await self._writer.drain()
            header = await self._reader.readexactly(7)
            r_tid, protocol, length, unit = struct.unpack(">HHHB", header)
            if r_tid != tid or protocol != 0 or unit != self.unit or not 2 <= length <= 254:
                raise RidenError("Invalid Modbus TCP header")
            response = await self._reader.readexactly(length - 1)
        if response[0] == (function | 0x80):
            if len(response) != 2:
                raise RidenError("Invalid Modbus exception length")
            raise ModbusError(response[1])
        if response[0] != function:
            raise RidenError("Unexpected Modbus function")
        return response[1:]

    async def _exchange(self, function: int, payload: bytes) -> bytes:
        try:
            await self._connect()
            return await self._packet(function, payload)
        except (OSError, TimeoutError, asyncio.IncompleteReadError, RidenError) as err:
            self.stats["errors"] += 1
            await self._disconnect()
            if isinstance(err, RidenError):
                raise
            raise RidenError(f"Modbus connection failed: {type(err).__name__}") from err

    @staticmethod
    def _decode_read(payload: bytes, count_: int) -> tuple[int, ...]:
        if len(payload) != 1 + 2 * count_ or payload[0] != 2 * count_:
            raise RidenError("Incorrect Modbus register byte count")
        return struct.unpack(f">{count_}H", payload[1:])

    async def _read(self, address: int, count_: int) -> RegisterBlock:
        payload = await self._exchange(3, struct.pack(">HH", address, count_))
        try:
            values = self._decode_read(payload, count_)
        except RidenError:
            await self._disconnect()
            raise
        return RegisterBlock(address, values, time.monotonic())

    async def read_registers(self, address: int, count_: int) -> RegisterBlock:
        if not 1 <= count_ <= 20 or not 0 <= address <= 65536 - count_:
            raise ValueError("Read must contain 1–20 valid holding registers")
        return await self._submit(lambda: self._read(address, count_))

    async def write_confirmed(
        self, address: int, value: int, max_voltage: float, max_current: float
    ) -> CommandResult:
        """Send FC6 once, then FC3. Even a valid ACK is never optimistic state."""
        if address not in (8, 9, 18) or type(value) is not int:
            raise ValueError("Only integer writes to registers 8, 9 and 18 are allowed")
        limit = {
            8: int(Decimal(str(min(MAX_VOLTAGE, max_voltage))) * 1000),
            9: int(Decimal(str(min(MAX_CURRENT, max_current))) * 10000),
            18: 1,
        }[address]
        if not 0 <= value <= limit:
            raise ValueError("Write exceeds the configured limit")
        if address in self._pending_commands:
            raise BusyError("Wait for the previous command to this control")
        self._pending_commands.add(address)
        started = False

        async def operation() -> CommandResult:
            nonlocal started
            started = True
            try:
                # Limit command traffic; do not create a slider write backlog.
                delay = 0.25 - (time.monotonic() - self._last_command)
                if delay > 0:
                    await asyncio.sleep(delay)
                if address == 18 and value == 1:
                    before = await self._read(8, 2)
                    if (
                        before.values[0] / 1000 > max_voltage
                        or before.values[1] / 10000 > max_current
                    ):
                        raise RidenError("Read setpoints exceed HA limits; ON was not sent")
                ack_error = None
                try:
                    payload = struct.pack(">HH", address, value)
                    self.stats["writes"] += 1
                    reply = await self._exchange(6, payload)
                    if reply != payload:
                        await self._disconnect()
                        raise RidenError("Write echo does not match")
                except IdentityError:
                    raise
                except RidenError as err:
                    ack_error = str(err)
                finally:
                    self._last_command = time.monotonic()
                # On a timeout _exchange closed the old socket; the new socket
                # rechecks identity. The write is NEVER resubmitted.
                block = await self._read(8, 13)
                result = CommandResult(block, address, value, ack_error)
                if result.confirmed and ack_error:
                    self.stats["recovered_write_acks"] += 1
                return result
            finally:
                self._pending_commands.discard(address)

        try:
            return await self._submit(operation, priority=0)
        finally:
            # A cancelled caller must not release an in-flight command's guard.
            if not started or self._closed:
                self._pending_commands.discard(address)
