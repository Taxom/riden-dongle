#!/usr/bin/env python3
"""RD6006P final acceptance: two TCP readers, HTTP load, live V/I/output control.

Python 3.9+, standard library only. Requires taxom.8+ diagnostics switch.
Runs OFF/ON diagnostics phases. Writes only PSU registers 8, 9 and 18 plus
its own diagnostics checkbox; saves and restores initial settings/state.
Run without an attached load. It never contacts GitHub or merges code.
"""
import argparse
import collections
import csv
import datetime as dt
import gzip
import html.parser
import http.client
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import struct
import sys
import threading
import time
import urllib.parse
import zipfile

VERSION = "1.0"
READS = ((8, 1), (9, 1), (10, 1), (11, 1), (12, 2), (14, 1),
         (4, 2), (33, 1), (34, 2), (38, 2), (40, 2), (18, 1), (8, 12))
FIELDS = (8, 9, 18)
OBSERVERS = {"reader-1", "reader-2", "web"}

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

    def exchange(self, pdu, allow_exception=False):
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
                if allow_exception:
                    return result
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

    def write_one(self, address, value):
        if address not in (8, 9, 18) or type(value) is not int or not 0 <= value <= 65535:
            raise ValueError("Only V/I/output writes are allowed")
        if address == 18 and value not in (0, 1):
            raise ValueError("Output must be 0 or 1")
        request = struct.pack(">BHH", 6, address, value)
        if self.exchange(request) != request:
            self.close()
            raise ProtocolError("Wrong FC6 write echo")

    def write_pair(self, voltage_raw, current_raw):
        if any(type(v) is not int or not 0 <= v <= 65535 for v in (voltage_raw, current_raw)):
            raise ValueError("Invalid V/I values")
        request = struct.pack(">BHHBHH", 16, 8, 2, 4, voltage_raw, current_raw)
        if self.exchange(request) != struct.pack(">BHH", 16, 8, 2):
            self.close()
            raise ProtocolError("Wrong FC16 write echo")


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    p = (len(ordered) - 1) * quantile
    low, high = int(p), math.ceil(p)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (p - low), 3)


class Report:
    def __init__(self, folder):
        self.folder, self.started = folder, time.monotonic()
        self.lock = threading.RLock()
        self.phase = "preflight"
        self.error_count = 0
        self.errors = collections.Counter()
        self.ops = collections.defaultdict(lambda: {"ok": 0, "errors": 0, "ms": []})
        self.csv_file = (folder / "operations.csv").open("w", newline="", encoding="utf-8-sig")
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(["timestamp", "elapsed_s", "phase", "client", "operation", "ok",
                              "duration_ms", "transaction_id", "register", "count", "details"])
        self.events_file = (folder / "events.jsonl").open("w", encoding="utf-8")

    def set_phase(self, phase):
        with self.lock:
            self.phase = phase

    def op(self, client, operation, start, ok, details, tid=None, reg=None, count=None, phase=None):
        ms = (time.monotonic() - start) * 1000
        with self.lock:
            phase = self.phase if phase is None else phase
            row = self.ops[(phase, client + "/" + operation)]
            row["ok" if ok else "errors"] += 1
            row["ms"].append(ms)
            if not ok:
                self.error_count += 1
                self.errors[str(details.get("error_type", "validation"))] += 1
                if self.error_count <= 20:
                    print("ERROR %s %s/%s: %s" % (phase, client, operation, details), flush=True)
            self.writer.writerow([dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
                                   round(time.monotonic() - self.started, 6), phase, client, operation,
                                   int(ok), round(ms, 3), tid, reg, count,
                                   json.dumps(details, ensure_ascii=True, separators=(",", ":"))])
            self.csv_file.flush()
        return ms

    def event(self, name, **data):
        with self.lock:
            self.events_file.write(json.dumps({
                "timestamp": dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "elapsed_s": round(time.monotonic() - self.started, 6), "phase": self.phase,
                "event": name, **data}, ensure_ascii=False) + "\n")
            self.events_file.flush()

    def snapshot(self, phase=None):
        with self.lock:
            result = {}
            for (label, key), values in self.ops.items():
                if phase is not None and label != phase:
                    continue
                result[label + "/" + key] = {
                    "ok": values["ok"], "errors": values["errors"],
                    "latency_ms": {"p50": percentile(values["ms"], .5),
                                   "p95": percentile(values["ms"], .95),
                                   "max": round(max(values["ms"]), 3)}}
            return result

    def close(self):
        self.csv_file.close()
        self.events_file.close()


def fail_details(exc):
    return {"error_type": type(exc).__name__, "error": str(exc)}


def http_request(args, path, method="GET", body=None, form=False, expected_status=200):
    conn = http.client.HTTPConnection(args.host, args.http_port, timeout=args.timeout)
    headers = {"Connection": "close", "Cache-Control": "no-cache",
               "User-Agent": "RD6006-final-test/" + VERSION}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "text/plain"
        body = body.encode("ascii")
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ProtocolError("HTTP response exceeds 2 MB")
        if response.status != expected_status:
            raise TestError("HTTP %d for %s: %s" %
                            (response.status, path, data[:160].decode("utf-8", "replace")))
        if response.getheader("Content-Encoding", "").lower() == "gzip":
            data = gzip.decompress(data)
            if len(data) > 2_000_000:
                raise ProtocolError("Decoded HTTP response exceeds 2 MB")
        return data
    finally:
        conn.close()


