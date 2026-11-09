#!/usr/bin/env python3
"""RD6006P concurrent Modbus TCP + HTTP control test; Python 3.9+, stdlib only.

Writes ONLY register 18 (output ON/OFF), or GET /toggle_out. Voltage,
current, protection, presets and calibration are never written. The finally
block tries to restore the initial output state, including after Ctrl+C.
"""

import argparse
import collections
import csv
import datetime as dt
import gzip
import http.client
import json
import math
import os
from pathlib import Path
import signal
import socket
import struct
import sys
import threading
import time
import zipfile

VERSION = "1.0"
READS = ((8, 1), (9, 1), (10, 1), (11, 1), (12, 2), (14, 1),
         (4, 2), (33, 1), (34, 2), (38, 2), (40, 2), (18, 1))


class TestError(Exception):
    pass


class ProtocolError(TestError):
    pass


class DeviceException(TestError):
    def __init__(self, code):
        self.code = code
        super().__init__("Modbus exception 0x%02X" % code)


class ModbusTCP:
    """One socket, one owning thread, one outstanding request; no write retries."""

    def __init__(self, host, port, unit, timeout, seed):
        self.host, self.port, self.unit = host, port, unit
        self.timeout, self.tid = timeout, seed
        self.sock = None
        self.last_tid = None
        self.connections = 0

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def exchange(self, pdu):
        self.tid = (self.tid + 1) & 0xFFFF
        self.last_tid = self.tid
        deadline = time.monotonic() + self.timeout
        try:
            if self.sock is None:
                self.sock = socket.create_connection((self.host, self.port), self.timeout)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.connections += 1
            self.sock.settimeout(max(0.001, deadline - time.monotonic()))
            self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, self.unit) + pdu)

            def receive(n):
                data = bytearray()
                while len(data) < n:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("TCP response deadline exceeded")
                    self.sock.settimeout(remaining)
                    part = self.sock.recv(n - len(data))
                    if not part:
                        raise ConnectionError("Peer closed Modbus connection")
                    data.extend(part)
                return bytes(data)

            tid, protocol, length, unit = struct.unpack(">HHHB", receive(7))
            if (tid, protocol, unit) != (self.tid, 0, self.unit):
                raise ProtocolError("Wrong MBAP: tid=%d/%d protocol=%d unit=%d/%d" %
                                    (tid, self.tid, protocol, unit, self.unit))
            if not 2 <= length <= 254:
                raise ProtocolError("Invalid MBAP length %d" % length)
            result = receive(length - 1)
            if result[0] == (pdu[0] | 0x80):
                if len(result) != 2:
                    raise ProtocolError("Malformed Modbus exception")
                raise DeviceException(result[1])
            if result[0] != pdu[0]:
                raise ProtocolError("Response function differs from request")
            return result
        except Exception:
            # Never leave a partial/late response on a socket for the next call.
            self.close()
            raise

    def read(self, address, count):
        result = self.exchange(struct.pack(">BHH", 3, address, count))
        if len(result) != 2 + 2 * count or result[1] != 2 * count:
            self.close()
            raise ProtocolError("Wrong FC3 byte count or PDU length")
        return list(struct.unpack(">" + "H" * count, result[2:]))

    def write_output(self, value):
        if value not in (0, 1):
            raise ValueError("Output must be 0 or 1")
        request = struct.pack(">BHH", 6, 18, value)
        if self.exchange(request) != request:
            self.close()
            raise ProtocolError("Wrong FC6 write echo")


