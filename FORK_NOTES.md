# Taxom: shared UART polling and clock synchronization

Based on upstream v1.7.0, commit `e9a060958c9dcf82f24353e36c2282b057690be4`.
Candidate firmware version: **1.7.0-taxom.1**.

## Observed problem

RD6006P with UniSoft and the original ESP-12F module was stable with either
TestController Modbus TCP or the web Control page running alone. Running
both produced false peaks and unrelated register values in both clients.
For example, model ID 60065 appeared as a 60.065 V setpoint.

## Changes

- Send TCP replies using a separate MBAP/PDU buffer. The previous use of
  `rawResponce()` could free the TCP receive buffer while the next request
  was still using it during a wait for the previous UART transaction.
- Finish the pending bridge request on UART timeout/failure and remove its
  raw interceptor. A later web response must not be forwarded as that reply.
- Validate response function, slave, register byte count, and write echo.
  Invalid replies produce a gateway exception instead of fabricated readings.
- Preserve the requesting TCP client's address, transaction ID (including
  zero), and unit ID. The library supports up to four TCP clients with
  distinct IP addresses; its existing unique-IP connection policy remains.
- Reject non-PSU unit IDs, including broadcast zero, without installing an
  interceptor that could steal a later local reply. Configure clients for ID 1.
- Report local Modbus exceptions and timeouts as failures. Wait until the
  library has released its callback and destination buffer before returning.
- Synchronize the PSU clock after each NTP update, with a five-second retry
  interval for failed clock writes. A new NTP event during a write stays pending.
- Pin the platform and library versions used to build this candidate.

The single UART continues to serve requests in order. Modbus TCP waits for
an existing transaction to finish; HTTP/SCPI/local operations do the same.
No extra polling thread or background writer has been added.

## Validation

The host regression tests compile the actual `RidenModbus`,
`RidenModbusBridge`, and modbus-esp8266 v4.1.0 RTU implementation. UART bytes,
time, and TCP sockets are simulated. The TCP model reproduces the library's
shared receive-buffer ownership; it is not a complete TCP network stack.

Tests cover overlapping TCP requests, interleaved local/web reads, timeout
recovery, malformed byte counts, RTU exceptions, unit zero, transaction zero,
successful and failed writes, and 200 cycles of two TCP clients plus web reads.
They run with AddressSanitizer and UndefinedBehaviorSanitizer.

```sh
python -m pip install -r requirements.txt
VERSION=1.7.0-taxom.1 pio run -e esp12e
python test/host/run_tests.py
```

To reproduce the original use-after-free (expected sanitizer failure):

```sh
python test/host/run_tests.py --reproduce-upstream
```

On environments where LeakSanitizer cannot inspect `/proc` under ptrace,
set `ASAN_OPTIONS=detect_leaks=0`. Address/undefined-behavior checks remain on.

## First hardware test

This candidate has not yet been tested on the actual PSU/module.

1. Stop network clients. Use the dongle's Configure / Firmware Update page
   to upload `firmware_esp12e_1_7_0-taxom_1.bin` and reboot the dongle.
2. Check the version on Home. For the user's existing wiring keep UniSoft
   TTL+EN, UART 115200 baud on both ends, and Modbus address 1.
3. With the output off and no load, compare TestController and web readings
   with the PSU display for several minutes. Then check setpoint writes.
4. Add Home Assistant as another Modbus TCP client on port 502, unit ID 1.
   Start with a five-second polling interval and repeat the comparison.
5. Confirm local date/time after startup. Subsequent NTP updates should
   resynchronize it without a dongle restart; failed writes retry later.

For rollback, upload the original v1.7.0 ESP-12E binary through the same web
page. The ESP UART bootloader and the user's original full flash backup
remain the recovery route if web updating is unavailable.