def status_json(raw):
    data = json.loads(raw)
    if (not isinstance(data, dict) or type(data.get("out_on")) is not bool or
            any(type(data.get(k)) not in (int, float) or not math.isfinite(data[k])
                for k in ("set_v", "set_c", "out_v", "out_c"))):
        raise ProtocolError("Missing/invalid /status fields")
    return data


class Shared:
    def __init__(self, initial, report, settle):
        self.lock = threading.RLock()
        self.initial, self.report, self.settle = initial, report, settle
        self.expected = {key: initial[key] for key in FIELDS}
        self.epoch, self.state = 0, "stable"
        self.started = time.monotonic()
        self.stable_after = self.started + settle
        self.seen = {}
        self.fatal = threading.Event()
        self.enabled = None
        self.cycles = collections.defaultdict(list)
        self.connections = {}

    def token(self):
        with self.lock:
            return self.epoch, self.state, time.monotonic(), self.report.phase

    def begin(self, target):
        with self.lock:
            self.epoch += 1
            self.expected = dict(target)
            self.state, self.started = "transition", time.monotonic()
            return self.epoch

    def stable(self):
        with self.lock:
            self.state = "stable"
            self.stable_after = time.monotonic() + self.settle

    def suspend(self):
        with self.lock:
            self.state = "paused"

    def fail(self, source, message, **data):
        self.report.op(source, "invariant", time.monotonic(), False,
                       {"error_type": "invariant", "error": message, **data})
        self.fatal.set()

    def mark(self, source, field):
        key = (source, field)
        if self.seen.get(key) != self.epoch:
            self.seen[key] = self.epoch
            self.report.event("state_observed", client=source, field=str(field), epoch=self.epoch,
                              value=self.expected.get(field),
                              command_to_observation_ms=round((time.monotonic()-self.started)*1000, 3))

    def observe(self, source, token, values, web=False, telemetry=None):
        with self.lock:
            if self.state != "stable" or token[0] != self.epoch or token[1] != "stable" or token[2] < self.stable_after:
                return
            for field, value in values.items():
                expected = self.expected[field]
                if web and field != 18:
                    difference = abs(value - expected / (1000 if field == 8 else 10000))
                    correct = difference <= .0006  # /status rounds to three decimals
                else:
                    correct = value == expected
                if not correct:
                    self.fail(source, "Unexpected stable setpoint/output", register=field,
                              expected=expected, observed=value, web=web, epoch=self.epoch)
                    return
                self.mark(source, field)
            if telemetry is not None:
                voltage, current, cv, protection = telemetry
                if protection not in (0, "None"):
                    self.fail(source, "Protection activated", protection=protection)
                    return
                if self.expected[18]:
                    desired = self.expected[8] / 1000
                    limit = self.expected[9] / 10000
                    if not cv or abs(voltage-desired) > max(.03, desired*.02):
                        self.fail(source, "ON voltage did not track the setpoint in the unloaded CV test",
                                  expected_v=desired, measured_v=voltage, cv=cv)
                        return
                    if current < 0 or current > limit + max(.002, limit*.05):
                        self.fail(source, "Measured current exceeded the set limit",
                                  limit_a=limit, measured_a=current)
                        return
                    self.mark(source, "telemetry")

    def missing(self):
        with self.lock:
            required = set(FIELDS) | ({"telemetry"} if self.expected[18] else set())
            return sorted("%s/%s" % (source, field) for source in OBSERVERS for field in required
                          if self.seen.get((source, field)) != self.epoch)

    def observe_diagnostics(self, token, enabled):
        with self.lock:
            # A periodic HTTP request may straddle our Configure POST. Like a
            # register read across a command, its old epoch is not a mode fault.
            if (self.state == "stable" and token[1] == "stable" and token[0] == self.epoch and
                    self.enabled is not None and enabled != self.enabled):
                self.fail("web", "Diagnostics mode changed outside the test",
                          expected=self.enabled, observed=enabled)


def reader_worker(args, number, stop, shared, report):
    name = "reader-%d" % number
    # Identical transaction sequences on two independent sockets deliberately
    # exercise the repeated-IP and duplicate transaction-ID routing case.
    client = ModbusTCP(args.host, args.port, args.unit, args.timeout, 65535)
    stop.wait((number-1)*.13)
    try:
        while not stop.is_set():
            cycle, phase = time.monotonic(), report.phase
            for address, count in READS:
                if stop.is_set():
                    break
                token = shared.token()
                values = read_logged(client, name, report, address, count, token[3])
                relevant = {key: values[key-address] for key in FIELDS if address <= key < address+count}
                telemetry = None
                if address == 8 and count == 12:
                    if values[10] not in (0, 1) or values[9] not in (0, 1):
                        raise ProtocolError("Invalid output/CV-CC register")
                    telemetry = values[2]/1000, values[3]/10000, values[9] == 0, values[8]
                shared.observe(name, token, relevant, telemetry=telemetry)
                if shared.fatal.is_set():
                    break
                stop.wait(args.request_gap)
            duration = time.monotonic()-cycle
            with shared.lock:
                shared.cycles[(phase, name)].append(duration*1000)
            report.event("reader_cycle", client=name, mode_phase=phase, duration_ms=round(duration*1000, 3))
            if shared.fatal.is_set():
                stop.set()
                break
            stop.wait(max(0, args.read_interval-duration))
    except Exception as exc:
        shared.fail(name, "Reader stopped", **fail_details(exc))
        stop.set()
    finally:
        with shared.lock:
            shared.connections[name] = client.connections
        report.event("connections", client=name, connections=client.connections)
        client.close()


