# Taxom: shared UART polling and clock synchronization

Based on upstream v1.7.0, commit `e9a060958c9dcf82f24353e36c2282b057690be4`.
Candidate firmware version: **1.7.0-taxom.2**.

## Observed problem

RD6006P with UniSoft and the original ESP-12F module was stable with either
TestController Modbus TCP or the web Control page running alone. Running
both produced false peaks and unrelated register values in both clients.
For example, model ID 60065 appeared as a 60.065 V setpoint.

The user's hardware test of `taxom.1` showed improvement, but still had
isolated `-11` readings before scaling (`-0.011`, `-0.0011`, `-0.11`).
These are consistent with gateway exception 0x0B, not a measured zero or
an output shutdown. A second TestController instance worsened the issue.
The UART failure mechanism on the physical unit is not yet established;
`taxom.2` adds pacing, a bounded read retry, and diagnostics to investigate it.

## Changes

- Send TCP replies using a separate MBAP/PDU buffer. The previous use of
  `rawResponce()` could free the TCP receive buffer while the next request
  was still using it during a wait for the previous UART transaction.
- Finish the pending bridge request on UART timeout/failure and remove its
  raw interceptor. A later web response must not be forwarded as that reply.
- Validate response function, slave, register byte count, and write echo.
  Invalid replies produce a gateway exception instead of fabricated readings.
- Preserve the requesting TCP socket, transaction ID (including zero), and
  unit ID. Disable the TCP template's unique-IP eviction policy and route
  responses by its current client slot. Up to four simultaneous sockets
  are supported, including two TestControllers on one PC plus Home Assistant.
- Reject non-PSU unit IDs, including broadcast zero, without installing an
  interceptor that could steal a later local reply. Configure clients for ID 1.
- Report local Modbus exceptions and timeouts as failures. Wait until the
  library has released its callback and destination buffer before returning.
- Synchronize the PSU clock after each NTP update, with a five-second retry
  interval for failed clock writes. A new NTP event during a write stays pending.
- Pin the platform and library versions used to build this candidate.
- Wait at least 10 ms after UART completion before starting another local
  or TCP transaction. After failure, drain late input until 50 ms of quiet;
  abort preparation if input cannot settle within 250 ms.
- Retry TCP reads (functions 1--4) once after a transport failure. Complete
  both attempts within the same TCP callback. Never automatically repeat
  a TCP write, replay cached values, or turn a failed read into a numeric zero.
  Valid PSU exception replies are forwarded without retrying.
- Add read-only `/diagnostics/modbus` JSON counters. Reading this endpoint
  does not poll the PSU or change any setting.

The single UART continues to serve requests in order. Modbus TCP waits for
an existing transaction to finish; HTTP/SCPI/local operations do the same.
No extra polling thread or background writer has been added.

## Validation

The host regression tests compile the actual `RidenModbus`,
`RidenModbusBridge`, and modbus-esp8266 v4.1.0 RTU implementation. UART bytes
and time are simulated. One TCP ownership model reproduces the original
shared-buffer fault. A second harness compiles the actual TCP parser,
accept loop, client slots and cleanup with simulated WiFi sockets.

Tests cover overlapping TCP requests, interleaved local/web reads, timeout
recovery, malformed byte counts, RTU exceptions, unit zero, transaction zero,
successful and failed writes, a PSU requiring turnaround time, dropped/CRC-bad
reads, late replies, real zeros, and a write whose acknowledgement was lost.
Both harnesses run 200 cycles of two same-IP TestControllers, Home Assistant
and web reads; the real TCP harness also checks reconnects. They run with
AddressSanitizer and UndefinedBehaviorSanitizer.

```sh
python -m pip install -r requirements.txt
VERSION=1.7.0-taxom.2 pio run -e esp12e
python test/host/run_tests.py
```

To reproduce the original use-after-free (expected sanitizer failure):

```sh
python test/host/run_tests.py --reproduce-upstream
```

On environments where LeakSanitizer cannot inspect `/proc` under ptrace,
set `ASAN_OPTIONS=detect_leaks=0`. Address/undefined-behavior checks remain on.

## First hardware test

`taxom.2` has not yet been tested on the actual PSU/module.

1. Stop network clients. Use the dongle's Configure / Firmware Update page
   to upload `firmware_esp12e_1_7_0-taxom_2.bin` and reboot the dongle.
2. Check the version on Home. For the user's existing wiring keep UniSoft
   TTL+EN, UART 115200 baud on both ends, and Modbus address 1.
3. With the output off and no load, compare TestController and web readings
   with the PSU display for several minutes. Then check setpoint writes.
4. Add Home Assistant as another Modbus TCP client on port 502, unit ID 1.
   Start with a five-second polling interval and repeat the comparison.
5. Confirm local date/time after startup. Subsequent NTP updates should
   resynchronize it without a dongle restart; failed writes retry later.

Then keep two TestControllers and HA connected while the web Control page
polls. Capture `/diagnostics/modbus` after several minutes. Counters reset
when the dongle reboots:

| Field | Meaning |
| --- | --- |
| `read_retries` / `recovered_reads` | Retried TCP reads / successful read replies on retry |
| `uart_timeouts` | UART attempts that expired, including recovered attempts |
| `invalid_responses` | CRC-valid replies with wrong identity, length or write echo |
| `uart_exceptions` | Exception replies actually returned by the PSU |
| `gateway_failures` | Requests finalized with a gateway-generated exception |
| `last_failed_function` / `last_failed_register` | Most recent failed UART request (decimal) |
| `last_failure_code` | RTU library failure code; timeout 228, mismatch 226 |
| `recovery_waits` / `discarded_bytes` | Quiet recovery waits / discarded late or unsolicited bytes |
| `rx_overruns` / `rx_errors` | Hardware UART overrun/error flag observations (ESP-12E/F) |

A failed read can still take two one-second RTU timeouts plus quiet guards.
Clients must allow for that latency. Diagnostics distinguish a terminal
failure from a successful retry; they do not prove which physical UART
condition originally lost the reply. Disconnecting an IP in the web UI
disconnects all Modbus TCP sockets from that IP.

For rollback, upload the original v1.7.0 ESP-12E binary through the same web
page. The ESP UART bootloader and the user's original full flash backup
remain the recovery route if web updating is unavailable.
