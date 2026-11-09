#include <riden_modbus_bridge/riden_modbus_bridge.h>
#include <riden_logging/clock_sync_state.h>
#include <riden_logging/web_diagnostics.h>
#include <cassert>
#include <cstdio>
#include <iostream>

uint64_t host_time_us=10000;
HostSerial Serial;
static bool drop=false;

struct HostESP {
    uint32_t getFreeHeap() { return 25000; }
    uint32_t getMaxFreeBlockSize() { return 18000; }
    uint32_t getCpuFreqMHz() { return 80; }
} ESP;
struct HostWiFi { int RSSI() { return -55; } } WiFi;

namespace RidenDongle {
const char *version_string="1.7.0-taxom.5";
class RidenHttpServer {
  public:
    RidenModbus &modbus;
    RidenModbusBridge &bridge;
    TimingStats http_poll_time, status_time, diagnostics_time;
    struct Http {
        String body;
        int status=0;
        void sendHeader(const char *, const char *) {}
        void send(int code, const char *, const String &content) {
            status=code;body=content;
            delay(120); // model network send wait for the HTTP timer
        }
    } server;
    RidenHttpServer(RidenModbus &bus, RidenModbusBridge &bridge) : modbus(bus), bridge(bridge) {}
    void handle_modbus_diagnostics();
};
}
using namespace RidenDongle;

// PRODUCTION_HANDLER

static void reply(const std::vector<uint8_t> &) {
    if(drop) { drop=false;return; }
    std::vector<uint8_t> bytes={1,3,2,0xea,0xa1};
    uint16_t crc=0xffff;
    for(uint8_t byte:bytes) {
        crc ^= byte;
        for(unsigned i=0;i<8;++i) crc=crc&1 ? (crc>>1)^0xa001 : crc>>1;
    }
    bytes.push_back(crc);bytes.push_back(crc>>8);Serial.deliver(std::move(bytes));
}

int main() {
    Serial.on_request=reply;
    static RidenModbus bus;assert(bus.begin());
    RidenModbusBridge bridge(bus);assert(bridge.begin());
    auto &tcp=*ModbusTCP::instance;tcp.addClient(100);
    for(unsigned i=0;i<10;++i) {
        drop=true;tcp.enqueue(100,i,{3,0,0,0,1});bridge.loop();
    }
    RidenHttpServer http(bus,bridge);
    const auto requests=Serial.requests.size();
    http.handle_modbus_diagnostics();
    assert(http.server.status==200 && Serial.requests.size()==requests);
    assert(http.diagnostics_time.max_us==120000);
    http.handle_modbus_diagnostics();
    assert(Serial.requests.size()==requests);
    std::cout<<http.server.body<<"\n";
}