def web_worker(args, stop, shared, report):
    next_status = next_page = time.monotonic()
    next_diag = next_status + args.diagnostics_interval
    count = 0
    try:
        while not stop.is_set():
            now = time.monotonic()
            if now >= next_status:
                token, started = shared.token(), time.monotonic()
                try:
                    data = status_json(http_request(args, "/status"))
                    if type(data.get("cvmode")) is not bool or type(data.get("prot")) is not str:
                        raise ProtocolError("Missing CV/protection status")
                    report.op("web", "status", started, True, data, phase=token[3])
                    shared.observe("web", token, {8:data["set_v"], 9:data["set_c"], 18:int(data["out_on"])},
                                   web=True, telemetry=(data["out_v"], data["out_c"], data["cvmode"], data["prot"]))
                except Exception as exc:
                    report.op("web", "status", started, False, fail_details(exc), phase=token[3])
                    raise
                next_status = started + args.web_interval
            if not stop.is_set() and time.monotonic() >= next_page:
                started = time.monotonic()
                try:
                    body = http_request(args, "/control/")
                    if b"Power Supply Control" not in body:
                        raise ProtocolError("Unexpected Control page")
                    report.op("web", "page", started, True, {"bytes":len(body)})
                except Exception as exc:
                    report.op("web", "page", started, False, fail_details(exc))
                    raise
                next_page = started + args.page_interval
            if not stop.is_set() and time.monotonic() >= next_diag:
                token = shared.token()
                count += 1
                data = capture_diagnostics(args, report, "periodic_%04d" % count)
                shared.observe_diagnostics(token, data["diagnostics_enabled"])
                next_diag = time.monotonic() + args.diagnostics_interval
            if shared.fatal.is_set():
                break
            stop.wait(.02)
    except Exception as exc:
        shared.fail("web", "HTTP load worker stopped", **fail_details(exc))
        stop.set()


def check_running(shared):
    if shared.fatal.is_set():
        raise TestError("A reader/web invariant failed; see operations.csv")


def wait_observed(args, shared, report, started):
    deadline = time.monotonic() + args.observe_timeout
    while shared.missing() and time.monotonic() < deadline:
        check_running(shared)
        time.sleep(.02)
    check_running(shared)
    missing = shared.missing()
    report.op("controller", "all_observed", started, not missing,
              {"epoch":shared.epoch, "missing":missing})
    if missing:
        raise TestError("Fresh state not observed by all clients: " + repr(missing))


def snapshot(client, report, name="controller"):
    raw = read_logged(client, name, report, 0, 20)
    if raw[0] != 60065 or raw[18] not in (0, 1):
        raise TestError("Expected RD6006P model 60065 and a valid output register")
    return {i: value for i, value in enumerate(raw)}


def guards(client, report, name="controller"):
    values = {}
    for address, count in ((20, 1), (32, 1), (66, 7), (82, 2)):
        values.update({address+i: v for i, v in enumerate(read_logged(client, name, report, address, count))})
    return values


def wait_target(args, client, report, target):
    deadline, last = time.monotonic()+args.verify_timeout, None
    while time.monotonic() < deadline:
        last = snapshot(client, report)
        if all(last[key] == value for key, value in target.items()):
            return last
        time.sleep(.1)
    raise TestError("Command did not reach its target: expected=%r, observed=%r" % (target, last))


def payload_for_raw(value, scale):
    # The firmware uses truncation to a register. A quarter-LSB decimal margin
    # avoids binary floating-point rounding landing just below the intended integer.
    exact = "%.8f" % (value/scale)
    return exact if int(float(exact)*scale) == value else "%.8f" % ((value+.25)/scale)


def perform_action(args, client, shared, report, action, hold=True):
    kind, values = action
    check_running(shared)
    current = snapshot(client, report)
    if any(current[key] != shared.expected[key] for key in FIELDS):
        raise TestError("Setpoints/output changed outside this test")
    target = {**shared.expected, **values}
    epoch = shared.begin(target)
    started, ack_error = time.monotonic(), None
    try:
        if kind == "modbus_pair":
            client.write_pair(values[8], values[9])
        elif kind == "modbus_v":
            client.write_one(8, values[8])
        elif kind == "modbus_i":
            client.write_one(9, values[9])
        elif kind == "modbus_out":
            client.write_one(18, values[18])
        elif kind in ("web_v", "web_i"):
            reg, scale, path = (8, 1000, "/set_v") if kind == "web_v" else (9, 10000, "/set_i")
            if http_request(args, path, "POST", payload_for_raw(values[reg], scale)).strip() != b"OK":
                raise ProtocolError("Unexpected setpoint acknowledgement")
        elif kind == "web_out":
            if current[18] == values[18]:
                raise TestError("Plan attempted a redundant non-idempotent toggle")
            data = status_json(http_request(args, "/toggle_out"))
            if int(data["out_on"]) != values[18]:
                raise ProtocolError("Wrong web output acknowledgement")
        else:
            raise ValueError("Unknown action " + kind)
        report.op("controller", kind, started, True, {"epoch":epoch, "target":target}, client.last_tid)
    except Exception as exc:
        ack_error = exc
        report.op("controller", kind, started, False, {"epoch":epoch, "target":target, **fail_details(exc)}, client.last_tid)
    # Never resend an unacknowledged command. An explicit read distinguishes
    # an applied command with lost ACK from a command that did not execute.
    wait_target(args, client, report, target)
    shared.stable()
    report.event("command_confirmed", epoch=epoch, kind=kind, target=target,
                 acknowledgement_ok=ack_error is None,
                 confirmation_ms=round((time.monotonic()-started)*1000, 3))
    if ack_error:
        raise TestError("Command applied but ACK failed; stopping further test writes")
    wait_observed(args, shared, report, started)
    print("%-4s %-12s %.3f V / %.4f A / %s  all clients confirmed" % (
        report.phase, kind, target[8]/1000, target[9]/10000, "ON" if target[18] else "OFF"), flush=True)
    while hold and time.monotonic()-started < args.step_seconds:
        check_running(shared)
        time.sleep(max(0, min(.05, args.step_seconds-(time.monotonic()-started))))