def http_get(args, path):
    # Direct connection: no environment proxy, external redirects or retries.
    conn = http.client.HTTPConnection(args.host, args.http_port, timeout=args.timeout)
    try:
        conn.request("GET", path, headers={"Connection": "close",
                     "Cache-Control": "no-cache", "User-Agent": "RD6006-test/" + VERSION})
        response = conn.getresponse()
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ProtocolError("HTTP response exceeds 2 MB")
        if response.status != 200:
            raise TestError("HTTP %d for %s: %s" %
                            (response.status, path, body[:160].decode("utf-8", "replace")))
        if response.getheader("Content-Encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        return body
    finally:
        conn.close()


def percentile(values, q):
    if not values:
        return None
    a = sorted(values)
    p = (len(a) - 1) * q
    lo, hi = int(p), math.ceil(p)
    return round(a[lo] + (a[hi] - a[lo]) * (p - lo), 3)


class Report:
    def __init__(self, folder):
        self.folder = folder
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.ops = collections.defaultdict(lambda: {"ok": 0, "errors": 0, "ms": []})
        self.errors = collections.Counter()
        self.error_count = 0
        self.csv_file = (folder / "operations.csv").open("w", newline="", encoding="utf-8-sig")
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(["timestamp", "elapsed_s", "client", "operation", "ok",
                              "duration_ms", "transaction_id", "register", "count", "details"])
        self.events_file = (folder / "events.jsonl").open("w", encoding="utf-8")

    def op(self, client, operation, start, ok, details, tid=None, reg=None, count=None):
        ms = (time.monotonic() - start) * 1000
        with self.lock:
            row = self.ops[client + "/" + operation]
            row["ok" if ok else "errors"] += 1
            row["ms"].append(ms)
            if not ok:
                self.error_count += 1
                self.errors[str(details.get("error_type", "validation"))] += 1
                if self.error_count <= 15:
                    print("ERROR %s/%s: %s" % (client, operation, details), flush=True)
            self.writer.writerow([dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
                                   round(time.monotonic() - self.started, 6), client, operation,
                                   int(ok), round(ms, 3), tid, reg, count,
                                   json.dumps(details, ensure_ascii=True, separators=(",", ":"))])
            self.csv_file.flush()
        return ms

    def event(self, name, **data):
        with self.lock:
            record = {"timestamp": dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
                      "elapsed_s": round(time.monotonic() - self.started, 6), "event": name, **data}
            self.events_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.events_file.flush()

    def snapshot(self):
        with self.lock:
            return {key: {"ok": v["ok"], "errors": v["errors"],
                          "latency_ms": {"p50": percentile(v["ms"], .5),
                                         "p95": percentile(v["ms"], .95),
                                         "max": round(max(v["ms"]), 3)}}
                    for key, v in self.ops.items()}

    def close(self):
        self.csv_file.close()
        self.events_file.close()


class Shared:
    def __init__(self, initial, report, observers, settle):
        self.lock = threading.Lock()
        self.initial, self.report, self.observers = initial, report, observers
        self.settle = settle
        self.epoch, self.expected = 0, initial[18]
        self.phase, self.stable_after = "stable", time.monotonic()
        self.command_started = self.stable_after
        self.seen = {}
        self.fatal = threading.Event()

    def token(self):
        with self.lock:
            return (self.epoch, self.phase, time.monotonic())

    def transition(self, target):
        with self.lock:
            self.epoch += 1
            self.expected, self.phase = target, "transition"
            self.command_started = time.monotonic()
            return self.epoch

    def stable(self):
        with self.lock:
            self.phase = "stable"
            self.stable_after = time.monotonic() + self.settle

    def suspend(self):
        with self.lock:
            self.phase = "cleanup"

    def invariant(self, source, message, **data):
        self.report.op(source, "invariant", time.monotonic(), False,
                       {"error_type": "invariant", "error": message, **data})
        self.fatal.set()

    def observe(self, source, token, output):
        if output not in (0, 1):
            self.invariant(source, "Output register is not 0/1", value=output)
            return
        with self.lock:
            # A response to a read begun before a command is not evidence of
            # stale data. Require a fresh request within the same stable epoch.
            valid = (self.phase == "stable" and token[0] == self.epoch and
                     token[1] == "stable" and token[2] >= self.stable_after)
            expected, epoch, sent = self.expected, self.epoch, self.command_started
            first = valid and self.seen.get(source) != epoch and output == expected
            if first:
                self.seen[source] = epoch
        if valid and output != expected:
            self.invariant(source, "Unexpected output during a stable state",
                           expected=expected, observed=output, epoch=epoch)
        elif first:
            self.report.event("state_observed", client=source, epoch=epoch, output=output,
                              command_to_observation_ms=round((time.monotonic() - sent) * 1000, 3))

    def missing(self):
        with self.lock:
            return sorted(x for x in self.observers if self.seen.get(x) != self.epoch)


def read_logged(client, name, report, address, count):
    start = time.monotonic()
    try:
        values = client.read(address, count)
    except Exception as exc:
        report.op(name, "read", start, False,
                  {"error_type": type(exc).__name__, "error": str(exc)},
                  client.last_tid, address, count)
        raise
    report.op(name, "read", start, True, {"values": values}, client.last_tid, address, count)
    return values


def reader_worker(args, number, stop, shared, report):
    name = "reader-%d" % number
    client = ModbusTCP(args.host, args.port, args.unit, args.timeout, number * 12000)
    stop.wait((number - 1) * 0.17)
    try:
        while not stop.is_set():
            cycle = time.monotonic()
            for address, count in READS:
                if stop.is_set():
                    break
                token = shared.token()
                try:
                    values = read_logged(client, name, report, address, count)
                    if address in (8, 9) and values[0] != shared.initial[address]:
                        shared.invariant(name, "Voltage/current setpoint changed",
                                         register=address, expected=shared.initial[address], observed=values[0])
                    if address == 18:
                        shared.observe(name, token, values[0])
                except Exception:
                    stop.wait(0.25)
                    break  # Next cycle reconnects. The failed request is never hidden by a retry.
                stop.wait(args.request_gap)
            report.event("reader_cycle", client=name, duration_ms=round((time.monotonic() - cycle) * 1000, 3))
            stop.wait(max(0, args.read_interval - (time.monotonic() - cycle)))
    except Exception as exc:
        shared.invariant(name, "Reader worker stopped unexpectedly", error=str(exc))
    finally:
        report.event("connections", client=name, connections=client.connections)
        client.close()


def capture_diagnostics(args, report, label):
    start = time.monotonic()
    try:
        raw = http_get(args, "/diagnostics/modbus")
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("uptime_ms"), int):
            raise ProtocolError("Invalid diagnostics JSON")
        if label in ("before", "after"):
            (report.folder / ("diagnostics_" + label + ".json")).write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        report.event("diagnostics", label=label, data=data)
        report.op("web", "diagnostics", start, True, {"uptime_ms": data["uptime_ms"]})
        return data
    except Exception as exc:
        report.op("web", "diagnostics", start, False,
                  {"error_type": type(exc).__name__, "error": str(exc)})
        return None


