# Taxom: shared UART polling and clock synchronization

Based on upstream v1.7.0, commit `e9a060958c9dcf82f24353e36c2282b057690be4`.
Candidate firmware version: **1.7.0-taxom.3**.

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

Hardware tests of `taxom.2` produced 1,118 clean logged rows across two
TestControllers and the web page. The later diagnostic snapshot had 27,806
TCP requests, 210 recovered reads and no terminal gateway failures after
almost 30 minutes. Long logging gaps remained with little web interaction.
The precise UART loss mechanism and each logging gap remain unconfirmed.
TestController also adapts its logging interval when it cannot maintain it,
so the CSV interval is not a measurement of ESP CPU utilization.

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
- In `taxom.3`, observe the existing UART Stream passively to count CRC-bad
  frames and distinguish timeouts with no RX bytes from timeouts where bytes
  arrived. Record TCP and local attempts separately, observed RX latency,
  latency buckets, and the last eight UART timeout events.
- Measure elapsed time in TCP request handling, the TCP poll, the HTTP poll,
  web status and diagnostics handlers, and the main loop. Include current
  heap, largest free block, WiFi RSSI and CPU frequency. Elapsed time includes
  waiting/yielding; it does not measure CPU utilization.

`taxom.3` preserves the one-second RTU timeout, the retry policy and both
quiet guards from `taxom.2`. It adds no background UART polling and does
not emit debug text on the PSU serial port. CRC analysis happens after
ModbusRTU has consumed a frame; the adapter neither reads ahead nor changes
the parser's frame boundaries.

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
and web reads; the real TCP harness also checks reconnects. Additional
assertions check CRC/no-RX timeout classification, origins, timing buckets,
event ring ordering, and the 32-bit microsecond clock wrapping. A harness
compiles the actual diagnostics handler, parses its JSON, models a delayed
HTTP send, and verifies that diagnostics cause no UART requests. Tests run
with AddressSanitizer and UndefinedBehaviorSanitizer.

```sh
python -m pip install -r requirements.txt
VERSION=1.7.0-taxom.3 pio run -e esp12e
python test/host/run_tests.py
```

To reproduce the original use-after-free (expected sanitizer failure):

```sh
python test/host/run_tests.py --reproduce-upstream
```

On environments where LeakSanitizer cannot inspect `/proc` under ptrace,
set `ASAN_OPTIONS=detect_leaks=0`. Address/undefined-behavior checks remain on.

## First hardware test

`taxom.2` passed the user's mixed-client logging test. `taxom.3` still
requires testing on the actual PSU/module.

1. Stop network clients. Use the dongle's Configure / Firmware Update page
   to upload `firmware_esp12e_1_7_0-taxom_3.bin` and reboot the dongle.
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

## Timing/CRC test for taxom.3

Keep the existing UART baudrate, address and client configuration. Do not
change the logging setup while comparing the following phases:

1. After reboot, start the two TestControllers (or TC plus HA) and log for
   5 minutes with web pages closed. Save both CSV logs, stop logging, then
   save the full `/diagnostics/modbus` JSON once.
2. Start another 5-minute run with the same clients and the Control page
   polling. Refresh diagnostics a few times during this phase. Save logs
   and the full JSON again. Counters accumulate: compare their differences.

New fields:

| Field | Meaning |
| --- | --- |
| `uart.tcp_timeouts` / `local_timeouts` | UART timeouts for bridged TCP / local web, SCPI and startup operations |
| `uart.timeouts_no_rx` / `timeouts_with_rx` | No RX bytes observed / some bytes observed during the attempt |
| `uart.crc_errors` | CRC-bad chunks consumed by the existing RTU parser |
| `uart.first_byte` | Delay after TX flush until bytes are first observed available |
| `uart.crc_valid_frame_read` | Delay after TX flush until a CRC-valid frame is consumed, excluding the later TCP send callback |
| `uart.attempt_wait` | Full UART attempt wall time, including timeout or callback work |
| `uart.reply_buckets` | CRC-valid frame read delays <=5/10/20/50/100/500 ms, then >500 ms |
| `uart.recent_timeouts` | Last eight timeout events, oldest first; `argument` is quantity for reads or value for a single-register write |
| `timing.tcp_request` / `tcp_poll` | Time in a single TCP request / full library TCP poll |
| `timing.http_poll` / `http_status` / `http_diagnostics` | Whole HTTP poll / Control status handler / diagnostics handler |
| `timing.main_loop` | Main-loop wall time including waits and all services |

Timing objects contain count/min/average/max/last in microseconds. HTTP
and main-loop timing counters describe completed calls; the snapshot cannot
include the duration of its own still-running HTTP send. RX timing is from
software observations, not hardware UART interrupt timestamps. CRC counters
describe chunks parsed by the library, not independently reconstructed wire
frames. No-RX timeouts do not prove whether the PSU failed to transmit or
bytes were lost before the UART interface could observe them.

For rollback, upload the original v1.7.0 ESP-12E binary through the same web
page. The ESP UART bootloader and the user's original full flash backup
remain the recovery route if web updating is unavailable.