class ConfigParser(html.parser.HTMLParser):
    """Read only the dongle form. Firmware-upload and reboot forms are ignored."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.select = None
        self.options = collections.defaultdict(list)
        self.checkbox = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.select = attrs.get("name")
        elif tag == "option" and self.select in ("timezone", "uart_baudrate"):
            self.options[self.select].append((attrs.get("value"), "selected" in attrs))
        elif tag == "input" and attrs.get("name") == "diagnostics_enabled":
            if attrs.get("type", "").lower() != "checkbox":
                raise ProtocolError("Diagnostics setting is not a checkbox")
            self.checkbox = "checked" in attrs

    def handle_endtag(self, tag):
        if tag == "select":
            self.select = None

    def value(self):
        result = {}
        for name in ("timezone", "uart_baudrate"):
            options = self.options[name]
            chosen = [value for value, selected in options if selected]
            if not options or len(chosen) > 1:
                raise ProtocolError("Cannot determine the saved " + name)
            # HTML selects their first option when no selected attribute exists.
            result[name] = chosen[0] if chosen else options[0][0]
            if result[name] is None:
                raise ProtocolError("Missing configuration option value")
        if self.checkbox is None or not result["uart_baudrate"].isdigit():
            raise ProtocolError("Requires taxom.8+ Configure diagnostics checkbox")
        result["diagnostics_enabled"] = self.checkbox
        return result


def get_config(args, report):
    start = time.monotonic()
    try:
        parser = ConfigParser()
        parser.feed(http_request(args, "/config/").decode("utf-8"))
        config = parser.value()
        report.op("config", "read", start, True, config)
        return config
    except Exception as exc:
        report.op("config", "read", start, False, fail_details(exc))
        raise


def read_logged(client, name, report, address, count, phase=None):
    start = time.monotonic()
    try:
        values = client.read(address, count)
    except Exception as exc:
        report.op(name, "read", start, False, fail_details(exc), client.last_tid, address, count, phase)
        raise
    report.op(name, "read", start, True, {"values": values}, client.last_tid, address, count, phase)
    return values


def capture_diagnostics(args, report, label, expected=None):
    start = time.monotonic()
    try:
        data = json.loads(http_request(args, "/diagnostics/modbus"))
        if (not isinstance(data, dict) or type(data.get("uptime_ms")) is not int or
                type(data.get("diagnostics_enabled")) is not bool):
            raise ProtocolError("Requires taxom.8+ diagnostics JSON")
        if expected is not None and data["diagnostics_enabled"] != expected:
            raise TestError("Diagnostics mode differs from requested mode")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", label):
            raise ValueError("Invalid snapshot name")
        (report.folder / ("diagnostics_" + label + ".json")).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        report.op("web", "diagnostics", start, True,
                  {"uptime_ms": data["uptime_ms"], "diagnostics_enabled": data["diagnostics_enabled"]})
        report.event("diagnostics", label=label, data=data)
        return data
    except Exception as exc:
        report.op("web", "diagnostics", start, False, fail_details(exc))
        raise


def set_diagnostics(args, report, initial_config, enabled):
    current = get_config(args, report)
    for key in ("timezone", "uart_baudrate"):
        if current[key] != initial_config[key]:
            raise TestError("Configuration changed outside test: " + key)
    ack_error = None
    if current["diagnostics_enabled"] != enabled:
        fields = {key: initial_config[key] for key in ("timezone", "uart_baudrate")}
        if enabled:
            fields["diagnostics_enabled"] = "1"
        start = time.monotonic()
        try:
            http_request(args, "/config/", "POST", urllib.parse.urlencode(fields), form=True)
            report.op("config", "save", start, True, {"enabled": enabled})
        except Exception as exc:
            ack_error = exc
            report.op("config", "save", start, False, fail_details(exc))
        # A lost HTTP acknowledgement must never trigger an automatic second POST.
        confirmed = get_config(args, report)
        if confirmed != {**initial_config, "diagnostics_enabled": enabled}:
            raise TestError("Configuration save was not confirmed")
    data = capture_diagnostics(args, report, "mode_" + ("on" if enabled else "off"), enabled)
    if ack_error:
        raise TestError("Configuration applied but save acknowledgement failed: " + str(ack_error))
    print("Diagnostics %s confirmed; timezone and UART configuration preserved" %
          ("ON" if enabled else "OFF"), flush=True)
    return data


def make_plan(initial, args):
    ceiling_v = min(initial[8], int(args.voltage_ceiling*1000))
    ceiling_i = min(initial[9], int(args.current_ceiling*10000))
    if ceiling_v < 100 or ceiling_i < 100:
        raise TestError("Set a safe initial voltage >=0.1 V and current limit >=0.01 A before testing")
    a = {8: ceiling_v*80//100, 9: ceiling_i*50//100}
    b = {8: ceiling_v*90//100, 9: ceiling_i*75//100}
    c = {8: ceiling_v, 9: ceiling_i}
    plan = [("modbus_out", {18:0}), ("modbus_pair", a), ("modbus_out", {18:1}),
            ("web_v", {8:b[8]}), ("modbus_i", {9:b[9]}), ("web_out", {18:0}),
            ("web_i", {9:c[9]}), ("web_v", {8:c[8]}), ("web_out", {18:1}),
            ("modbus_pair", a)]
    return plan, {"A":a, "B":b, "C":c}


def negative_tests(args, client, shared, report, max_voltage, max_current):
    if shared.expected[18] != 0:
        raise TestError("Negative-test precondition: output must be confirmed OFF")
    before = snapshot(client, report)
    expected = {key: before[key] for key in FIELDS}
    protected = guards(client, report)
    count_http = count_modbus = 0
    wrong = ("", " ", "abc", "2.6V", "1,2", "NaN", "Infinity", "-0.001", "1e999", "0x10", "2.6\x00junk")
    for path, maximum in (("/set_v", max_voltage), ("/set_i", max_current)):
        for body in (*wrong, "%.8f" % (maximum+.001), "9"*80):
            check_running(shared)
            started = time.monotonic()
            try:
                http_request(args, path, "POST", body, expected_status=400)
                current = snapshot(client, report)
                if any(current[k] != expected[k] for k in FIELDS):
                    raise TestError("Rejected HTTP request changed a live setting")
                report.op("negative", "http", started, True, {"path":path, "input":body, "expected_status":400})
                count_http += 1
            except Exception as exc:
                report.op("negative", "http", started, False, {"path":path, "input":body, **fail_details(exc)})
                raise
    v_bad, i_bad = int(round(max_voltage*1000))+1, int(round(max_current*10000))+1
    if not (v_bad <= 65535 and i_bad <= 65535):
        raise TestError("Unexpected RD6006P register limits")
    cases = [
        (struct.pack(">BHH",6,8,v_bad),3), (struct.pack(">BHH",6,8,65535),3),
        (struct.pack(">BHH",6,9,i_bad),3), (struct.pack(">BHH",6,18,2),3),
        (struct.pack(">BHH",6,18,65535),3), (b"\x06\x00\x08\x00",3),
        (struct.pack(">BHHBHH",16,8,2,4,v_bad,expected[9]),3),
        (struct.pack(">BHHBHH",16,8,2,4,expected[8],i_bad),3),
        (struct.pack(">BHHBH",16,8,2,2,expected[8]),3),
        (struct.pack(">BHHB",16,8,0,0),3),
        (struct.pack(">BHHHHBHH",23,8,2,8,2,4,v_bad,expected[9]),3),
        (struct.pack(">BHH",3,8,0),3),
        (struct.pack(">BHHH",22,8,0,65535),1)]
    for pdu, code in cases:
        check_running(shared)
        started = time.monotonic()
        try:
            response = client.exchange(pdu, allow_exception=True)
            if response != bytes([pdu[0]|0x80, code]):
                raise ProtocolError("Bad Modbus request was not rejected with the expected exception")
            current = snapshot(client, report)
            if any(current[k] != expected[k] for k in FIELDS):
                raise TestError("Rejected Modbus request changed a live setting")
            report.op("negative", "modbus", started, True,
                      {"request_hex":pdu.hex(), "exception":code}, client.last_tid)
            count_modbus += 1
        except Exception as exc:
            report.op("negative", "modbus", started, False, {"request_hex":pdu.hex(), **fail_details(exc)})
            raise
    for body in ("abc", "-1", "2"):
        started = time.monotonic()
        http_request(args, "/set_i_range", "POST", body, expected_status=400)
        report.op("negative", "range", started, True, {"input":body})
        count_http += 1
    if guards(client, report) != protected:
        raise TestError("Negative request changed a protected configuration register")
    # Also prove that a valid zero is accepted while the output is OFF.
    perform_action(args, client, shared, report, ("modbus_pair", {8:0, 9:0}), hold=False)
    perform_action(args, client, shared, report, ("modbus_pair", {8:expected[8], 9:expected[9]}), hold=False)
    print("Negative tests passed: %d HTTP + %d Modbus; settings unchanged; valid zero accepted" %
          (count_http, count_modbus), flush=True)
    return {"http_rejections":count_http, "modbus_rejections":count_modbus,
            "settings_unchanged":True, "valid_zero_accepted":True}


def numeric_delta(before, after):
    result = {}
    for key, old in before.items():
        new = after.get(key)
        if type(old) is int and type(new) is int:
            result[key] = new-old
        elif isinstance(old, dict) and isinstance(new, dict):
            result[key] = numeric_delta(old, new)
    return result


def validate_phase(args, report, before, after, elapsed, enabled, negatives):
    delta = numeric_delta(before, after)
    uptime_ms = (after["uptime_ms"]-before["uptime_ms"]) & 0xffffffff
    if abs(uptime_ms-elapsed*1000) > max(10000, elapsed*100):
        raise TestError("ESP uptime indicates a reset or a wrong diagnostic snapshot")
    if before["diagnostics_enabled"] != enabled or after["diagnostics_enabled"] != enabled:
        raise TestError("Diagnostic mode changed during phase")
    attempts = delta.get("uart", {}).get("attempts")
    if attempts is None or (attempts <= 0 if enabled else attempts != 0):
        raise TestError("Optional UART observation did not follow the diagnostics checkbox")
    timing = delta.get("timing", {})
    if not enabled and any(v.get("count", 0) != 0 for v in timing.values() if isinstance(v, dict)):
        raise TestError("Timing collection continued with diagnostics OFF")
    if enabled and timing.get("tcp_request", {}).get("count", 0) <= 0:
        raise TestError("Timing collection did not start with diagnostics ON")
    if delta.get("rejected_requests", 0) != negatives["modbus_rejections"]:
        raise TestError("Unexpected number of rejected Modbus requests")
    for path in (("gateway_failures",), ("local_reads", "failures"),
                 ("web", "status_read_failures"), ("web", "toggle_unconfirmed_writes")):
        value = delta
        for key in path:
            value = value.get(key, 0) if isinstance(value, dict) else 0
        if value:
            raise TestError("Terminal firmware failure increased: " + "/".join(path))
    if delta.get("requests", 0) <= 0:
        raise TestError("No Modbus requests recorded during phase")
    report.event("phase_validation", enabled=enabled, elapsed_s=round(elapsed,3),
                 diagnostic_delta=delta, negative_tests=negatives)
    return delta


def run_phase(args, enabled, client, shared, report, config, plan, initial_guards):
    phase = "on" if enabled else "off"
    report.set_phase("switch_" + phase)
    shared.suspend()
    with shared.lock:
        shared.enabled = None
    set_diagnostics(args, report, config, enabled)
    with shared.lock:
        shared.enabled = enabled
    report.set_phase(phase)
    shared.begin(shared.expected)
    shared.stable()
    wait_observed(args, shared, report, time.monotonic())
    before = capture_diagnostics(args, report, phase+"_before", enabled)
    started = time.monotonic()
    print("\nPHASE %s: %d identical load cycles, two readers + controller + HTTP" %
          (phase.upper(), args.cycles), flush=True)
    for cycle in range(args.cycles):
        for step, action in enumerate(plan, 1):
            print("CYCLE %d/%d STEP %d/%d" % (cycle+1,args.cycles,step,len(plan)), flush=True)
            perform_action(args, client, shared, report, action)
    perform_action(args, client, shared, report, ("modbus_out", {18:0}), hold=False)
    data = status_json(http_request(args, "/status"))
    negatives = negative_tests(args, client, shared, report, data["max_v"], data["max_c"])
    if guards(client, report) != initial_guards:
        raise TestError("Current range, battery mode, protection or PSU options changed")
    after = capture_diagnostics(args, report, phase+"_after", enabled)
    elapsed = time.monotonic()-started
    delta = validate_phase(args, report, before, after, elapsed, enabled, negatives)
    result = {"result":"PASS", "diagnostics_enabled":enabled, "duration_s":round(elapsed,3),
              "cycles_completed":args.cycles, "load_steps_completed":args.cycles*len(plan),
              "negative_tests":negatives, "diagnostics_delta":delta}
    print("PHASE %s PASS (%.1f s)\n" % (phase.upper(),elapsed), flush=True)
    return result


def restore(args, client, report, initial, initial_guards, config):
    result = {"output":False, "setpoints":False, "diagnostics":False, "guards":False}
    print("\nRestoring initial settings...", flush=True)
    try:
        current = snapshot(client, report, "restore")
        if any(current[k] != initial[k] for k in (0,1,2,3)):
            raise TestError("PSU identity changed; refusing restoration writes")
        if current[18] != 0:
            try:
                client.write_one(18,0)
            except Exception as exc:
                report.op("restore","off_ack",time.monotonic(),False,fail_details(exc))
        wait_target(args,client,report,{18:0})
        if current[8] != initial[8] or current[9] != initial[9]:
            try:
                client.write_pair(initial[8],initial[9])
            except Exception as exc:
                report.op("restore","pair_ack",time.monotonic(),False,fail_details(exc))
        wait_target(args,client,report,{8:initial[8],9:initial[9],18:0})
        result["setpoints"] = True
        result["guards"] = guards(client,report,"restore") == initial_guards
        if not result["guards"]:
            raise TestError("Protected settings changed; output left OFF")
        if initial[18]:
            try:
                client.write_one(18,1)
            except Exception as exc:
                report.op("restore","output_ack",time.monotonic(),False,fail_details(exc))
        wait_target(args,client,report,{8:initial[8],9:initial[9],18:initial[18]})
        result["output"] = True
    except Exception as exc:
        report.op("restore","psu",time.monotonic(),False,fail_details(exc))
        print("RESTORE FAILED: check physical output and recovery.json",flush=True)
    try:
        set_diagnostics(args,report,config,config["diagnostics_enabled"])
        result["diagnostics"] = True
    except Exception as exc:
        report.op("restore","diagnostics",time.monotonic(),False,fail_details(exc))
        # Read-only reconciliation, never a repeated POST after a lost ACK.
        try:
            saved=get_config(args,report)
            data=capture_diagnostics(args,report,"restore_reconcile",config["diagnostics_enabled"])
            result["diagnostics"] = saved==config and data["diagnostics_enabled"]==config["diagnostics_enabled"]
        except Exception:
            pass
    report.event("restored",details=result)
    return result


def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host",default="192.168.1.176")
    p.add_argument("--port",type=int,default=502)
    p.add_argument("--http-port",type=int,default=80)
    p.add_argument("--unit",type=int,default=1)
    p.add_argument("--cycles",type=int,default=3,help="10 steps per cycle, repeated in each diagnostic mode")
    p.add_argument("--step-seconds",type=float,default=10)
    p.add_argument("--read-interval",type=float,default=1)
    p.add_argument("--request-gap",type=float,default=.005)
    p.add_argument("--web-interval",type=float,default=1)
    p.add_argument("--page-interval",type=float,default=30)
    p.add_argument("--diagnostics-interval",type=float,default=30)
    p.add_argument("--timeout",type=float,default=5)
    p.add_argument("--verify-timeout",type=float,default=10)
    p.add_argument("--observe-timeout",type=float,default=12)
    p.add_argument("--settle",type=float,default=1)
    p.add_argument("--voltage-ceiling",type=float,default=5,help="test also never exceeds initial voltage")
    p.add_argument("--current-ceiling",type=float,default=.1,help="test also never exceeds initial current limit")
    p.add_argument("--order",choices=("off,on","on,off"),default="off,on")
    p.add_argument("--output-dir",type=Path)
    args=p.parse_args(argv)
    for name in ("step_seconds","read_interval","web_interval","page_interval","diagnostics_interval",
                 "timeout","verify_timeout","observe_timeout","voltage_ceiling","current_ceiling"):
        if not math.isfinite(getattr(args,name)) or getattr(args,name)<=0:
            p.error("--"+name.replace("_","-")+" must be finite and positive")
    if any(not math.isfinite(v) or v<0 for v in (args.request_gap,args.settle)):
        p.error("--request-gap and --settle must be finite and nonnegative")
    if args.cycles<1 or not(1<=args.port<=65535 and 1<=args.http_port<=65535 and 1<=args.unit<=247):
        p.error("Invalid cycles, port or unit")
    return args


def write_text_report(folder, summary):
    lines=["RD6006P: итоговая автоматическая проверка", "Результат: "+summary["result"],
           "Ошибок: "+str(summary["error_count"]),
           "Причина остановки: "+str(summary["fatal"] or "нет"),
           "Восстановление: "+json.dumps(summary["restoration"],ensure_ascii=False), ""]
    for name,result in summary["phases"].items():
        lines.append("Диагностика %s: %s, %.1f с, %d шагов, неверных запросов: %s" %
                     (name,result["result"],result["duration_s"],result["load_steps_completed"],
                      result["negative_tests"]))
    lines += ["", "Сравнение задержек (миллисекунды):"]
    for key, stats in summary["operations"].items():
        if key.startswith(("off/","on/")) and not "/negative/" in key:
            lines.append("%s: успешно=%d, ошибки=%d, p50=%s, p95=%s, max=%s" %
                         (key,stats["ok"],stats["errors"],stats["latency_ms"]["p50"],
                          stats["latency_ms"]["p95"],stats["latency_ms"]["max"]))
    lines += ["", "Это задержки полного запроса с ожиданием UART и сети, а не загрузка CPU ESP.",
              "Напряжение проверено по телеметрии БП; внешнего измерителя нет.",
              "Тест без нагрузки проверяет уставку тока, а не точность стабилизации тока под нагрузкой.",
              "Скрипт не обращается к GitHub. Для решения о main нужно проверить этот отчет."]
    (folder/"REPORT_RU.txt").write_text("\n".join(lines)+"\n",encoding="utf-8-sig")


def run(args):
    folder=(args.output_dir or Path(__file__).resolve().parent/"results"/(
        dt.datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+str(os.getpid()))).resolve()
    folder.mkdir(parents=True,exist_ok=False)
    report=Report(folder)
    client=ModbusTCP(args.host,args.port,args.unit,args.timeout,65535)
    stop=threading.Event()
    shared=None
    threads=[]
    initial=initial_guards=config=None
    touched=False
    phases={}
    restoration={}
    fatal=None
    interrupted=False
    started=time.monotonic()
    (folder/"run_config.json").write_text(json.dumps(
        {k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},indent=2),encoding="utf-8")
    try:
        print("RD6006P FINAL TEST %s\nTarget %s:%d / unit %d" % (VERSION,args.host,args.port,args.unit),flush=True)
        print("2 independent readers + controller + HTTP; %s diagnostics; about %.0f minutes" %
              (args.order,args.cycles*len(range(10))*args.step_seconds*2/60),flush=True)
        config=get_config(args,report)
        diag=capture_diagnostics(args,report,"initial")
        if diag.get("setpoint_validation")!="strict_v1":
            raise TestError("Install taxom.9+ before running invalid-input tests; no PSU writes were sent")
        if diag["diagnostics_enabled"]!=config["diagnostics_enabled"]:
            raise TestError("Saved diagnostics checkbox and runtime mode disagree")
        initial=snapshot(client,report,"preflight")
        initial_guards=guards(client,report,"preflight")
        if initial[16] in (1,2) or initial_guards[32]!=0:
            raise TestError("Clear PSU protection/battery mode before the unloaded test")
        plan,profiles=make_plan(initial,args)
        if initial_guards[82] and profiles["C"][8]>=initial_guards[82]:
            raise TestError("Test voltage would reach the saved overvoltage protection limit")
        journal={"host":args.host,"port":args.port,"unit":args.unit,"http_port":args.http_port,
                 "initial_registers":initial,"protected_registers":initial_guards,"config":config,"profiles":profiles}
        (folder/"recovery.json").write_text(json.dumps(journal,indent=2),encoding="utf-8")
        print("Initial: %.3f V / %.4f A / %s; diagnostics=%s" %
              (initial[8]/1000,initial[9]/10000,"ON" if initial[18] else "OFF",config["diagnostics_enabled"]),flush=True)
        for name,profile in profiles.items():
            print("Profile %s: %.3f V / %.4f A" % (name,profile[8]/1000,profile[9]/10000),flush=True)
        print("Physical output and V/I will change. Run without a load. Ctrl+C restores the initial state.",flush=True)
        touched=True  # Also set before any command whose acknowledgement might be lost.
        if initial[18]:
            client.write_one(18,0)
        wait_target(args,client,report,{18:0})
        baseline={**initial,18:0}
        shared=Shared(baseline,report,args.settle)
        report.set_phase("warmup")
        threads=[threading.Thread(target=reader_worker,args=(args,n,stop,shared,report),
                                  name="reader-%d"%n,daemon=True) for n in (1,2)]
        threads.append(threading.Thread(target=web_worker,args=(args,stop,shared,report),name="web",daemon=True))
        for thread in threads:
            thread.start()
        wait_observed(args,shared,report,time.monotonic())
        for name in args.order.split(","):
            phases[name]=run_phase(args,name=="on",client,shared,report,config,plan,initial_guards)
    except KeyboardInterrupt:
        interrupted=True
        client.close()  # An interrupted receive may leave a partial response on its socket.
        print("\nInterrupted. Restoring initial state...",flush=True)
    except Exception as exc:
        fatal=str(exc)
        report.op("main","fatal",time.monotonic(),False,fail_details(exc))
    finally:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT,signal.SIG_IGN)
        if shared:
            shared.suspend()
            with shared.lock:
                shared.enabled=None
        stop.set()
        for thread in threads:
            thread.join(args.timeout+2)
        alive=[thread.name for thread in threads if thread.is_alive()]
        if alive:
            report.op("main","workers",time.monotonic(),False,{"error_type":"workers_alive","workers":alive})
        report.set_phase("restore")
        load_connections={**(shared.connections if shared else {}),"controller":client.connections}
        if touched:
            restoration=restore(args,client,report,initial,initial_guards,config)
        client.close()
        connections={**load_connections,"controller_including_restore":client.connections}
        if touched and any(count!=1 for count in load_connections.values()):
            report.op("main","connections",time.monotonic(),False,
                      {"error_type":"reconnect","connections":connections})
        complete=set(phases)=={"off","on"}
        restored=bool(restoration) and all(restoration.values())
        result="PASS" if complete and restored and not report.error_count and not interrupted else (
            "INTERRUPTED" if interrupted and restored and not report.error_count else "FAIL")
        summary={"tool_version":VERSION,"result":result,"eligible_for_merge":result=="PASS",
                 "firmware":diag.get("version") if "diag" in locals() else None,
                 "duration_s":round(time.monotonic()-started,3),"fatal":fatal,"interrupted":interrupted,
                 "error_count":report.error_count,"error_types":dict(report.errors),
                 "initial_registers":initial,"restoration":restoration,"connections":connections,
                 "phases":phases,"operations":report.snapshot(),
                 "reader_cycle_ms":{"/".join(key):{"p50":percentile(values,.5),"p95":percentile(values,.95),
                                                      "max":round(max(values),3)}
                                    for key,values in (shared.cycles.items() if shared else [])},
                 "notes":["No command is automatically resent after an acknowledgement failure.",
                          "Recovered UART retries are allowed; terminal client/protocol/state errors fail.",
                          "Expected HTTP 400 / Modbus exception 1 or 3 from negative tests are successes.",
                          "Detailed metrics freeze when diagnostics is off; basic counters remain active.",
                          "Measured durations are not ESP CPU utilization.",
                          "No external voltage meter or constant-current load test; no GitHub/merge operation."]}
        (folder/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
        write_text_report(folder,summary)
        report.close()
        archive=folder.with_suffix(".zip")
        with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED) as output:
            for path in sorted(folder.iterdir()):
                if path.is_file():
                    output.write(path,folder.name+"/"+path.name)
        print("\nRESULT: %s\nErrors: %d\nRestoration: %s\nSend this ZIP: %s" %
              (result,report.error_count,restoration,archive),flush=True)
    return 0 if result=="PASS" else (130 if result=="INTERRUPTED" else 1)


if __name__=="__main__":
    if sys.version_info<(3,9):
        raise SystemExit("Python 3.9 or newer is required; no pip packages are needed.")
    raise SystemExit(run(parse_args()))


