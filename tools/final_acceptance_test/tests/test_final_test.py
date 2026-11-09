"""Fault-injection checks against a localhost PSU/ESP emulator, never real hardware."""
import collections
import contextlib
import html
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
import urllib.parse

SCRIPT=Path(__file__).resolve().parents[1]/"rd6006_final_test.py"
spec=importlib.util.spec_from_file_location("rd_final",SCRIPT)
rd=importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)


class PSU:
    def __init__(self,output=0,enabled=False,fault=None):
        self.lock=threading.RLock()
        self.reg=[0]*128
        for key,value in {0:60065,1:17,2:3111,3:143,5:26,8:2600,9:500,14:6578,
                          18:output,33:2,69:1,72:4,82:60000,83:60000}.items():
            self.reg[key]=value
        self.enabled=enabled
        self.timezone="Etc/GMT+5"
        self.baud="115200"
        self.fault,self.used=fault,False
        self.writes=[]
        self.posts=[]
        self.paths=collections.Counter()
        self.requests=self.attempts=self.rejected=0
        self.active=self.peak=0
        self.started=time.monotonic()

    def fault_once(self,name):
        with self.lock:
            if self.fault==name and not self.used:
                self.used=True
                return True
        return False

    def uart(self,count=1):
        if self.enabled or self.fault=="off_tracing_active":
            self.attempts+=count

    def refresh(self):
        self.reg[10]=self.reg[8] if self.reg[18] else 0
        if self.fault=="wrong_voltage" and self.enabled and self.reg[18]:
            self.reg[10]=0
        self.reg[11]=0

    def status(self):
        with self.lock:
            self.uart(3)
            self.refresh()
            return {"set_v":self.reg[8]/1000,"set_c":round(self.reg[9]/10000,3),
                    "out_v":self.reg[10]/1000,"out_c":self.reg[11]/10000,
                    "out_on":bool(self.reg[18]),"cvmode":True,"prot":"None","max_v":60.1,"max_c":6.0}

    def diagnostic(self):
        with self.lock:
            return {"version":"1.7.0-taxom.9","uptime_ms":int((time.monotonic()-self.started)*1000),
                    "diagnostics_enabled":self.enabled,"detail_scope":"enabled_periods_since_boot",
                    "setpoint_validation":"legacy" if self.fault=="old_firmware" else "strict_v1",
                    "requests":self.requests,"read_retries":0,"recovered_reads":0,"uart_timeouts":0,
                    "gateway_failures":0,"rejected_requests":self.rejected,
                    "uart":{"attempts":self.attempts,"crc_errors":0},
                    "timing":{"tcp_request":{"count":self.attempts}},
                    "local_reads":{"failures":0},"web":{"status_read_failures":0,"toggle_unconfirmed_writes":0}}

    def write(self,method,address,values):
        with self.lock:
            assert address in (8,9,18) and (len(values)==1 or (address==8 and len(values)==2))
            self.writes.append((method,address,list(values),self.enabled))
            if (address==8 and values==[2600,500] and len(values)==2 and
                    self.fault_once("restore_pair_ignored")):
                return
            if address==8 and len(values)==2 and self.fault_once("ignored_pair"):
                return
            self.reg[address:address+len(values)]=values


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        s=self.server.state
        self.request.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        with s.lock:
            s.active+=1
            s.peak=max(s.peak,s.active)
        def receive(count):
            value=bytearray()
            while len(value)<count:
                chunk=self.request.recv(count-len(value))
                if not chunk:
                    return None
                value.extend(chunk)
            return bytes(value)
        try:
            while True:
                header=receive(7)
                if header is None:
                    return
                tid,proto,length,unit=struct.unpack(">HHHB",header)
                pdu=receive(length-1)
                if not pdu:
                    return
                fn=pdu[0]
                address=count=None
                values=None
                error=None
                with s.lock:
                    s.requests+=1
                    if fn==3:
                        if len(pdu)!=5:
                            error=3
                        else:
                            _,address,count=struct.unpack(">BHH",pdu)
                            if not count or count>125:
                                error=3
                    elif fn==6:
                        if len(pdu)!=5:
                            error=3
                        else:
                            _,address,value=struct.unpack(">BHH",pdu)
                            values=[value]
                    elif fn==16:
                        if len(pdu)<6:
                            error=3
                        else:
                            _,address,count,size=struct.unpack(">BHHB",pdu[:6])
                            if not count or count>123 or size!=count*2 or len(pdu)!=6+size:
                                error=3
                            else:
                                values=list(struct.unpack(">"+"H"*count,pdu[6:]))
                    elif fn==23:
                        error=3
                    elif fn==22:
                        error=1
                    else:
                        error=1
                    if values is not None:
                        if any((address+i==8 and v>60100) or (address+i==9 and v>60000) or
                               (address+i==18 and v>1) for i,v in enumerate(values)):
                            error=3
                    if error:
                        s.rejected+=1
                        result=bytes([fn|0x80,error])
                    elif fn==3:
                        s.uart()
                        s.refresh()
                        values=s.reg[address:address+count]
                        if s.fault=="false_zero" and s.reg[18] and address==8 and count==1:
                            values=[0]
                        result=bytes([3,count*2])+struct.pack(">"+"H"*count,*values)
                    else:
                        s.uart()
                        s.write("modbus",address,values)
                        if fn==16 and s.fault_once("lost_pair_ack"):
                            return
                        result=pdu if fn==6 else struct.pack(">BHH",16,address,count)
                        if fn==16 and s.fault_once("wrong_pair_echo"):
                            result=struct.pack(">BHH",16,address,1)
                if s.fault_once("wrong_tid"):
                    tid^=1
                frame=struct.pack(">HHHB",tid,proto,len(result)+1,unit)+result
                for part in (frame[:2],frame[2:7],frame[7:8],frame[8:]):
                    if part:
                        self.request.sendall(part)
        except (ConnectionError,OSError):
            pass
        finally:
            with s.lock:
                s.active-=1


class HTTPHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*_):
        pass

    def reply(self,body,status=200):
        if isinstance(body,str):
            body=body.encode()
        self.send_response(status)
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError,OSError):
            pass

    def do_GET(self):
        s=self.server.state
        with s.lock:
            s.paths[self.path]+=1
        if self.path=="/config/":
            checked=" checked" if s.enabled else ""
            self.reply("<form method='post'><select name='timezone'><option value='%s' selected>Zone</option></select>"
                       "<select name='uart_baudrate'><option value='%s' selected>Baud</option></select>"
                       "<input type='checkbox' name='diagnostics_enabled' value='1'%s></form>" %
                       (html.escape(s.timezone,quote=True),s.baud,checked))
        elif self.path=="/diagnostics/modbus":
            self.reply(json.dumps(s.diagnostic()))
        elif self.path=="/status":
            if s.fault_once("http_status_error"):
                self.reply("unavailable",500)
            else:
                self.reply(json.dumps(s.status()))
        elif self.path=="/control/":
            self.reply("<html><body>Power Supply Control</body></html>")
        elif self.path=="/toggle_out":
            s.uart()
            s.write("web",18,[1-s.reg[18]])
            self.reply(json.dumps(s.status()))
        else:
            self.reply("unknown",404)

    def do_POST(self):
        s=self.server.state
        body=self.rfile.read(int(self.headers.get("Content-Length",0))).decode("ascii")
        with s.lock:
            s.paths[self.path]+=1
        if self.path=="/config/":
            fields=urllib.parse.parse_qs(body,keep_blank_values=True)
            if fields.get("timezone")!=[s.timezone] or fields.get("uart_baudrate")!=[s.baud]:
                raise AssertionError("Script altered timezone or baudrate")
            enabled=fields.get("diagnostics_enabled")==["1"]
            s.posts.append(enabled)
            if s.fault!="ignored_config" and not (s.fault=="restore_config_ignored" and not enabled):
                s.enabled=enabled
            if s.fault_once("lost_config_ack"):
                return
            self.reply("<script>location.replace('/config/')</script>")
        elif self.path in ("/set_v","/set_i"):
            scale,reg,maximum=(1000,8,60.1) if self.path=="/set_v" else (10000,9,6.0)
            valid=bool(__import__("re").fullmatch(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*",body))
            try:
                value=float(body)
                valid=valid and 0<=value<=maximum
            except ValueError:
                valid=False
            if not valid:
                if s.fault=="accepts_bad_input" and body=="abc":
                    s.write("web",reg,[0])
                    self.reply("OK")
                else:
                    self.reply("Invalid setpoint",400)
            else:
                s.uart()
                s.write("web",reg,[int(value*scale)])
                self.reply("OK")
        elif self.path=="/set_i_range":
            self.reply("Unsupported",400)
        else:
            self.reply("unknown",404)


@contextlib.contextmanager
def emulator(output=0,enabled=False,fault=None):
    state=PSU(output,enabled,fault)
    state.tcp=Server(("127.0.0.1",0),TCPHandler)
    state.http=http.server.ThreadingHTTPServer(("127.0.0.1",0),HTTPHandler)
    state.tcp.state=state.http.state=state
    for server in (state.tcp,state.http):
        threading.Thread(target=server.serve_forever,kwargs={"poll_interval":.01},daemon=True).start()
    try:
        yield state
    finally:
        state.tcp.shutdown()
        state.http.shutdown()
        state.tcp.server_close()
        state.http.server_close()


class Tests(unittest.TestCase):
    def command(self,state,folder,extra=()):
        return [sys.executable,str(SCRIPT),"--host","127.0.0.1","--port",str(state.tcp.server_address[1]),
                "--http-port",str(state.http.server_address[1]),"--cycles","1","--step-seconds",".02",
                "--read-interval",".02","--request-gap",".0001","--web-interval",".02",
                "--page-interval",".1","--diagnostics-interval",".1","--timeout",".5",
                "--verify-timeout",".6","--observe-timeout","1.5","--settle",".006",
                "--output-dir",str(folder),*extra]

    def run_case(self,fault=None,output=0,enabled=False,extra=()):
        with tempfile.TemporaryDirectory() as temp,emulator(output,enabled,fault) as state:
            folder=Path(temp)/"run"
            process=subprocess.run(self.command(state,folder,extra),capture_output=True,text=True,timeout=30)
            summary=json.loads((folder/"summary.json").read_text())
            events=[json.loads(line) for line in (folder/"events.jsonl").read_text().splitlines()]
            return process,summary,events,state

    def test_complete_off_on_modes_commands_and_negatives(self):
        p,s,events,state=self.run_case()
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(s["result"],"PASS")
        self.assertTrue(s["eligible_for_merge"])
        self.assertTrue(all(s["restoration"].values()))
        self.assertEqual((state.reg[8],state.reg[9],state.reg[18],state.enabled),(2600,500,0,False))
        self.assertEqual(state.peak,3)
        self.assertEqual(s["connections"],{"reader-1":1,"reader-2":1,"controller":1,"controller_including_restore":1})
        self.assertEqual(set(s["phases"]),{"off","on"})
        for mode in ("off","on"):
            phase=s["phases"][mode]
            self.assertEqual(phase["load_steps_completed"],10)
            self.assertEqual(phase["negative_tests"]["modbus_rejections"],13)
            self.assertEqual(phase["negative_tests"]["http_rejections"],29)
            self.assertTrue(phase["negative_tests"]["valid_zero_accepted"])
        self.assertEqual(state.posts,[True,False])
        self.assertTrue({("web",8),("web",9),("web",18),("modbus",8),("modbus",9),("modbus",18)} <=
                        {(method,addr) for method,addr,_,_ in state.writes})
        for event in events:
            if event["event"]=="command_confirmed":
                seen={(x["client"],x["field"]) for x in events if x["event"]=="state_observed" and x["epoch"]==event["epoch"]}
                self.assertTrue({(name,str(reg)) for name in rd.OBSERVERS for reg in rd.FIELDS} <= seen)
        self.assertGreater(state.paths["/control/"],1)
        self.assertGreater(state.paths["/status"],10)

    def test_initial_on_and_diagnostics_on_are_restored(self):
        p,s,_,state=self.run_case(output=1,enabled=True)
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual((state.reg[8],state.reg[9],state.reg[18],state.enabled),(2600,500,1,True))
        self.assertTrue(all(s["restoration"].values()))

    def test_reverse_order(self):
        p,s,_,state=self.run_case(extra=("--order","on,off"))
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertEqual(set(s["phases"]),{"off","on"})
        self.assertFalse(state.enabled)

    def test_old_firmware_stops_before_any_write(self):
        p,s,_,state=self.run_case("old_firmware")
        self.assertEqual(p.returncode,1)
        self.assertEqual(state.writes,[])
        self.assertEqual(state.posts,[])
        self.assertFalse(s["eligible_for_merge"])

    def test_lost_pair_ack_not_repeated_and_state_restored(self):
        p,s,_,state=self.run_case("lost_pair_ack")
        self.assertEqual(p.returncode,1,p.stdout)
        pairs=[values for method,reg,values,_ in state.writes if method=="modbus" and reg==8 and len(values)==2]
        self.assertEqual(pairs,[[2080,250],[2600,500]])
        self.assertTrue(all(s["restoration"].values()))

    def test_wrong_echo_fails(self):
        p,s,_,_=self.run_case("wrong_pair_echo")
        self.assertEqual(p.returncode,1)
        self.assertFalse(s["eligible_for_merge"])
        self.assertTrue(all(s["restoration"].values()))

    def test_ignored_setpoint_is_detected(self):
        p,s,_,state=self.run_case("ignored_pair")
        self.assertEqual(p.returncode,1,p.stdout)
        self.assertTrue(all(s["restoration"].values()))
        self.assertEqual(state.reg[8],2600)

    def test_false_zero_is_detected(self):
        p,s,_,_=self.run_case("false_zero")
        self.assertEqual(p.returncode,1,p.stdout)
        self.assertFalse(s["eligible_for_merge"])
        self.assertTrue(all(s["restoration"].values()))

    def test_actual_voltage_tracking_is_checked(self):
        p,s,_,_=self.run_case("wrong_voltage")
        self.assertEqual(p.returncode,1,p.stdout)
        self.assertFalse(s["eligible_for_merge"])
        self.assertTrue(all(s["restoration"].values()))

    def test_http_error_is_not_hidden(self):
        p,s,_,_=self.run_case("http_status_error")
        self.assertEqual(p.returncode,1)
        self.assertTrue(all(s["restoration"].values()))

    def test_config_must_actually_change(self):
        p,s,_,state=self.run_case("ignored_config")
        self.assertEqual(p.returncode,1,p.stdout)
        self.assertFalse(state.enabled)
        self.assertTrue(all(s["restoration"].values()))

    def test_config_lost_ack_is_not_reposted(self):
        p,s,_,state=self.run_case("lost_config_ack")
        self.assertEqual(p.returncode,1,p.stdout)
        self.assertEqual(state.posts,[True,False])
        self.assertTrue(all(s["restoration"].values()))

    def test_negative_test_catches_an_accepted_invalid_value(self):
        p,s,_,state=self.run_case("accepts_bad_input")
        self.assertEqual(p.returncode,1,p.stdout)
        self.assertEqual((state.reg[8],state.reg[9],state.reg[18]),(2600,500,0))
        self.assertFalse(s["eligible_for_merge"])

    def test_off_mode_really_stops_detailed_counters(self):
        p,s,_,_=self.run_case("off_tracing_active")
        self.assertEqual(p.returncode,1,p.stdout)
        self.assertIn("checkbox",s["fatal"])

    def test_wrong_transaction_id_is_detected(self):
        p,s,_,state=self.run_case("wrong_tid")
        self.assertEqual(p.returncode,1)
        self.assertEqual(state.writes,[])

    def test_ctrl_c_restores_initial_on(self):
        if not hasattr(signal,"SIGINT"):
            self.skipTest("SIGINT unavailable")
        with tempfile.TemporaryDirectory() as temp,emulator(output=1,enabled=True) as state:
            folder=Path(temp)/"run"
            process=subprocess.Popen(self.command(state,folder,("--step-seconds","1")),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            deadline=time.monotonic()+8
            while time.monotonic()<deadline and len(state.writes)<3 and process.poll() is None:
                time.sleep(.02)
            process.send_signal(signal.SIGINT)
            stdout,stderr=process.communicate(timeout=10)
            summary=json.loads((folder/"summary.json").read_text())
            self.assertEqual(process.returncode,130,stdout+stderr)
            self.assertEqual(summary["result"],"INTERRUPTED")
            self.assertTrue(all(summary["restoration"].values()))
            self.assertEqual((state.reg[8],state.reg[9],state.reg[18],state.enabled),(2600,500,1,True))

    def test_limits_never_exceed_initial_setpoints(self):
        args=rd.parse_args([])
        plan,profiles=rd.make_plan({8:2600,9:500},args)
        self.assertEqual(profiles["C"],{8:2600,9:500})
        for _,values in plan:
            self.assertLessEqual(values.get(8,0),2600)
            self.assertLessEqual(values.get(9,0),500)
        _,profiles=rd.make_plan({8:60000,9:60000},args)
        self.assertEqual(profiles["C"],{8:5000,9:1000})

    def test_failed_setpoint_restoration_keeps_output_off(self):
        p,s,_,state=self.run_case("restore_pair_ignored",output=1)
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertFalse(s["eligible_for_merge"])
        self.assertFalse(s["restoration"]["setpoints"])
        self.assertFalse(s["restoration"]["output"])
        self.assertEqual(state.reg[18],0)

    def test_failed_diagnostics_restoration_cannot_pass(self):
        p,s,_,state=self.run_case("restore_config_ignored")
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertFalse(s["eligible_for_merge"])
        self.assertFalse(s["restoration"]["diagnostics"])
        self.assertTrue(state.enabled)

    def test_periodic_diagnostics_crossing_mode_switch_is_not_a_false_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            report=rd.Report(Path(temp))
            shared=rd.Shared({8:2600,9:500,18:0},report,0)
            shared.enabled=False
            old_token=shared.token()
            shared.suspend()
            shared.enabled=True
            shared.begin(shared.expected)
            shared.stable()
            shared.observe_diagnostics(old_token,False)
            self.assertFalse(shared.fatal.is_set())
            shared.observe_diagnostics(shared.token(),False)
            self.assertTrue(shared.fatal.is_set())
            report.close()


if __name__=="__main__":
    unittest.main(verbosity=2)
