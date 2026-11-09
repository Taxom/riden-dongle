"""Offline emulator tests. Never connects to the user's PSU."""
import collections
import contextlib
import http.server
import importlib.util
import json
from pathlib import Path
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "rd6006_multi_client_test.py"
spec = importlib.util.spec_from_file_location("rdtest", SCRIPT)
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)


class PSU:
    def __init__(self, output=0, fault=None):
        self.reg = [0] * 128
        for k, v in {0: 60065, 4: 0, 5: 28, 8: 2600, 9: 500, 10: 2599,
                     14: 6576, 18: output, 33: 2, 34: 0, 35: 16}.items():
            self.reg[k] = v
        self.lock = threading.Lock()
        self.fault, self.used = fault, False
        self.writes, self.paths = [], collections.Counter()
        self.active = self.peak = self.requests = 0
        self.readers = set()
        self.tcp = self.http = None
        self.birth = time.monotonic()

    def status(self):
        with self.lock:
            return {"out_on": bool(self.reg[18]), "set_v": self.reg[8] / 1000,
                    "set_c": self.reg[9] / 10000, "out_v": 2.599 if self.reg[18] else 0,
                    "out_c": 0.0}

    def fault_once(self, name):
        with self.lock:
            if self.fault == name and not self.used:
                self.used = True
                return True
        return False


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        state = self.server.state
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with state.lock:
            state.active += 1
            state.peak = max(state.peak, state.active)
        def receive(n):
            a = bytearray()
            while len(a) < n:
                part = self.request.recv(n - len(a))
                if not part:
                    return None
                a.extend(part)
            return bytes(a)
        try:
            while True:
                header = receive(7)
                if header is None:
                    return
                tid, proto, length, unit = struct.unpack(">HHHB", header)
                pdu = receive(length - 1)
                if not pdu:
                    return
                fc, addr, value = struct.unpack(">BHH", pdu)
                with state.lock:
                    state.requests += 1
                if fc == 3:
                    if addr == 14 and state.fault_once("read_exception"):
                        result = bytes([0x83, 0x0B])
                    else:
                        with state.lock:
                            values = state.reg[addr:addr + value]
                        result = bytes([3, value * 2]) + struct.pack(">" + "H" * value, *values)
                        if addr == 8 and state.fault_once("setpoint_zero"):
                            result = bytes([3, 2, 0, 0])
                    if state.fault_once("wrong_tid"):
                        tid ^= 1
                elif fc == 6:
                    if addr != 18 or value not in (0, 1):
                        raise AssertionError("Unauthorized write: %d=%d" % (addr, value))
                    suppress = state.fault_once("ignore_write")
                    with state.lock:
                        state.writes.append(("modbus", addr, value))
                        if not suppress:
                            state.reg[addr] = value
                    if state.fault_once("lost_modbus_ack"):
                        return
                    result = pdu
                    if state.fault_once("wrong_echo"):
                        result = struct.pack(">BHH", 6, addr, 1 - value)
                else:
                    raise AssertionError("Unexpected function %d" % fc)
                frame = struct.pack(">HHHB", tid, proto, 1 + len(result), unit) + result
                # Split the MBAP header and payload over multiple send calls.
                for chunk in (frame[:2], frame[2:7], frame[7:8], frame[8:]):
                    if chunk:
                        self.request.sendall(chunk)
        except (ConnectionError, OSError):
            pass
        finally:
            with state.lock:
                state.active -= 1


class HTTPHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        s = self.server.state
        with s.lock:
            s.paths[self.path] += 1
        if self.path == "/status":
            data = json.dumps(s.status()).encode()
        elif self.path == "/toggle_out":
            with s.lock:
                s.reg[18] = 1 - s.reg[18]
                s.writes.append(("web", 18, s.reg[18]))
            data = json.dumps(s.status()).encode()
            if s.fault_once("lost_web_ack"):
                time.sleep(0.7)
        elif self.path == "/control/":
            data = b"<html><body>Power Supply Control</body></html>"
        elif self.path == "/diagnostics/modbus":
            data = json.dumps({"version": "offline-mock", "uptime_ms": int((time.monotonic()-s.birth)*1000),
                               "requests": s.requests, "read_retries": 0, "recovered_reads": 0,
                               "uart_timeouts": 0, "gateway_failures": 0,
                               "uart": {"attempts": s.requests, "crc_errors": 0},
                               "clock_sync": {"writes": 0}}).encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (ConnectionError, OSError):
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@contextlib.contextmanager
def emulator(output=0, fault=None):
    state = PSU(output, fault)
    state.tcp = Server(("127.0.0.1", 0), TCPHandler)
    state.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HTTPHandler)
    state.tcp.state = state.http.state = state
    for server in (state.tcp, state.http):
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        yield state
    finally:
        state.tcp.shutdown()
        state.http.shutdown()
        state.tcp.server_close()
        state.http.server_close()


