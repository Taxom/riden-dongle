# Taxom: shared UART polling and clock synchronization

Based on upstream v1.7.0, commit `e9a060958c9dcf82f24353e36c2282b057690be4`.
Current firmware version: **1.7.0-taxom.9**.

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

The user's `taxom.3` no-web run produced another 2,162 clean rows. The
snapshot showed 407 recovered TCP reads, 409 total UART timeouts (408 with
no RX bytes observed), two CRC-bad chunks, and no terminal gateway failures.
CRC-valid frames were consumed in 6.93 ms on average, at most 16.95 ms.
The remaining first-attempt failures are not yet explained. The user asked
to defer automatic PSU clock writes until the output is confirmed OFF.

The user's `taxom.4` clock test confirmed that an NTP update remained pending
while output was ON, then performed one successful clock write after OFF.
A 10-minute concurrent control run (`20261109_093040_8696.zip`) completed all
60 transitions (30 Modbus, 30 web), with every transition observed by two
independent readers and web. All three TCP sockets stayed connected, all
11,215 TCP operations succeeded, and all 169 TCP read retries recovered.
Web telemetry returned 36 HTTP errors in 575 polls. One web write returned
"Failed to toggle output" despite applying the requested ON state: the UART
monitor recorded a CRC-bad 8-byte FC6 acknowledgement, and a fresh Modbus
read confirmed ON. The run added 68 CRC-bad parser chunks, 26 short chunks,
and 206 UART timeouts (159 with no observed RX bytes). No clock check/write
ran during this test. `taxom.5` adds local read recovery and reconciles this
ambiguous web-write outcome; it does not establish the physical UART cause.

The same 10-minute PC test on `taxom.5` (`20261109_100828_5392.zip`) passed
with 60 confirmed transitions, 11,390 successful TCP operations, 573
successful web status polls and no PC-visible errors. All 163 TCP retries
and 31 local read retries during the run recovered; all three sockets stayed
connected and every state was observed by both readers and web. The PSU
output returned to its initial OFF state. UART timeouts/CRC-bad chunks still
occurred internally. Separately, the user reported needing to power-cycle
the PSU after web firmware updates since installing `taxom.4`. The update
restart handler was unchanged between `.3` and `.5`; the precise on-device
failure stage (reset, boot, WiFi/mDNS, UART initialization or browser reload)
has not been established by the screenshot or current logs.
The binary crossed 512 KiB at `.4` (523,872 bytes for `.3`, 524,624 for `.4`,
525,712 for `.5`), while the first eight boot-header bytes remained identical
(`e902024080f41040`). This is an observation, not proof of an image-size cause;
the new flash/layout diagnostics can help assess that hypothesis.

The user then confirmed a successful automatic reboot installing `.6`, followed
by another successful `.6` to `.6` update without power-cycling the PSU. The
second update exercised the new update/restart path on the actual module.
This verifies that path in the reported test; it does not identify the exact
cause of the earlier failures. `taxom.7` adds only a navigation link to the
existing diagnostics endpoint and its version/build metadata.

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
- From `taxom.4`, automatic clock writes require a fresh successful read
  confirming Output OFF. ON or an unreadable output state leaves the update
  pending. While pending, check no more often than every five seconds.
  Capture current local time after the OFF check, so a long deferral does not
  write an old NTP timestamp. Each write retry performs another output check.
  NTP updates to the ESP's own clock continue normally.
- Pin the platform and library versions used to build this candidate.
- Wait at least 10 ms after UART completion before starting another local
  or TCP transaction. After failure, drain late input until 50 ms of quiet;
  abort preparation if input cannot settle within 250 ms.
- Retry TCP reads (functions 1--4) once after a transport failure. Complete
  both attempts within the same TCP callback. Never automatically repeat
  a TCP write, replay cached values, or turn a failed read into a numeric zero.
  Valid PSU exception replies are forwarded without retrying.
- From `taxom.5`, local FC3 reads also retry a transport timeout once, using
  the same recovery quiet guard. Only the failed register block is repeated.
  Valid PSU exceptions and rejected/malformed replies are not retried by
  this local policy. Writes remain single-attempt operations.
- A web toggle now fails without sending a write if the initial output
  state cannot be read. Previously it treated unknown as OFF and could
  enable the output unexpectedly. After a failed write acknowledgement,
  read the output and accept success only if it matches the intended state;
  never resend the write or perform a second toggle. If the outcome remains
  unknown, return an explicit error. Distinguish a telemetry read failure
  after an acknowledged/reconciled command from a failed command.