def web_worker(args, stop, shared, report):
    next_status = next_page = time.monotonic()
    next_diag = time.monotonic() + args.diagnostics_interval
    try:
        while not stop.is_set():
            now = time.monotonic()
            if now >= next_status:
                token, start = shared.token(), time.monotonic()
                try:
                    data = json.loads(http_get(args, "/status"))
                    if (not isinstance(data, dict) or type(data.get("out_on")) is not bool or
                            any(type(data.get(k)) not in (int, float) or not math.isfinite(data[k])
                                for k in ("set_v", "set_c", "out_v", "out_c"))):
                        raise ProtocolError("Missing/invalid /status fields")
                    report.op("web", "status", start, True, data)
                    # /status rounds current to 3 decimals; compare within that resolution.
                    if (abs(data["set_v"] - shared.initial[8] / 1000) > 0.0006 or
                            abs(data["set_c"] - shared.initial[9] / 10000) > 0.0006):
                        shared.invariant("web", "Web setpoint differs from initial Modbus setpoint", status=data)
                    shared.observe("web", token, int(data["out_on"]))
                except Exception as exc:
                    report.op("web", "status", start, False,
                              {"error_type": type(exc).__name__, "error": str(exc)})
                next_status = start + args.web_interval
            if not stop.is_set() and time.monotonic() >= next_page:
                start = time.monotonic()
                try:
                    body = http_get(args, "/control/")
                    if b"<html" not in body.lower() and b"<body" not in body.lower():
                        raise ProtocolError("Control page is not HTML")
                    report.op("web", "page", start, True, {"bytes": len(body)})
                except Exception as exc:
                    report.op("web", "page", start, False,
                              {"error_type": type(exc).__name__, "error": str(exc)})
                next_page = start + args.page_interval
            if not stop.is_set() and time.monotonic() >= next_diag:
                capture_diagnostics(args, report, "periodic")
                next_diag = time.monotonic() + args.diagnostics_interval
            stop.wait(0.02)
    except Exception as exc:
        shared.invariant("web", "Web worker stopped unexpectedly", error=str(exc))


