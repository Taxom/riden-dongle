#include <riden_modbus/riden_modbus.h>
#include <riden_logging/web_diagnostics.h>
#include <cassert>
#include <iostream>

uint64_t host_time_us=10000;
HostSerial Serial;
static uint16_t regs[128]={};
static unsigned writes=0;

namespace RidenDongle {
class RidenHttpServer {
  public:
    RidenModbus &modbus;
    TimingStats status_time;
    struct Http {
        String body;int status=0;
        void send(int code,const char *,const String &text) { status=code;body=text; }
        void sendContent(const char *) {}
    } server;
    explicit RidenHttpServer(RidenModbus &bus) : modbus(bus) {}
    void handle_status_get();
    bool send_status(const char *failure_message);
};
}
using namespace RidenDongle;

// PRODUCTION_DISPLAY_FUNCTIONS

static std::vector<uint8_t> frame(std::vector<uint8_t> data) {
    uint16_t crc=0xffff;
    for(const auto byte:data) {
        crc^=byte;
        for(unsigned i=0;i<8;++i) crc=crc&1 ? (crc>>1)^0xa001 : crc>>1;
    }
    data.push_back(crc);data.push_back(crc>>8);return data;
}
static void emulate(const std::vector<uint8_t> &request) {
    const unsigned reg=(unsigned(request[2])<<8)|request[3];
    const unsigned value=(unsigned(request[4])<<8)|request[5];
    std::vector<uint8_t> response={request[0],request[1]};
    if(request[1]==3) {
        response.push_back(value*2);
        for(unsigned i=0;i<value;++i) {response.push_back(regs[reg+i]>>8);response.push_back(regs[reg+i]);}
    } else {
        assert(request[1]==6 && reg<128);
        regs[reg]=value;++writes;
        response.insert(response.end(),request.begin()+2,request.begin()+6);
    }
    Serial.deliver(frame(std::move(response)));
}

int main(int argc,char **argv) {
    assert(argc==5);
    const unsigned model=std::stoul(argv[1]);
    const unsigned range=std::stoul(argv[2]);
    const unsigned voltage_digits=std::stoul(argv[3]),current_digits=std::stoul(argv[4]);
    regs[0]=model;regs[8]=2601;regs[9]=501;regs[10]=2603;regs[11]=503;regs[14]=6578;
    regs[20]=range;regs[33]=3001;
    Serial.on_request=emulate;
    RidenModbus bus;assert(bus.begin());RidenHttpServer http(bus);
    http.handle_status_get();
    assert(http.server.status==200 && writes==0);
    assert(bus.get_voltage_precision()==voltage_digits && bus.get_current_precision()==current_digits);
    assert(bus.get_precision()==voltage_digits);
    std::cout<<http.server.body<<'\n';

    if(model==60065) {
        assert(voltage_to_string(0.123,3)=="123 mV");
        assert(current_to_string(0.0501,4)=="50.1 mA");
        assert(current_to_string(1.2345,4)=="1.2345 A");
        // Every representable RD6006P setpoint must survive a decimal readback
        // followed by a write. Truncating double * divisor loses one LSB for
        // e.g. 1.001 V and 0.0003 A even though both are exactly valid inputs.
        for(unsigned raw=0;raw<=60100;++raw) {
            assert(bus.set_voltage_set(double(raw)/1000.0) && regs[8]==raw);
            Serial.requests.clear();
        }
        for(unsigned raw=0;raw<=60000;++raw) {
            assert(bus.set_current_set(double(raw)/10000.0) && regs[9]==raw);
            Serial.requests.clear();
        }
    }
    if(model==60125) {
        // The next status must update the current precision when another client
        // or the front panel switches ranges; no model-only precision cache.
        regs[20]=1-range;http.handle_status_get();
        assert(bus.get_current_precision()==(range ? 4 : 3));
        std::cout<<http.server.body<<'\n';
    }
}