- Export `local_reads` and `web` recovery/failure counters plus `http_toggle`
  wall time in diagnostics. Existing TCP counters keep their previous meaning.
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
VERSION=1.7.0-taxom.9 pio run -e esp12e
python test/host/run_tests.py
```

To reproduce the original use-after-free (expected sanitizer failure):

```sh
python test/host/run_tests.py --reproduce-upstream
```

On environments where LeakSanitizer cannot inspect `/proc` under ptrace,
set `ASAN_OPTIONS=detect_leaks=0`. Address/undefined-behavior checks remain on.

## First hardware test

`taxom.5` passed the full concurrent control/web test, and the user confirmed
a successful `.6` to `.6` self-update/reboot on the physical module.
`taxom.7` adds the diagnostics navigation link to those changes. `taxom.8`
adds an optional, persistent detailed-diagnostics setting; hardware testing
of the switch and strict input validation was subsequently confirmed in the
`taxom.9` final acceptance run documented below.

1. Stop network clients. Use the dongle's Configure / Firmware Update page
   to upload `firmware_esp12e_1_7_0-taxom_9.bin` and reboot the dongle.
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
| `uart_timeouts` | Bridged TCP UART attempts that expired, including recovered attempts; local timeouts are in `uart.local_timeouts` |
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

## Output-gated clock synchronization in taxom.4

UART guards remain 10 ms normally and 50 ms during recovery. The output-state
check runs once per five seconds only while a clock update is pending.
From `taxom.5`, each check may use two UART read attempts if the first times
out; diagnostics itself still issues no UART request.

The `clock_sync` JSON object reports policy `output_off_only`, `pending`,
check/defer/read-failure counters, write/success/failure counters and the last
check/write uptime. `last_check_output_known` and `last_check_output_on`
describe the last synchronization eligibility check, not a live output poll.
The output is never toggled by synchronization. No network command handler
runs between the OFF check and clock write; a physical front-panel action
cannot be made atomic with two separate RTU transactions.

Host tests compile the actual clock-sync helper from `main.cpp` and cover
ON-to-OFF deferral, failed output reads, write retries that must recheck the
output, a new NTP event during a write, capturing current time after deferral,
and the five-second gate across the 32-bit millisecond clock wrapping.

For the hardware check, request NTP synchronization with the output ON and
confirm `pending=true`, increasing `deferred_output_on`, and no automatic
clock write. On the next confirmed OFF check, `writes` and `successes`
should increase and pending should clear, unless another NTP update arrived.

## Local web recovery diagnostics (taxom.5)

`local_reads.retries` counts local FC3 calls granted a second attempt after a
timeout, `recovered` counts those that succeeded, and `failures` counts calls
that ultimately returned failure, including valid PSU exceptions. These are
independent of the legacy TCP retry counters. `uart.local_timeouts` continues
to count every expired local attempt, including writes and recovered reads.

The `web` object counts failed status responses, toggle requests, failed
initial output-state reads, failed write acknowledgements, writes reconciled
by fresh readback, and writes whose outcome could not be confirmed. A
reconciled write sends exactly one output write on UART. `timing.http_toggle`
includes output reads, write waiting, optional reconciliation and the full
status response; it is elapsed time, not CPU utilization.

The sanitizer suite passes 39 checks. The new harness compiles the production
status/toggle handlers with the real RidenModbus/RTU implementation. It tests
single-block recovery, CRC corruption, two-attempt exhaustion, failure to
read the initial output in either ON/OFF state, normal ON/OFF, applied writes
with missing or CRC-bad acknowledgements, unapplied writes, unreadable
outcomes, and telemetry failure after a completed write. It also runs 100
cycles of real web toggles interleaved with two same-IP TC sockets and HA,
injecting read and acknowledgement failures. Clock gating remains covered
by the existing six clock-sync checks.

## HTTP firmware update and restart (taxom.6)

This is defensive hardening, not a confirmed diagnosis of the user's physical
restart problem. Track upload start, completion, byte counts, short writes,
aborts, and the boolean result of `Update.end(true)`. A POST without a completed
successful update returns HTTP 500 and cannot advertise success or schedule a
restart, even if the updater's numeric error remains zero.

A successful response has an explicit Content-Length and Connection: close.
The callback schedules a restart instead of resetting within the upload
handler. The main loop gives the response 500 ms, skips all UART polling,
clock writes and further command handling while restart is pending, then
stops the HTTP client with a 200 ms bound (aborting it if necessary), stops
UDP and calls the supported `ESP.restart()`. It does not force a raw/hard
reset or alter bootloader commands. Manual/config-portal restarts use the
same deferred path. UDP services are no longer stopped at upload start, so
a rejected or aborted upload does not gratuitously disable mDNS/NTP/ArduinoOTA.

Optional mDNS initialization no longer traps startup in an infinite loop:
HTTP and Modbus by IP can still start if service discovery fails. Both reboot
page countdowns stop at zero and initiate exactly one redirect, including
when navigation cannot complete. Diagnostics adds PSU connection state, reset
reason code, real/configured flash sizes, sketch size and free sketch space.

Host tests compile the actual upload/restart handlers and actual main loop.
They cover empty POSTs, begin/end failure with no numeric error, partial writes,
aborts, complete response length, deferred restart, suppression of UART/clock
work, an unacknowledged HTTP socket, manual/config-portal restart, repeated
requests, normal loop behavior and millis rollover. The two real page scripts
are also executed with stalled navigation and checked for a bounded countdown
and one redirect. Host mocks cannot establish that the physical SDK reset,
bootloader copy or network reconnection succeeds on this module.

The old running firmware handles the upload and restart when installing a new
image. Consequently, installing `.6` from `.5` still uses the old restart
handler. Test the new path on a subsequent upload (or by uploading `.6` again
after `.6` is running). Do not treat a repaired countdown alone as proof of
a reset: check version, uptime and reset reason from a fresh IP-based request.

## Diagnostics navigation (taxom.7)

The shared header includes a `Diagnostics` link to `/diagnostics/modbus`,
visible on Home, Control, Configure and the other pages using that header.
It opens the existing JSON snapshot in a new tab using `rel="noopener"`.
No automatic refresh is added; the endpoint still issues no UART requests.
Both the source mockup and its generated C header are updated together.

## Optional detailed diagnostics (taxom.8)

Configure has an **Enable detailed diagnostics** checkbox. It starts off for
new devices and upgrades from taxom.1-7. Save applies the setting immediately
and persists it across restarts. Timezone and UART baudrate remain in the
original V2 EEPROM format; the new setting uses a validated extension at
offset 128, so earlier firmware can still read and save the core settings.
A failed EEPROM commit reports an HTTP error and restores the active settings.

When off, the passive UART adapter forwards data without per-byte capture,
diagnostic CRC computation, latency histograms or timeout-event collection.
Scoped timing skips both clock reads and statistics updates. ModbusRTU still
validates the actual transport CRC, and all pacing, retries, response validation,
output control and OFF-only clock synchronization continue unchanged. Basic
request/error/recovery counters stay active; they are also used by the bridge
to distinguish a recovered read from a valid PSU exception.

The JSON endpoint remains available and includes `diagnostics_enabled` and
`detail_scope: "enabled_periods_since_boot"`. Detailed UART/timing fields freeze
when disabled and resume on enabling, accumulating only enabled periods until
the next reboot. Basic counters cover the entire uptime. Buffers remain
statically allocated; the switch reduces work, not the reserved RAM footprint.
No statistics are written to flash. Only saving configuration writes EEPROM.

The diagnostics tab has no automatic refresh: leaving it open creates no
periodic requests. Opening/refreshing it constructs and sends one JSON snapshot
and reads system status, without polling the PSU UART. Existing background
diagnostics in taxom.7 performed extra CPU work even with the tab closed;
its CPU percentage has not been measured, and elapsed HTTP/loop timing must
not be interpreted as CPU utilization.

For a detailed PC-test report, enable the checkbox before starting the script.
Normal operation can leave it off. Regression checks exercise output commands,
read/ACK recovery and multi-client routing with tracing off and on, as well as
EEPROM migration, checkbox rendering, persistence and save failure.

For rollback, upload the original v1.7.0 ESP-12E binary through the same web
page. The ESP UART bootloader and the user's original full flash backup
remain the recovery route if web updating is unavailable.

## Desktop test and original version history

The standalone Windows/Python multi-client test is in
[`tools/multi_client_test`](tools/multi_client_test/README_RU.txt), with its
12 offline emulator checks in the adjacent `tests` directory.

Versions 1 through 7 have separate `versions/1.7.0-taxom.N` branches.
[`history/README.md`](history/README.md) maps each source snapshot to its
published commit and its original local commit. The accompanying Git bundle
preserves the original objects, dates and version tags without squashing
any development stage.

## Strict setpoint validation and final acceptance (taxom.9)

The user requested incorrect/out-of-range commands in the final control test.
Previously, HTTP `strtod` ignored an invalid suffix and converted empty/text
input to zero; non-finite or excessive values could reach an unchecked integer
conversion. The new validation rejects these before sending a UART request.

- HTTP `/set_v` and `/set_i` accept one complete finite decimal number in
  the advertised PSU range. Empty/text input, units, decimal comma, embedded
  NUL, NaN/infinity, overflow, negative and excessive values return HTTP 400.
  Valid zero, bounds, decimal exponents and surrounding whitespace work.
- Invalid/unsupported `/set_i_range` input returns HTTP 400. Local numeric
  voltage/current helpers also reject non-finite, negative, excessive or
  unrepresentable values before register conversion. A range-aware current
  write fails if the required range read fails instead of using an unknown scale.
- The TCP bridge validates read sizes and FC6/FC16/FC23 framing before UART.
  Writes covering live registers 8/9/18 must respect V/I limits and boolean
  output values. A multi-register request is checked in full before forwarding.
  Masked writes to those live registers return exception 1 because no atomic
  read/validate/masked-write policy is implemented; invalid values return 3.
- Expected client rejections increment `rejected_requests`, separately from
  terminal gateway failures. JSON advertises `setpoint_validation: "strict_v1"`
  so the hardware test refuses older firmware before attempting negative writes.
- Host Arduino stubs include the actual `word(...)` macro, guarding against
  accidental helper-name macro substitution in the production parser.

The standalone final tester is in
[`tools/final_acceptance_test`](tools/final_acceptance_test/README_RU.txt).
It preserves the saved diagnostics checkbox, timezone, UART speed and original
V/I/output, then runs identical OFF/ON diagnostic phases with two persistent
same-IP Modbus readers, a separate controller and HTTP load. It exercises live
and OFF setpoint changes, FC6/FC16 and HTTP commands, per-client fresh readback,
unloaded CV voltage tracking, 29 HTTP plus 13 Modbus rejection cases per mode,
valid zero and protected-register invariants. Detailed metrics must freeze in
OFF and resume in ON; successfully recovered UART retries remain allowed.

Default profiles never exceed initial V/I and are additionally capped at
5 V / 0.1 A. Use no attached load and disable other writers. Every write uses
at most one command attempt; an ambiguous ACK is reconciled by reading and
stops the test. Cleanup confirms OFF before restoring V/I, and restores ON only
after the original setpoints and protected settings are verified. The initial
diagnostics checkbox is restored too. An interruption or failed restoration
cannot be a passing result. `eligible_for_merge` requires both complete phases,
all expected rejections, no client errors and confirmed cleanup; the script
does not access GitHub. The user's hardware run and independent log review
passed as documented below.

## Final hardware acceptance (2026-11-09)

The user ran `RD6006P_final_diagnostics_v1` on the RD6006P/UniSoft original
ESP-12F with firmware `1.7.0-taxom.9`, UART 115200 and Modbus unit 1. The
submitted `20261109_122340_14580.zip` reports PASS after 674.078 seconds.
Independent inspection of `operations.csv`, `events.jsonl`, recovery data and
raw diagnostic snapshots confirmed:

- Both OFF/ON diagnostic phases completed all three 10-step load cycles.
  All 66 confirmed commands, including closing OFF and valid-zero checks,
  had fresh V/I/output observations from both TCP readers and the web client.
  The three observers each recorded 237 field confirmations; none were missing.
- All 13,785 CSV rows succeeded. There were 12,882 TCP read operations and
  629 web status polls, with one connection per TCP client and no reconnects.
- All 84 negative cases passed: 29 HTTP plus 13 Modbus rejections in each
  mode, with unchanged live setpoints/output and protected settings.
- The readers observed only expected voltage setpoints 0/2080/2340/2600,
  current setpoints 0/250/375/500, and output values 0/1. ON voltage tracked
  the requested profile in unloaded CV operation.
- Fresh cleanup reads confirmed restoration to 2.600 V / 0.0500 A, output
  OFF and diagnostics OFF, plus unchanged timezone/UART and protected settings.
- Detailed UART and timing counters froze while OFF and advanced while ON.
  No clock checks or clock writes ran during either measured phase.

| Metric | Diagnostics OFF | Diagnostics ON |
| --- | ---: | ---: |
| Measured phase duration | 329.141 s | 335.359 s |
| TCP-reader median request time, both readers | 16 ms | 16 ms |
| TCP-reader 95th percentile, both readers | 94 ms | 94 ms |
| Web status median | 157 ms | 156 ms |
| Web status 95th percentile | 1187.3 ms | 1188 ms |
| Maximum all-client confirmation time | 4.484 s | 5.875 s |
| TCP read retries / recovered | 106 / 106 | 91 / 91 |
| Local read retries / recovered | 18 / 18 | 23 / 23 |
| Terminal gateway/local/web failures | 0 | 0 |

The all-client time includes the deliberate one-second settling allowance and
reader/web polling schedules. This single run shows no change in typical TCP
or web status latency attributable to enabled tracing; it does not measure
ESP CPU utilization or establish a general performance bound. First-attempt
UART failures remain: all 197 TCP and 41 local retries within the phases
recovered. The ON phase recorded 30 CRC-bad parser chunks and two short chunks,
not proof of 30 distinct physical-line errors. No reset, capture overflow,
terminal failure or unexpected state was observed. An external voltage meter,
constant-current load test and long-duration endurance run were outside scope.

The runtime validation passed on hardware, and the source checks include
82 ASan/UBSan firmware checks, 20 final-tester fault-injection checks and 12
legacy desktop-tool checks. This fulfills the agreed final acceptance before
integrating the development branch into main while preserving its commits.