def wait_output(args, client, report, target):
    deadline = time.monotonic() + args.verify_timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = read_logged(client, "controller", report, 18, 1)[0]
            if last == target:
                return
        except Exception:
            pass
        time.sleep(0.15)
    raise TestError("Output did not confirm %d within %.1f s (last=%r)" %
                    (target, args.verify_timeout, last))


def change_output(args, client, shared, report, target, method):
    # Read before toggling a non-idempotent web endpoint. Never retry /toggle_out.
    current = read_logged(client, "controller", report, 18, 1)[0]
    if current != shared.expected:
        raise TestError("Output changed outside this test: expected %d, read %d" %
                        (shared.expected, current))
    epoch = shared.transition(target)
    start = time.monotonic()
    ack_ok = True
    try:
        if method == "web":
            data = json.loads(http_get(args, "/toggle_out"))
            if not isinstance(data, dict) or type(data.get("out_on")) is not bool:
                raise ProtocolError("Invalid /toggle_out status JSON")
            if int(data["out_on"]) != target:
                raise ProtocolError("Web toggle returned an unexpected output state")
        else:
            client.write_output(target)
        report.op("controller", method + "_command", start, True,
                  {"epoch": epoch, "target": target}, client.last_tid if method == "modbus" else None, 18, 1)
    except Exception as exc:
        ack_ok = False
        report.op("controller", method + "_command", start, False,
                  {"epoch": epoch, "target": target, "error_type": type(exc).__name__, "error": str(exc)},
                  client.last_tid if method == "modbus" else None, 18, 1)
        # The command might already have executed. Reconcile by reading, never
        # by sending the command again. Its acknowledgement error remains logged.
    wait_output(args, client, report, target)
    verify_ms = (time.monotonic() - start) * 1000
    shared.stable()
    report.event("output_verified", epoch=epoch, target=target, method=method,
                 acknowledgement_ok=ack_ok, command_to_confirmation_ms=round(verify_ms, 3))
    print("OUTPUT %s via %-6s  confirmed in %.0f ms%s" %
          ("ON " if target else "OFF", method, verify_ms, "" if ack_ok else " (ACK ERROR)"), flush=True)
    deadline = time.monotonic() + args.observe_timeout
    while time.monotonic() < deadline and shared.missing() and not shared.fatal.is_set():
        time.sleep(0.05)
    missing = shared.missing()
    report.op("controller", "all_clients_observed", start, not missing,
              {"epoch": epoch, "target": target, "missing": missing,
               **({"error_type": "observation_timeout"} if missing else {})})


def restore_output(args, client, initial, report):
    start = time.monotonic()
    try:
        try:
            current = read_logged(client, "restore", report, 18, 1)[0]
        except Exception:
            current = None
        if current != initial:
            # Exactly one explicit restore write; a lost ACK is reconciled below.
            try:
                client.write_output(initial)
            except Exception as exc:
                report.op("restore", "write_ack", start, False,
                          {"error_type": type(exc).__name__, "error": str(exc)})
        wait_output(args, client, report, initial)
        report.op("restore", "confirmed", start, True, {"output": initial})
        print("Initial output restored and confirmed: %s" % ("ON" if initial else "OFF"), flush=True)
        return True
    except Exception as exc:
        report.op("restore", "confirmed", start, False,
                  {"error_type": type(exc).__name__, "error": str(exc), "desired_output": initial})
        print("RESTORE FAILED: check the physical PSU output. Desired: %s" %
              ("ON" if initial else "OFF"), flush=True)
        return False


