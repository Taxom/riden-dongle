#include <riden_modbus_bridge/riden_modbus_bridge.h>
#include <riden_logging/web_diagnostics.h>
#include <riden_http_server/setpoint_input.h>
#include <cassert>
#include <iostream>

uint64_t host_time_us=10000;
HostSerial Serial;
static uint16_t regs[512]={};
static uint16_t failing_reg=0xffff;
static unsigned read_failures=0, reads[512]={}, writes=0;
static bool corrupt_read=false, corrupt_ack=false, drop_ack=false;
static bool apply_write=true, fail_after_write=false, fail_reconcile=false;
static uint8_t exception_code=0;

namespace RidenDongle {
class RidenHttpServer {
  public:
    RidenModbus &modbus;
    TimingStats status_time;
    struct Http {
        String body;
        int status=0;
        String plain;
        String arg(const char *) { return plain; }
        void send(int code, const char *, const String &text) { status=code;body=text; }
        void sendContent(const char *) {}
    } server;
    explicit RidenHttpServer(RidenModbus &bus) : modbus(bus) {}
    void handle_status_get();
    bool send_status(const char *failure_message);
    void handle_toggle_out();
    void handle_set_i();
    void handle_set_v();
    void handle_set_i_range();
};
}
using namespace RidenDongle;

// PRODUCTION_STATUS_AND_TOGGLE

static std::vector<uint8_t> frame(uint8_t address, std::vector<uint8_t> pdu) {
    pdu.insert(pdu.begin(),address);
    uint16_t crc=0xffff;
    for(uint8_t byte:pdu) {
        crc ^= byte;
        for(unsigned i=0;i<8;++i) crc=crc&1 ? (crc>>1)^0xa001 : crc>>1;
    }
    pdu.push_back(crc);pdu.push_back(crc>>8);return pdu;
}

static void emulate(const std::vector<uint8_t> &request) {
    const uint8_t fn=request[1];
    const uint16_t reg=(uint16_t(request[2])<<8)|request[3];
    const uint16_t argument=(uint16_t(request[4])<<8)|request[5];
    std::vector<uint8_t> pdu;
    bool bad_crc=false;
    if(fn==3) {
        ++reads[reg];
        if(reg==failing_reg && read_failures) { --read_failures;return; }
        if(exception_code && reg==18) {
            Serial.deliver(frame(request[0],{0x83,exception_code}));exception_code=0;return;
        }
        pdu={3,uint8_t(argument*2)};
        for(unsigned i=0;i<argument;++i) {pdu.push_back(regs[reg+i]>>8);pdu.push_back(regs[reg+i]);}
        if(corrupt_read && reg==failing_reg) {corrupt_read=false;bad_crc=true;}
    } else if(fn==6) {
        assert((reg==18 && argument<=1) || reg==8 || reg==9);
        ++writes;
        if(apply_write) regs[reg]=argument;
        pdu.assign(request.begin()+1,request.begin()+6);
        if(fail_after_write) {failing_reg=20;read_failures=2;fail_after_write=false;}
        if(fail_reconcile) {failing_reg=18;read_failures=2;fail_reconcile=false;}
        if(drop_ack) {drop_ack=false;return;}
        if(corrupt_ack) {corrupt_ack=false;bad_crc=true;}
    } else { std::abort(); }
    auto response=frame(request[0],pdu);
    if(bad_crc) response.back()^=0xff;
    Serial.deliver(std::move(response));
}

static void reset_faults(RidenHttpServer &http) {
    failing_reg=0xffff;read_failures=0;
    corrupt_read=corrupt_ack=drop_ack=fail_after_write=fail_reconcile=false;
    apply_write=true;exception_code=0;
    std::fill(std::begin(reads),std::end(reads),0);
    web_diagnostics()={};http.server.status=0;http.server.body.clear();
}

