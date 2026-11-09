#include <Arduino.h>
#include <riden_config/riden_config.h>
#include <riden_logging/diagnostics_state.h>
#include <riden_logging/riden_logging.h>
#include <EEPROM.h>
#include "http_static.h"
#include <cassert>
#include <iostream>
#include <map>
#include <list>

uint64_t host_time_us=10000;
HostSerial Serial;
static constexpr size_t CONTENT_LENGTH_UNKNOWN=size_t(-1);
static const std::list<uint32_t> uart_baudrates={9600,115200};

namespace RidenDongle {
class RidenHttpServer {
  public:
    struct Http {
        std::map<std::string,String> args;
        String body;
        int status=0;
        unsigned redirects=0;
        void setContentLength(size_t) {}
        void send(int code,const char *,const String &content) { status=code;body=content; }
        void sendContent(const String &content) { body+=content; }
        void sendContent_P(const char *content) { body+=content; }
        String arg(const char *key) { return args[key]; }
    } server;
    void handle_config_get();
    void handle_config_post();
    void send_redirect_self() { ++server.redirects;server.status=303; }
};
}
using namespace RidenDongle;

// PRODUCTION_CONFIG_HANDLERS

struct LegacyHeader { char magic[3];uint8_t version; };
struct LegacyV1 { LegacyHeader header;char timezone[100];bool portal; };
struct LegacyV2 { LegacyHeader header;char timezone[100];bool portal;uint32_t baud; };

static void legacy_v2() {
    EEPROM=HostEEPROM{};
    LegacyV2 old{};
    std::memcpy(old.header.magic,"RD",3);old.header.version=2;
    std::strcpy(old.timezone,"America/Toronto");old.baud=115200;
    EEPROM.put(0,old);assert(EEPROM.commit());
    riden_config=RidenConfig{};assert(riden_config.begin());
}
static void post(RidenHttpServer &http,bool enabled) {
    http.server.args={{"timezone","America/Toronto"},{"uart_baudrate","115200"}};
    if(enabled) http.server.args["diagnostics_enabled"]="1";
    http.handle_config_post();
}

int main() {
    assert(!detailed_diagnostics_enabled());
    riden_config=RidenConfig{};assert(riden_config.begin());
    assert(!riden_config.get_diagnostics_enabled() && EEPROM.commits==1);
    assert(riden_config.get_uart_baudrate()==9600);
    std::cout<<"PASS: fresh EEPROM defaults to diagnostics off\n";

    legacy_v2();
    assert(!detailed_diagnostics_enabled() && riden_config.get_uart_baudrate()==115200);
    assert(riden_config.get_timezone_name()=="America/Toronto");
    assert(EEPROM.commits==1);
    std::cout<<"PASS: taxom.1-7 configuration keeps baudrate/timezone and defaults tracing off\n";

    RidenHttpServer http;http.handle_config_get();
    assert(http.server.status==200);
    assert(http.server.body.find("name='diagnostics_enabled' value='1'>")!=String::npos);
    post(http,true);assert(http.server.status==303 && detailed_diagnostics_enabled());
    LegacyV2 saved{};EEPROM.get(0,saved);
    assert(saved.header.version==2 && saved.baud==115200 && String(saved.timezone)=="America/Toronto");
    riden_config=RidenConfig{};assert(riden_config.begin() && detailed_diagnostics_enabled());
    http.handle_config_get();
    assert(http.server.body.find("name='diagnostics_enabled' value='1' checked>")!=String::npos);
    std::cout<<"PASS: Configure checkbox enables immediately and persists after reboot\n";

    post(http,false);assert(!detailed_diagnostics_enabled());
    riden_config=RidenConfig{};assert(riden_config.begin() && !detailed_diagnostics_enabled());
    http.handle_config_get();
    assert(http.server.body.find("name='diagnostics_enabled' value='1'>")!=String::npos);
    std::cout<<"PASS: unchecked Configure checkbox disables immediately and persists\n";

    EEPROM.commit_ok=false;
    const auto redirects=http.server.redirects;
    post(http,true);
    assert(http.server.status==500 && !detailed_diagnostics_enabled());
    assert(http.server.redirects==redirects);
    EEPROM.commit_ok=true;
    riden_config=RidenConfig{};assert(riden_config.begin() && !detailed_diagnostics_enabled());
    std::cout<<"PASS: failed EEPROM commit reports failure and restores the active setting\n";

    post(http,true);
    EEPROM.get(0,saved);saved.baud=57600;std::strcpy(saved.timezone,"Europe/Berlin");
    EEPROM.put(0,saved);assert(EEPROM.commit());
    riden_config=RidenConfig{};assert(riden_config.begin());
    assert(detailed_diagnostics_enabled() && riden_config.get_uart_baudrate()==57600);
    assert(riden_config.get_timezone_name()=="Europe/Berlin");
    std::cout<<"PASS: config stays V2-readable across firmware downgrade and re-upgrade\n";

    EEPROM.working[132]=2;assert(EEPROM.commit());
    riden_config=RidenConfig{};assert(riden_config.begin() && !detailed_diagnostics_enabled());
    std::cout<<"PASS: invalid optional configuration is treated as diagnostics off\n";

    EEPROM=HostEEPROM{};LegacyV1 v1{};
    std::memcpy(v1.header.magic,"RD",3);v1.header.version=1;
    std::strcpy(v1.timezone,"America/Toronto");
    EEPROM.put(0,v1);assert(EEPROM.commit());
    riden_config=RidenConfig{};assert(riden_config.begin());
    assert(!detailed_diagnostics_enabled() && riden_config.get_uart_baudrate()==9600);
    assert(riden_config.get_timezone_name()=="America/Toronto");
    std::cout<<"PASS: V1 EEPROM settings remain readable\n";
}