def diagnostics_delta(before, after):
    if before is None or after is None:
        return None
    if after["uptime_ms"] < before["uptime_ms"]:
        return {"counter_reset_or_uptime_wrap": True, "delta_unavailable": True}
    keys = ("requests", "read_retries", "recovered_reads", "uart_timeouts", "invalid_responses",
            "uart_exceptions", "gateway_failures", "recovery_waits", "discarded_bytes", "rx_overruns", "rx_errors")
    result = {k: after[k] - before[k] for k in keys if k in before and k in after}
    result["elapsed_ms"] = after["uptime_ms"] - before["uptime_ms"]
    for section, counters in {
        "uart": ("attempts", "tcp_attempts", "local_attempts", "tcp_timeouts", "local_timeouts",
                 "timeouts_no_rx", "timeouts_with_rx", "crc_errors"),
        "clock_sync": ("checks", "deferred_output_on", "output_read_failures", "writes", "successes", "write_failures")
    }.items():
        result[section] = {k: after[section][k] - before[section][k] for k in counters
                           if k in before.get(section, {}) and k in after.get(section, {})}
    return result


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="192.168.1.176")
    p.add_argument("--port", type=int, default=502)
    p.add_argument("--http-port", type=int, default=80)
    p.add_argument("--unit", type=int, default=1)
    p.add_argument("--duration", type=float, default=600, help="test duration in seconds")
    p.add_argument("--readers", type=int, choices=(1, 2, 3), default=2, help="separate TCP readers; plus one control socket")
    p.add_argument("--read-interval", type=float, default=1.0, help="minimum start-to-start reader cycle interval")
    p.add_argument("--request-gap", type=float, default=0.005, help="gap after each read within a cycle")
    p.add_argument("--toggle-interval", type=float, default=10.0, help="minimum start-to-start command interval")
    p.add_argument("--control-mode", choices=("mixed", "modbus", "web"), default="mixed")
    p.add_argument("--no-web", action="store_true", help="disable web polling; default mixed control becomes Modbus")
    p.add_argument("--web-interval", type=float, default=1.0)
    p.add_argument("--page-interval", type=float, default=30.0)
    p.add_argument("--diagnostics-interval", type=float, default=30.0)
    p.add_argument("--timeout", type=float, default=5.0, help="network request timeout")
    p.add_argument("--verify-timeout", type=float, default=8.0)
    p.add_argument("--observe-timeout", type=float, default=6.0)
    p.add_argument("--settle", type=float, default=0.5, help="ignore in-flight observations during output transition")
    p.add_argument("--output-dir", type=Path, help="new result directory (must not already exist)")
    args = p.parse_args(argv)
    for name in ("duration", "read_interval", "toggle_interval", "web_interval", "page_interval",
                 "diagnostics_interval", "timeout", "verify_timeout", "observe_timeout"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            p.error("--" + name.replace("_", "-") + " must be finite and positive")
    if any(not math.isfinite(x) or x < 0 for x in (args.request_gap, args.settle)):
        p.error("--request-gap and --settle must be finite and nonnegative")
    if not (1 <= args.port <= 65535 and 1 <= args.http_port <= 65535 and 1 <= args.unit <= 247):
        p.error("Invalid port or Modbus unit")
    if args.no_web:
        if args.control_mode == "web":
            p.error("--no-web cannot be combined with --control-mode web")
        args.control_mode = "modbus"
    return args


def run(args):
    folder = args.output_dir or Path(__file__).resolve().parent / "results" / (
        dt.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(os.getpid()))
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    report = Report(folder)
    stop = threading.Event()
    threads, initial, shared = [], None, None
    client = ModbusTCP(args.host, args.port, args.unit, args.timeout, 50000)
    before = after = None
    restored, interrupted, fatal, commands = False, False, None, 0
    started = time.monotonic()
    metadata = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    (folder / "run_config.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    try:
        print("RD6006P multi-client test %s\nTarget %s:%d / unit %d" %
              (VERSION, args.host, args.port, args.unit), flush=True)
        print("%d readers + 1 controller; web=%s; control=%s; %.0f seconds" %
              (args.readers, not args.no_web, args.control_mode, args.duration), flush=True)
        if not args.no_web:
            before = capture_diagnostics(args, report, "before")
        raw = read_logged(client, "preflight", report, 0, 20)
        if raw[0] != 60065 or raw[18] not in (0, 1):
            raise TestError("Expected RD6006P id 60065 and valid output; read id=%d, output=%d" %
                            (raw[0], raw[18]))
        initial = {8: raw[8], 9: raw[9], 18: raw[18]}
        report.event("initial_state", model_id=raw[0], registers=initial)
        print("Using existing setpoints: %.3f V / %.4f A; initial output %s" %
              (raw[8] / 1000, raw[9] / 10000, "ON" if raw[18] else "OFF"), flush=True)
        print("The physical output will alternate ON/OFF. Ctrl+C stops and restores it.", flush=True)
        observers = {"reader-%d" % i for i in range(1, args.readers + 1)}
        if not args.no_web:
            observers.add("web")
        shared = Shared(initial, report, observers, args.settle)
        for i in range(1, args.readers + 1):
            threads.append(threading.Thread(target=reader_worker, args=(args, i, stop, shared, report),
                                            name="reader-%d" % i, daemon=True))
        if not args.no_web:
            threads.append(threading.Thread(target=web_worker, args=(args, stop, shared, report), name="web", daemon=True))
        for thread in threads:
            thread.start()
        load_start = time.monotonic()
        warmup_end = load_start + max(10, args.observe_timeout)
        while shared.missing() and time.monotonic() < warmup_end and not shared.fatal.is_set():
            time.sleep(0.05)
        if shared.missing() or shared.fatal.is_set():
            raise TestError("Initial observations failed: " + repr(shared.missing()))
        print("All readers and web are receiving data. Starting output commands.", flush=True)
        next_toggle, next_progress = time.monotonic(), time.monotonic()
        methods = ("modbus", "web", "web", "modbus") if args.control_mode == "mixed" else (args.control_mode,)
        end = load_start + args.duration
        while time.monotonic() < end:
            if shared.fatal.is_set():
                raise TestError("A state/setpoint invariant failed; see operations.csv")
            if time.monotonic() >= next_toggle:
                command_start = time.monotonic()
                change_output(args, client, shared, report, 1 - shared.expected, methods[commands % len(methods)])
                commands += 1
                next_toggle = command_start + args.toggle_interval
            if time.monotonic() >= next_progress:
                elapsed = time.monotonic() - load_start
                print("PROGRESS %5.0f / %.0f s   commands=%d   logged_errors=%d" %
                      (elapsed, args.duration, commands, report.error_count), flush=True)
                next_progress = time.monotonic() + 5
            time.sleep(0.05)
        if commands == 0:
            raise TestError("Duration ended before any control command was tested")
    except KeyboardInterrupt:
        interrupted = True
        print("\nStopping; waiting for in-flight reads and restoring output...", flush=True)
    except Exception as exc:
        fatal = str(exc)
        report.op("main", "fatal", time.monotonic(), False,
                  {"error_type": type(exc).__name__, "error": str(exc)})
    finally:
        # Readers and HTTP poller never write; only this thread owns output control.
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        if shared:
            shared.suspend()
        stop.set()
        for thread in threads:
            thread.join(args.timeout + 2)
        alive = [t.name for t in threads if t.is_alive()]
        if alive:
            fatal = "Worker threads failed to stop: " + repr(alive)
            report.op("main", "shutdown", time.monotonic(), False,
                      {"error_type": "worker_shutdown", "error": fatal})
        if initial is not None:
            restored = restore_output(args, client, initial[18], report)
        report.event("connections", client="controller", connections=client.connections)
        client.close()
        if not args.no_web:
            after = capture_diagnostics(args, report, "after")
        status = "INTERRUPTED" if interrupted else "PASS"
        if fatal or report.error_count or not restored:
            status = "FAIL"
        summary = {"script_version": VERSION, "result": status, "interrupted": interrupted,
                   "fatal_error": fatal, "initial_registers": initial, "output_restored": restored,
                   "duration_s": round(time.monotonic() - started, 3), "commands_completed": commands,
                   "error_count": report.error_count, "error_types": dict(report.errors),
                   "operations": report.snapshot(), "diagnostics_delta": diagnostics_delta(before, after),
                   "notes": ["Latencies are PC wall time, not ESP CPU utilization.",
                             "Output is observed via register 18 and web out_on; no external voltage measurement.",
                             "Successful replies do not reveal UART retries; see diagnostics_delta.",
                             "No failed command or web toggle is automatically resent."]}
        (folder / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        report.close()
        archive = folder.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(folder.iterdir()):
                if path.is_file():
                    z.write(path, folder.name + "/" + path.name)
        print("\nRESULT: %s\nErrors: %d\nSend this ZIP: %s" % (status, report.error_count, archive), flush=True)
    return 130 if interrupted and status != "FAIL" else (0 if status == "PASS" else 1)


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        raise SystemExit("Python 3.9 or newer is required; no pip packages are needed.")
    raise SystemExit(run(parse_args()))