class Tests(unittest.TestCase):
    def command(self, state, output, extra=()):
        return [sys.executable, str(SCRIPT), "--host", "127.0.0.1",
                "--port", str(state.tcp.server_address[1]), "--http-port", str(state.http.server_address[1]),
                "--duration", "1.8", "--toggle-interval", ".28", "--read-interval", ".055",
                "--request-gap", ".0002", "--web-interval", ".05", "--page-interval", ".16",
                "--diagnostics-interval", ".2", "--timeout", ".4", "--verify-timeout", ".6",
                "--observe-timeout", ".7", "--settle", ".01", "--output-dir", str(output), *extra]

    def run_case(self, fault=None, initial=0, extra=()):
        with tempfile.TemporaryDirectory() as temp, emulator(initial, fault) as state:
            folder = Path(temp) / "run"
            process = subprocess.run(self.command(state, folder, extra), capture_output=True, text=True, timeout=20)
            summary = json.loads((folder / "summary.json").read_text())
            events = [json.loads(x) for x in (folder / "events.jsonl").read_text().splitlines()]
            return process, summary, events, state

    def test_healthy_mixed_and_both_directions(self):
        p, s, events, state = self.run_case()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(s["result"], "PASS")
        self.assertTrue(s["output_restored"])
        self.assertEqual(state.reg[18], 0)
        self.assertEqual(state.peak, 3)
        self.assertTrue({("web", 0), ("web", 1), ("modbus", 0), ("modbus", 1)} <=
                        {(method, value) for method, _, value in state.writes})
        verified = [e for e in events if e["event"] == "output_verified"]
        for e in verified:
            observers = {x["client"] for x in events if x["event"] == "state_observed" and x["epoch"] == e["epoch"]}
            self.assertEqual(observers, {"reader-1", "reader-2", "web"})
        self.assertGreater(state.paths["/control/"], 1)
        self.assertGreater(state.paths["/status"], 5)
        self.assertGreater(state.paths["/diagnostics/modbus"], 2)
        self.assertEqual((state.reg[8], state.reg[9]), (2600, 500))

    def test_all_four_modbus_slots(self):
        p, s, _, state = self.run_case(extra=("--readers", "3", "--duration", ".8"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(state.peak, 4)
        self.assertTrue(s["output_restored"])

    def test_lost_modbus_ack_is_not_resent(self):
        p, s, _, state = self.run_case("lost_modbus_ack", extra=("--control-mode", "modbus", "--duration", ".4", "--toggle-interval", "2"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(s["error_count"], 1)
        self.assertEqual(state.writes, [("modbus", 18, 1), ("modbus", 18, 0)])
        self.assertTrue(s["output_restored"])

    def test_lost_web_ack_is_not_resent(self):
        p, s, _, state = self.run_case("lost_web_ack", extra=("--control-mode", "web", "--duration", ".4", "--toggle-interval", "2"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(s["error_count"], 1)
        self.assertEqual(state.paths["/toggle_out"], 1)
        self.assertEqual(state.writes, [("web", 18, 1), ("modbus", 18, 0)])
        self.assertTrue(s["output_restored"])

    def test_read_exception_remains_visible(self):
        p, s, _, state = self.run_case("read_exception", extra=("--duration", ".6"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertEqual(s["error_types"]["DeviceException"], 1)
        self.assertTrue(s["output_restored"])
        self.assertGreater(s["operations"]["reader-1/read"]["ok"], 5)

    def test_unapplied_command_fails_verification(self):
        p, s, _, state = self.run_case("ignore_write", extra=("--control-mode", "modbus", "--duration", ".4"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("did not confirm", s["fatal_error"])
        self.assertTrue(s["output_restored"])
        self.assertEqual(state.reg[18], 0)

    def test_setpoint_zero_aborts_and_restores(self):
        p, s, _, state = self.run_case("setpoint_zero", extra=("--duration", ".5"))
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("invariant", s["error_types"])
        self.assertTrue(s["output_restored"])
        self.assertEqual(state.reg[18], 0)

    def test_wrong_tid_rejected(self):
        with emulator(fault="wrong_tid") as state:
            c = rd.ModbusTCP("127.0.0.1", state.tcp.server_address[1], 1, .5, 100)
            with self.assertRaises(rd.ProtocolError):
                c.read(8, 1)
            self.assertIsNone(c.sock)
            self.assertEqual(c.read(8, 1), [2600])
            c.close()

    def test_wrong_write_echo_rejected(self):
        with emulator(fault="wrong_echo") as state:
            c = rd.ModbusTCP("127.0.0.1", state.tcp.server_address[1], 1, .5, 100)
            with self.assertRaises(rd.ProtocolError):
                c.write_output(1)
            self.assertIsNone(c.sock)
            self.assertEqual(c.read(18, 1), [1])
            c.close()

    def test_inflight_observation_not_misclassified(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = rd.Report(Path(tmp))
            s = rd.Shared({8: 2600, 9: 500, 18: 0}, r, {"reader"}, 0)
            old_token = s.token()
            s.transition(1)
            s.stable()
            s.observe("reader", old_token, 0)
            self.assertFalse(s.fatal.is_set())
            s.observe("reader", s.token(), 1)
            self.assertEqual(s.missing(), [])
            s.observe("reader", s.token(), 0)
            self.assertTrue(s.fatal.is_set())
            r.close()

    def test_ctrl_c_restores_initial_on(self):
        with tempfile.TemporaryDirectory() as temp, emulator(output=1) as state:
            folder = Path(temp) / "run"
            process = subprocess.Popen(self.command(state, folder, ("--duration", "20", "--toggle-interval", "3")),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if any(v == 0 for _, _, v in state.writes):
                        break
                    time.sleep(.02)
                else:
                    self.fail("No output command before SIGINT")
                process.send_signal(signal.SIGINT)
                out, err = process.communicate(timeout=12)
                s = json.loads((folder / "summary.json").read_text())
                self.assertEqual(s["result"], "INTERRUPTED", out + err)
                self.assertEqual(process.returncode, 130, out + err)
                self.assertTrue(s["output_restored"])
                self.assertEqual(state.reg[18], 1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_no_web_mode_has_no_http_requests(self):
        p, s, _, state = self.run_case(extra=("--no-web", "--duration", ".6"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(state.paths, {})
        self.assertEqual(s["result"], "PASS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