int main(int argc,char **) {
    set_detailed_diagnostics_enabled(argc>1);
    regs[0]=60065;regs[8]=2600;regs[9]=500;regs[10]=2599;regs[14]=6576;
    Serial.on_request=emulate;
    static RidenModbus bus;assert(bus.begin());
    RidenHttpServer http(bus);

    const char *invalid[]={"", " ", "abc", "2.6V", "1,2", "NaN", "nan", "Infinity", "inf", "-1",
                           "1e309", "1e-9999", "1000000", "0x10", ".", "1 2", "1e", "2.6junk"};
    for(unsigned output=0;output<=1;++output) {
        regs[18]=output;
        for(const char *text:invalid) {
            const auto before=Serial.requests.size();
            http.server.plain=text;http.handle_set_v();assert(http.server.status==400);
            http.handle_set_i();assert(http.server.status==400);
            assert(Serial.requests.size()==before && regs[8]==2600 && regs[9]==500 && regs[18]==output);
        }
        for(const char *text:{"60.1001", "61", "65535"}) {
            const auto before=Serial.requests.size();http.server.plain=text;http.handle_set_v();
            assert(http.server.status==400 && Serial.requests.size()==before);
        }
        for(const char *text:{"6.0001", "6.1", "65535"}) {
            const auto before=Serial.requests.size();http.server.plain=text;http.handle_set_i();
            assert(http.server.status==400 && Serial.requests.size()==before);
        }
    }
    http.server.plain=String("2.6\0junk",8);http.handle_set_v();assert(http.server.status==400);
    std::cout<<"PASS: malformed/non-finite/negative/over-limit web setpoints never reach UART from ON or OFF\n";
    for(const char *text:{"0", "60.1", " 2.6 ", "2.6e0"}) {
        http.server.plain=text;http.handle_set_v();assert(http.server.status==200);
    }
    for(const char *text:{"0", "6", " 0.05 ", "5e-2"}) {
        http.server.plain=text;http.handle_set_i();assert(http.server.status==200);
    }
    assert(regs[8]==2600 && regs[9]==500);
    std::cout<<"PASS: valid web zero, bounds, fractions and decimal exponent still work\n";
    http.server.plain="1.001";http.handle_set_v();
    assert(http.server.status==200 && regs[8]==1001);
    http.server.plain="0.0003";http.handle_set_i();
    assert(http.server.status==200 && regs[9]==3);
    http.server.plain="0.0501";http.handle_set_i();
    assert(http.server.status==200 && regs[9]==501);
    assert(bus.set_voltage_set(2.6) && bus.set_current_set(0.05));
    std::cout<<"PASS: HTTP writes preserve RD6006P millivolts and 0.1 mA without losing one register step\n";
    for(const char *text:{"0", "1", "2", "-1", "abc", ""}) {
        const auto before=Serial.requests.size();http.server.plain=text;http.handle_set_i_range();
        assert(http.server.status==400 && Serial.requests.size()==before);
    }
    std::cout<<"PASS: unsupported or malformed current-range commands never reach UART\n";
    regs[18]=0;

    reset_faults(http);failing_reg=20;read_failures=1;
    const auto retried=bus.get_diagnostics().local_read_retries;
    http.handle_status_get();
    assert(http.server.status==200 && reads[0]==1 && reads[20]==2 && reads[40]==1);
    assert(bus.get_diagnostics().local_read_retries==retried+1);
    assert(http.server.body.find("\"out_on\": false")!=String::npos);
    std::cout<<"PASS: web status retries only its failed register block\n";

    reset_faults(http);failing_reg=0;corrupt_read=true;
    http.handle_status_get();assert(http.server.status==200 && reads[0]==2);
    std::cout<<"PASS: CRC-bad web status frame recovers with a fresh read\n";

    reset_faults(http);failing_reg=0;read_failures=2;
    http.handle_status_get();assert(http.server.status==500 && reads[0]==2 && reads[20]==0);
    assert(web_diagnostics().status_read_failures==1);
    assert(http.server.body=="Failed to read power supply registers");
    std::cout<<"PASS: two failed web reads return an error instead of fabricated data\n";

    for(unsigned initial=0;initial<=1;++initial) {
        reset_faults(http);regs[18]=initial;failing_reg=18;read_failures=2;
        const auto old_writes=writes;
        http.handle_toggle_out();
        assert(http.server.status==500 && regs[18]==initial && writes==old_writes);
        assert(web_diagnostics().toggle_state_read_failures==1);
    }
    std::cout<<"PASS: unreadable output never authorizes a web toggle from ON or OFF\n";

    for(unsigned initial=0;initial<=1;++initial) {
        reset_faults(http);regs[18]=initial;const auto old_writes=writes;
        http.handle_toggle_out();
        assert(http.server.status==200 && regs[18]==1-initial && writes==old_writes+1);
        assert(web_diagnostics().toggle_requests==1 && web_diagnostics().toggle_write_ack_failures==0);
    }
    std::cout<<"PASS: normal web ON and OFF each send one absolute output write\n";

    for(unsigned initial=0;initial<=1;++initial) {
        reset_faults(http);regs[18]=initial;corrupt_ack=true;const auto old_writes=writes;
        http.handle_toggle_out();
        assert(http.server.status==200 && regs[18]==1-initial && writes==old_writes+1);
        assert(web_diagnostics().toggle_write_ack_failures==1);
        assert(web_diagnostics().toggle_reconciled_writes==1);
        assert(web_diagnostics().toggle_unconfirmed_writes==0);
    }
    std::cout<<"PASS: applied web ON/OFF with a CRC-bad ACK is reconciled without another write\n";

    reset_faults(http);regs[18]=0;drop_ack=true;const auto old_writes=writes;
    http.handle_toggle_out();assert(http.server.status==200 && regs[18]==1 && writes==old_writes+1);
    assert(web_diagnostics().toggle_reconciled_writes==1);
    std::cout<<"PASS: missing web write ACK is reconciled by reading the output\n";

    reset_faults(http);regs[18]=0;apply_write=false;drop_ack=true;
    const auto before_unapplied=writes;
    http.handle_toggle_out();assert(http.server.status==500 && regs[18]==0 && writes==before_unapplied+1);
    assert(web_diagnostics().toggle_unconfirmed_writes==1 && web_diagnostics().toggle_reconciled_writes==0);
    std::cout<<"PASS: an unapplied command is not reported as successful or automatically repeated\n";

    reset_faults(http);regs[18]=0;drop_ack=true;fail_reconcile=true;
    const auto before_unknown=writes;
    http.handle_toggle_out();assert(http.server.status==500 && regs[18]==1 && writes==before_unknown+1);
    assert(web_diagnostics().toggle_unconfirmed_writes==1 && reads[18]==3);
    assert(http.server.body.find("verify output before retrying")!=String::npos);
    std::cout<<"PASS: unreadable write outcome stays explicit and never sends a second write\n";

    reset_faults(http);regs[18]=0;fail_after_write=true;
    http.handle_toggle_out();assert(http.server.status==500 && regs[18]==1);
    assert(http.server.body.find("Output command completed, but status read failed")!=String::npos);
    std::cout<<"PASS: telemetry failure after a completed command is reported distinctly\n";

    reset_faults(http);regs[18]=1;exception_code=2;const auto before_exception=writes;
    http.handle_toggle_out();assert(http.server.status==500 && writes==before_exception && reads[18]==1);
    std::cout<<"PASS: a valid PSU exception is not retried and cannot toggle the output\n";

    RidenModbusBridge bridge(bus);assert(bridge.begin());
    auto &tcp=*ModbusTCP::instance;
    auto &tc=tcp.addClient(100);auto &tc2=tcp.addClient(100);auto &ha=tcp.addClient(135);
    for(unsigned i=0;i<100;++i) {
        reset_faults(http);
        tcp.enqueueClient(tc,i,{3,0,8,0,1});
        tcp.enqueueClient(tc2,i,{3,0,10,0,1});
        tcp.enqueueClient(ha,i,{3,0,9,0,1});bridge.loop();
        if(i%7==0) {failing_reg=20;read_failures=1;}
        if(i%11==0) corrupt_ack=true;
        const auto before=writes;
        http.handle_toggle_out();assert(http.server.status==200 && writes==before+1);
        assert(tc.packets.back()[1]==uint8_t(i) && tc2.packets.back()[1]==uint8_t(i));
        assert(tc.packets.back().back()==0x28 && tc2.packets.back().back()==0x27);
        assert(ha.packets.back().back()==0xf4);
    }
    std::cout<<"PASS: 100 cycles of real web toggles plus two same-IP TCs and HA, with read/ACK faults\n";
    assert((web_diagnostics().toggle_time.count>0)==detailed_diagnostics_enabled());
    std::cout<<"PASS: output control and recovery work with optional tracing "
             <<(detailed_diagnostics_enabled() ? "on" : "off")<<"\n";
}
